# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Receipt-honesty persistent-debt ratchet for OCC (OMN-17495).

The locked :mod:`omnibase_core.validation.validator_receipt_honesty` module is
the only source of honesty-rule semantics.  It deliberately scans every
parseable ``*.yaml`` receipt, including superseded receipt bases.  That is an
important limitation: this first control is a truthful persistent-debt
migration guard, not a claimed burn-down of active receipt debt.  Making the
core scanner supersession-aware is follow-up work; this wrapper must never
reinterpret, filter, or suppress its findings.

The immutable ledger records each historical core finding as the tuple
``(canonical repo path, EnumHonestyRule value, SHA-256(raw bytes), origin blob
OID)``.  A full scan passes only when the live set equals the ledger set.  A
byte edit, path copy, rule addition, repair without ledger removal, or ledger
entry removal without a real repair consequently fails as both a new and/or a
stale identity.  The ledger is audit metadata, never a suppression allowlist.

``--seed-baseline`` is intentionally one-shot.  It may write a missing ledger
only from the controlled bootstrap base and only after independently proving
the frozen provenance census and live corpus census.  Afterwards the current
ledger must be a subset of the base commit's ledger; debt can disappear only
along with the corresponding live core finding.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
from omnibase_core.validation.validator_receipt_honesty import (
    EnumHonestyRule,
    scan_receipt_files,
    scan_receipts_directory,
)

_TICKET = "OMN-17495"
_MODULE = "onex_change_control.validation.receipt_honesty_ratchet"
_BASELINE_RELPATH = PurePosixPath(
    ".onex_ratchets/omn_17495_receipt_honesty_baseline.yaml"
)
_CONTRACT_RELPATH = PurePosixPath("contracts/OMN-17495.yaml")
_RECEIPTS_RELPATH = PurePosixPath("drift/dod_receipts")
_ORIGIN_COMMIT = "65a2adbba8a3c4f6cc57c1c7250480bfb708fac0"
_BOOTSTRAP_BASE_COMMIT = "b2293819e69a3bf4b58107bd2b951f3a45cc377f"
_SEED_FINDING_COUNT = 1142
_SEED_RECEIPT_PATH_COUNT = 1055
_SCANNER_LIMITATION = (
    "The locked core scanner includes superseded receipt bases. This ledger is "
    "a truthful persistent-debt migration control, not a burn-down claim; "
    "active-supersession scanner work is follow-up."
)
_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "origin_commit",
        "bootstrap_base_commit",
        "seed_finding_count",
        "seed_receipt_path_count",
        "scanner_limitation",
        "findings",
    }
)
_ENTRY_KEYS = frozenset({"path", "rule", "sha256", "blob_oid"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OID_RE = re.compile(r"^[0-9a-f]{40,64}$")
_GIT_EXECUTABLE = shutil.which("git")


class RatchetError(RuntimeError):
    """A fail-closed receipt-honesty ratchet violation."""


class _StrictYamlLoader(yaml.SafeLoader):
    """Safe loader which treats duplicate and merged keys as malformed."""


def _construct_unique_mapping(
    loader: _StrictYamlLoader, node: yaml.MappingNode, deep: Any = None
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    construct_object = cast("Callable[[yaml.Node, bool], Any]", loader.construct_object)
    deep_value = bool(deep)
    for key_node, value_node in node.value:
        key = construct_object(key_node, deep_value)
        if key == "<<":
            msg = "YAML merge keys are forbidden in the strict ratchet ledger"
            raise RatchetError(msg)
        if key in mapping:
            msg = f"duplicate YAML key: {key!r}"
            raise RatchetError(msg)
        mapping[key] = construct_object(value_node, deep_value)
    return mapping


_StrictYamlLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


@dataclass(frozen=True, order=True)
class FindingIdentity:
    """A content-bound core finding; detail text is intentionally not identity."""

    path: str
    rule: str
    sha256: str
    blob_oid: str

    def as_mapping(self) -> dict[str, str]:
        """Serialize in the ledger's canonical field order."""
        return {
            "path": self.path,
            "rule": self.rule,
            "sha256": self.sha256,
            "blob_oid": self.blob_oid,
        }


@dataclass(frozen=True)
class Baseline:
    """The strictly parsed receipt-honesty persistent-debt ledger."""

    findings: tuple[FindingIdentity, ...]

    @property
    def identities(self) -> frozenset[FindingIdentity]:
        """The set form used for live and base comparisons."""
        return frozenset(self.findings)


def _repo_root(value: Path) -> Path:
    root = value.resolve()
    if not (root / ".git").exists() and not (root / ".git").is_file():
        msg = f"repository root is not a Git worktree: {root}"
        raise RatchetError(msg)
    return root


def _git(repo_root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    """Run Git without a shell; errors are gate failures, never fallbacks."""
    if _GIT_EXECUTABLE is None:
        msg = "Git executable is unavailable for receipt-honesty provenance validation"
        raise RatchetError(msg)
    result = subprocess.run(  # noqa: S603 -- arguments are fixed Git plumbing
        [_GIT_EXECUTABLE, *args],
        cwd=repo_root,
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        msg = f"git {' '.join(args)} failed"
        if detail:
            msg = f"{msg}: {detail}"
        raise RatchetError(msg)
    return result.stdout


def _commit_exists(repo_root: Path, commit: str) -> bool:
    if _GIT_EXECUTABLE is None:
        return False
    result = subprocess.run(  # noqa: S603 -- arguments are fixed Git plumbing
        [_GIT_EXECUTABLE, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _head_commit(repo_root: Path) -> str:
    return _git(repo_root, "rev-parse", "HEAD").decode("ascii").strip()


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    if _GIT_EXECUTABLE is None:
        return False
    result = subprocess.run(  # noqa: S603 -- arguments are fixed Git plumbing
        [_GIT_EXECUTABLE, "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _normalize_base_commit(repo_root: Path, candidate: str) -> str:
    """Require an exact commit OID which is an ancestor of current HEAD.

    ``git rev-parse <name>^{commit}`` alone is insufficient: it accepts tags
    and branch-like names, and a resolved commit can still be non-linear after
    a force push.  The ledger comparison is meaningful only for an exact base
    commit in HEAD's history, so reject both cases before any base-tree read.
    """
    object_format = _git_object_format(repo_root)
    expected_length = 40 if object_format == "sha1" else 64
    if not re.fullmatch(rf"[0-9a-f]{{{expected_length}}}", candidate):
        msg = (
            "receipt-honesty base must be a full canonical "
            f"{object_format} commit OID, not a ref, tag, or abbreviation: "
            f"{candidate!r}"
        )
        raise RatchetError(msg)
    normalized = (
        _git(repo_root, "rev-parse", "--verify", f"{candidate}^{{commit}}")
        .decode("ascii")
        .strip()
    )
    if normalized != candidate:
        msg = (
            "receipt-honesty base did not normalize to its exact commit OID: "
            f"{candidate!r}"
        )
        raise RatchetError(msg)
    head = _head_commit(repo_root)
    if not _is_ancestor(repo_root, normalized, head):
        msg = (
            "receipt-honesty base is not an ancestor of HEAD; refusing a "
            f"force-push or non-linear comparison: base={normalized}, head={head}"
        )
        raise RatchetError(msg)
    return normalized


def _normalize_branch_ref(repo_root: Path, value: str, event_name: str) -> str:
    """Normalize only a ``refs/heads/`` prefix and validate an exact branch name."""
    branch = value.removeprefix("refs/heads/")
    if not branch:
        msg = f"{event_name} receipt-honesty base branch is missing"
        raise RatchetError(msg)
    _git(repo_root, "check-ref-format", "--branch", branch)
    return branch


def _merge_base_for_branch(repo_root: Path, branch: str) -> str:
    """Fetch one exact remote branch and return its ancestor-verified merge base."""
    _git(repo_root, "fetch", "origin", branch, "--no-tags")
    base = (
        _git(repo_root, "merge-base", f"origin/{branch}", "HEAD")
        .decode("ascii")
        .strip()
    )
    return _normalize_base_commit(repo_root, base)


def _resolve_local_tracking_ref(repo_root: Path) -> str:
    """Resolve only the repository default branch, without fetching or guessing.

    A feature branch can track itself after its first push, so ``@{upstream}``
    is not authoritative for corpus monotonicity.  Only origin/HEAD is accepted.
    """
    if _GIT_EXECUTABLE is None:
        msg = "Git executable is unavailable for local receipt-honesty base resolution"
        raise RatchetError(msg)
    candidate = "refs/remotes/origin/HEAD"
    result = subprocess.run(  # noqa: S603 -- fixed Git plumbing
        [
            _GIT_EXECUTABLE,
            "rev-parse",
            "--verify",
            "--quiet",
            f"{candidate}^{{commit}}",
        ],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return candidate
    if result.returncode != 1:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        msg = f"unable to resolve local receipt-honesty default ref {candidate}"
        if detail:
            msg = f"{msg}: {detail}"
        raise RatchetError(msg)
    msg = "local receipt-honesty base requires refs/remotes/origin/HEAD"
    raise RatchetError(msg)


def resolve_local_base(repo_root: Path) -> str:
    """Resolve an offline, ancestor-verified local corpus base without fetching."""
    ref = _resolve_local_tracking_ref(repo_root)
    base = _git(repo_root, "merge-base", ref, "HEAD").decode("ascii").strip()
    return _normalize_base_commit(repo_root, base)


@dataclass(frozen=True)
class CiBaseRequest:
    """The event fields needed to derive one CI monotonicity base."""

    event_name: str
    pr_base_ref: str
    merge_group_base_ref: str
    before_sha: str
    default_branch: str


def resolve_ci_base(repo_root: Path, request: CiBaseRequest) -> str:
    """Resolve an event-correct, ancestor-verified CI monotonicity base.

    Pull requests and merge groups may only use their event payload branch;
    ordinary pushes use their exact before commit. New branches, zero-before
    force-push sentinels, and manual dispatch require an explicit repository
    default branch supplied by CI's authenticated API lookup. There is no
    branch-name fallback.
    """
    if request.event_name == "pull_request":
        branch = _normalize_branch_ref(
            repo_root, request.pr_base_ref, request.event_name
        )
        return _merge_base_for_branch(repo_root, branch)
    if request.event_name == "merge_group":
        branch = _normalize_branch_ref(
            repo_root, request.merge_group_base_ref, request.event_name
        )
        return _merge_base_for_branch(repo_root, branch)
    if request.before_sha and request.before_sha != "0" * 40:
        return _normalize_base_commit(repo_root, request.before_sha)
    branch = _normalize_branch_ref(
        repo_root, request.default_branch, request.event_name
    )
    return _merge_base_for_branch(repo_root, branch)


def _git_object_format(repo_root: Path) -> str:
    value = _git(repo_root, "rev-parse", "--show-object-format").decode("ascii").strip()
    if value not in {"sha1", "sha256"}:
        msg = f"unsupported Git object format for receipt ledger: {value!r}"
        raise RatchetError(msg)
    return value


def _git_blob_oid(raw: bytes, object_format: str) -> str:
    digest = hashlib.new(object_format)
    digest.update(f"blob {len(raw)}\0".encode("ascii"))
    digest.update(raw)
    return digest.hexdigest()


def _canonical_receipt_path(value: str) -> str:
    """Reject non-canonical, non-receipt, and traversal ledger paths."""
    if not isinstance(value, str) or not value:
        msg = "ledger finding path must be a non-empty string"
        raise RatchetError(msg)
    if "\\" in value:
        msg = f"ledger finding path must use canonical POSIX separators: {value!r}"
        raise RatchetError(msg)
    path = PurePosixPath(value)
    if value != path.as_posix():
        msg = f"ledger finding path is not exact canonical POSIX spelling: {value!r}"
        raise RatchetError(msg)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        msg = (
            f"ledger finding path is not canonical or traverses directories: {value!r}"
        )
        raise RatchetError(msg)
    if not path.is_relative_to(_RECEIPTS_RELPATH) or path.suffix != ".yaml":
        msg = f"ledger finding path is outside canonical receipt corpus: {value!r}"
        raise RatchetError(msg)
    return path.as_posix()


def _read_yaml_document(raw: bytes, source: str) -> Any:
    try:
        documents = list(yaml.load_all(raw.decode("utf-8"), Loader=_StrictYamlLoader))
    except (UnicodeDecodeError, yaml.YAMLError, RatchetError) as exc:
        msg = f"malformed receipt-honesty baseline {source}: {exc}"
        raise RatchetError(msg) from exc
    if len(documents) != 1:
        msg = (
            f"malformed receipt-honesty baseline {source}: exactly one YAML "
            "document is required"
        )
        raise RatchetError(msg)
    return documents[0]


def _validate_baseline_metadata(document: dict[str, Any], source: str) -> list[Any]:
    """Validate exact root keys and immutable bootstrap metadata."""
    if not isinstance(document, dict) or set(document) != _ROOT_KEYS:
        msg = (
            f"malformed receipt-honesty baseline {source}: root keys must be exactly "
            f"{sorted(_ROOT_KEYS)!r}"
        )
        raise RatchetError(msg)
    expected_scalars: dict[str, object] = {
        "schema_version": 1,
        "origin_commit": _ORIGIN_COMMIT,
        "bootstrap_base_commit": _BOOTSTRAP_BASE_COMMIT,
        "seed_finding_count": _SEED_FINDING_COUNT,
        "seed_receipt_path_count": _SEED_RECEIPT_PATH_COUNT,
        "scanner_limitation": _SCANNER_LIMITATION,
    }
    for key, expected in expected_scalars.items():
        if document[key] != expected or type(document[key]) is not type(expected):
            msg = (
                f"malformed receipt-honesty baseline {source}: {key!r} must equal "
                f"the immutable {expected!r}"
            )
            raise RatchetError(msg)
    raw_findings = document["findings"]
    if not isinstance(raw_findings, list):
        msg = f"malformed receipt-honesty baseline {source}: findings must be a list"
        raise RatchetError(msg)
    return raw_findings


def _parse_finding(raw_entry: Any, source: str, index: int) -> FindingIdentity:
    """Parse one exactly shaped ledger identity."""
    if not isinstance(raw_entry, dict) or set(raw_entry) != _ENTRY_KEYS:
        msg = (
            f"malformed receipt-honesty baseline {source}: finding {index} keys "
            f"must be exactly {sorted(_ENTRY_KEYS)!r}"
        )
        raise RatchetError(msg)
    path = _canonical_receipt_path(raw_entry["path"])
    rule = raw_entry["rule"]
    sha256 = raw_entry["sha256"]
    blob_oid = raw_entry["blob_oid"]
    if not isinstance(rule, str) or rule not in {
        item.value for item in EnumHonestyRule
    }:
        msg = (
            f"malformed receipt-honesty baseline {source}: invalid rule at "
            f"finding {index}"
        )
        raise RatchetError(msg)
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        msg = (
            f"malformed receipt-honesty baseline {source}: invalid SHA-256 at "
            f"finding {index}"
        )
        raise RatchetError(msg)
    if not isinstance(blob_oid, str) or not _OID_RE.fullmatch(blob_oid):
        msg = (
            f"malformed receipt-honesty baseline {source}: invalid blob OID at "
            f"finding {index}"
        )
        raise RatchetError(msg)
    return FindingIdentity(path, rule, sha256, blob_oid)


def parse_baseline(raw: bytes, source: str = "baseline") -> Baseline:
    """Load a fail-closed strict schema; no unknown fields or duplicate identities."""
    document = _read_yaml_document(raw, source)
    if not isinstance(document, dict):
        msg = f"malformed receipt-honesty baseline {source}: root is not a mapping"
        raise RatchetError(msg)
    raw_findings = _validate_baseline_metadata(document, source)

    findings: list[FindingIdentity] = []
    seen_pairs: set[tuple[str, str]] = set()
    for index, raw_entry in enumerate(raw_findings):
        finding = _parse_finding(raw_entry, source, index)
        pair = (finding.path, finding.rule)
        if pair in seen_pairs:
            msg = (
                f"malformed receipt-honesty baseline {source}: duplicate path/rule "
                f"identity {pair!r}"
            )
            raise RatchetError(msg)
        seen_pairs.add(pair)
        findings.append(finding)

    if findings != sorted(findings):
        msg = (
            f"malformed receipt-honesty baseline {source}: findings are not "
            "deterministic-sorted"
        )
        raise RatchetError(msg)
    if len(set(findings)) != len(findings):
        msg = f"malformed receipt-honesty baseline {source}: duplicate finding identity"
        raise RatchetError(msg)
    return Baseline(tuple(findings))


def _baseline_path(repo_root: Path) -> Path:
    return repo_root / Path(_BASELINE_RELPATH)


def _read_regular_worktree_file(
    repo_root: Path, path: PurePosixPath, label: str
) -> bytes:
    candidate = repo_root / Path(path)
    try:
        mode = candidate.lstat().st_mode
    except OSError as exc:
        msg = f"{label} is missing or unreadable: {candidate}"
        raise RatchetError(msg) from exc
    if not stat.S_ISREG(mode) or mode & 0o111:
        msg = f"{label} must be a regular non-symlink file: {candidate}"
        raise RatchetError(msg)
    try:
        return candidate.read_bytes()
    except OSError as exc:
        msg = f"{label} is missing or unreadable: {candidate}"
        raise RatchetError(msg) from exc


def load_baseline(repo_root: Path) -> Baseline:
    """Read the working-tree ledger; absence or unreadability always fails closed."""
    raw = _read_regular_worktree_file(
        repo_root, _BASELINE_RELPATH, "receipt-honesty baseline"
    )
    return parse_baseline(raw, str(_baseline_path(repo_root)))


def _read_regular_index_file(repo_root: Path, path: PurePosixPath, label: str) -> bytes:
    """Read one exact stage-0 regular blob; no working-tree fallback is allowed."""
    return _read_regular_index_files(repo_root, (path,), label)[path]


def _read_regular_index_files(  # noqa: C901 -- one fail-closed batch parser
    repo_root: Path, paths: tuple[PurePosixPath, ...], label: str
) -> dict[PurePosixPath, bytes]:
    """Batch-read exact stage-0 100644 blobs with NUL-safe Git plumbing."""
    if not paths:
        return {}
    expected = tuple(sorted(set(paths)))
    raw_entries = _git(
        repo_root,
        "ls-files",
        "--stage",
        "-z",
        "--",
        *(path.as_posix() for path in expected),
    )
    entries = [entry for entry in raw_entries.split(b"\0") if entry]
    index_oids: dict[PurePosixPath, str] = {}
    for entry in entries:
        try:
            metadata, raw_path = entry.split(b"\t", 1)
            mode, oid, stage = metadata.split(b" ", 2)
            indexed_path = PurePosixPath(raw_path.decode("utf-8"))
            decoded_oid = oid.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            msg = f"{label} has an unreadable staged index entry"
            raise RatchetError(msg) from exc
        if (
            indexed_path not in expected
            or mode != b"100644"
            or stage != b"0"
            or not _OID_RE.fullmatch(decoded_oid)
            or indexed_path in index_oids
        ):
            msg = f"{label} must have one stage-0 regular index entry: {indexed_path}"
            raise RatchetError(msg)
        index_oids[indexed_path] = decoded_oid
    if set(index_oids) != set(expected):
        missing = sorted(path.as_posix() for path in set(expected) - set(index_oids))
        msg = f"{label} is missing staged index entries: {', '.join(missing[:5])}"
        raise RatchetError(msg)

    # Feed object IDs, not ``:<path>`` revision expressions: cat-file's batch
    # protocol is newline-framed, while Git paths may legally contain newlines.
    request = b"".join(f"{index_oids[path]}\n".encode() for path in expected)
    raw_blobs = _git(repo_root, "cat-file", "--batch", input_bytes=request)
    cursor = 0
    blobs: dict[PurePosixPath, bytes] = {}
    for path in expected:
        newline = raw_blobs.find(b"\n", cursor)
        if newline < 0:
            msg = f"{label} has truncated staged blob data: {path}"
            raise RatchetError(msg)
        header = raw_blobs[cursor:newline].split()
        cursor = newline + 1
        try:
            oid, object_type, raw_size = header
            size = int(raw_size)
        except ValueError as exc:
            msg = f"{label} has malformed staged blob data: {path}"
            raise RatchetError(msg) from exc
        if (
            oid.decode("ascii", errors="strict") != index_oids[path]
            or object_type != b"blob"
            or size < 0
            or cursor + size >= len(raw_blobs)
            or raw_blobs[cursor + size : cursor + size + 1] != b"\n"
        ):
            msg = f"{label} has invalid staged blob data: {path}"
            raise RatchetError(msg)
        blobs[path] = raw_blobs[cursor : cursor + size]
        cursor += size + 1
    if cursor != len(raw_blobs):
        msg = f"{label} has trailing staged blob data"
        raise RatchetError(msg)
    return blobs


def _validate_receipt_file(candidate: Path, receipts_dir: Path) -> None:
    """Reject links, executable files, FIFOs, and devices before the core opens them."""
    try:
        relative = candidate.relative_to(receipts_dir)
        canonical = _canonical_receipt_path((_RECEIPTS_RELPATH / relative).as_posix())
        mode = candidate.lstat().st_mode
    except (OSError, ValueError) as exc:
        msg = f"receipt path is unreadable or escaped its corpus root: {candidate}"
        raise RatchetError(msg) from exc
    if not stat.S_ISREG(mode) or mode & 0o111:
        msg = f"receipt path must be regular non-executable YAML: {canonical}"
        raise RatchetError(msg)


def _validate_receipt_corpus(receipts_dir: Path) -> None:
    """Walk the corpus without following links, before handing any path to core."""
    try:
        root_mode = receipts_dir.lstat().st_mode
    except OSError as exc:
        msg = f"canonical receipt directory is unreadable: {receipts_dir}"
        raise RatchetError(msg) from exc
    if not stat.S_ISDIR(root_mode):
        msg = f"canonical receipt directory must be a real directory: {receipts_dir}"
        raise RatchetError(msg)
    pending = [receipts_dir]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                children = list(entries)
        except OSError as exc:
            msg = f"canonical receipt directory is unreadable: {directory}"
            raise RatchetError(msg) from exc
        for entry in children:
            candidate = Path(entry.path)
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                msg = f"receipt corpus entry is unreadable: {candidate}"
                raise RatchetError(msg) from exc
            if stat.S_ISDIR(mode):
                pending.append(candidate)
                continue
            if candidate.suffix == ".yaml":
                _validate_receipt_file(candidate, receipts_dir)


def _require_worktree_matches_index(
    repo_root: Path, path: PurePosixPath, label: str
) -> bytes:
    """Reject a pre-commit view that differs from the bytes about to be committed."""
    working = _read_regular_worktree_file(repo_root, path, label)
    indexed = _read_regular_index_file(repo_root, path, label)
    if working != indexed:
        msg = f"{label} working tree differs from staged index bytes: {path}"
        raise RatchetError(msg)
    return indexed


def _require_worktree_matches_index_files(
    repo_root: Path, paths: tuple[PurePosixPath, ...], label: str
) -> None:
    """Compare a changed-file batch to its index using two Git subprocesses."""
    for path, indexed in _read_regular_index_files(repo_root, paths, label).items():
        working = _read_regular_worktree_file(repo_root, path, label)
        if working != indexed:
            msg = f"{label} working tree differs from staged index bytes: {path}"
            raise RatchetError(msg)


def _baseline_at_commit(repo_root: Path, commit: str) -> Baseline | None:
    """Return the exact base ledger, or None when the committed ledger is absent."""
    if not _commit_exists(repo_root, commit):
        msg = f"base commit is unavailable for receipt-honesty comparison: {commit}"
        raise RatchetError(msg)
    if _GIT_EXECUTABLE is None:
        msg = "Git executable is unavailable for base-ledger validation"
        raise RatchetError(msg)
    entries = _git(
        repo_root,
        "ls-tree",
        "-z",
        commit,
        "--",
        _BASELINE_RELPATH.as_posix(),
    ).split(b"\0")
    entries = [entry for entry in entries if entry]
    if not entries:
        return None
    if len(entries) != 1:
        msg = f"base receipt-honesty baseline is ambiguous at {commit}"
        raise RatchetError(msg)
    try:
        metadata, encoded_path = entries[0].split(b"\t", 1)
        mode, object_type, _oid = metadata.split(b" ", 2)
        path = encoded_path.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        msg = f"base receipt-honesty baseline entry is malformed at {commit}"
        raise RatchetError(msg) from exc
    if (
        path != _BASELINE_RELPATH.as_posix()
        or mode != b"100644"
        or object_type != b"blob"
    ):
        msg = f"base receipt-honesty baseline is not a regular file at {commit}"
        raise RatchetError(msg)
    return parse_baseline(
        _git(repo_root, "show", f"{commit}:{_BASELINE_RELPATH.as_posix()}"),
        f"{commit}:{_BASELINE_RELPATH}",
    )


def _tree_blobs(repo_root: Path, commit: str) -> dict[str, str]:
    """Return the committed receipt-tree path -> blob OID map, fail-closed."""
    raw = _git(
        repo_root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        commit,
        "--",
        _RECEIPTS_RELPATH.as_posix(),
    )
    blobs: dict[str, str] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, object_type, oid = metadata.split(b" ", 2)
            raw_path = encoded_path.decode("utf-8")
            decoded_oid = oid.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            msg = f"unreadable receipt tree entry at {commit}"
            raise RatchetError(msg) from exc
        # The locked core directory scanner itself scans only ``*.yaml``.
        # Ignore sibling receipt-like extensions here for semantic parity; they
        # are not a hidden exclusion introduced by this wrapper.
        if PurePosixPath(raw_path).suffix != ".yaml":
            continue
        path = _canonical_receipt_path(raw_path)
        if (
            mode != b"100644"
            or object_type != b"blob"
            or not _OID_RE.fullmatch(decoded_oid)
        ):
            msg = f"non-blob or malformed receipt tree object at {commit}: {path}"
            raise RatchetError(msg)
        if path in blobs:
            msg = f"duplicate canonical receipt path in origin tree: {path}"
            raise RatchetError(msg)
        blobs[path] = decoded_oid
    return blobs


def _extract_origin_receipts(repo_root: Path, destination: Path) -> None:
    """Materialize only the frozen receipt tree in a temporary, isolated directory."""
    archive = _git(
        repo_root,
        "archive",
        "--format=tar",
        _ORIGIN_COMMIT,
        "--",
        _RECEIPTS_RELPATH.as_posix(),
    )
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            for member in tar.getmembers():
                if member.isdir():
                    continue
                if not member.isfile():
                    msg = (
                        "frozen receipt archive contains unsupported member: "
                        f"{member.name}"
                    )
                    raise RatchetError(msg)
                target = (destination / member.name).resolve()
                try:
                    target.relative_to(destination.resolve())
                except ValueError as exc:
                    msg = (
                        "frozen receipt archive contains traversal member: "
                        f"{member.name}"
                    )
                    raise RatchetError(msg) from exc
                payload = tar.extractfile(member)
                if payload is None:
                    msg = f"frozen receipt archive member is unreadable: {member.name}"
                    raise RatchetError(msg)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload.read())
    except (tarfile.TarError, OSError) as exc:
        msg = "unable to materialize frozen receipt corpus for provenance validation"
        raise RatchetError(msg) from exc


def _identity_for(
    *,
    path: str,
    rule: str,
    raw: bytes,
    object_format: str,
    blob_oid: str | None = None,
) -> FindingIdentity:
    return FindingIdentity(
        path=path,
        rule=rule,
        sha256=hashlib.sha256(raw).hexdigest(),
        blob_oid=blob_oid or _git_blob_oid(raw, object_format),
    )


def _scan_directory_identities(
    repo_root: Path,
    receipts_dir: Path,
    *,
    blob_oids: dict[str, str] | None = None,
) -> frozenset[FindingIdentity]:
    """Adapt locked core findings to content-bound identities without filtering.

    The wrapper never changes which locked-core rules apply.
    """
    _validate_receipt_corpus(receipts_dir)
    object_format = _git_object_format(repo_root)
    findings: set[FindingIdentity] = set()
    for finding in scan_receipts_directory(receipts_dir):
        try:
            relative = finding.receipt_path.resolve().relative_to(
                receipts_dir.resolve()
            )
        except ValueError as exc:
            msg = f"core scanner escaped receipt root: {finding.receipt_path}"
            raise RatchetError(msg) from exc
        path = _canonical_receipt_path((_RECEIPTS_RELPATH / relative).as_posix())
        raw = finding.receipt_path.read_bytes()
        expected_blob = blob_oids[path] if blob_oids is not None else None
        for violation in finding.violations:
            findings.add(
                _identity_for(
                    path=path,
                    rule=violation.rule.value,
                    raw=raw,
                    object_format=object_format,
                    blob_oid=expected_blob,
                )
            )
    return frozenset(findings)


def _scan_explicit_identities(
    repo_root: Path, receipt_paths: list[str], *, require_index: bool = False
) -> frozenset[FindingIdentity]:
    """Scan only the files pre-commit supplied, retaining exact core semantics."""
    receipts_dir = repo_root / Path(_RECEIPTS_RELPATH)
    explicit: list[Path] = []
    canonical_paths = tuple(
        PurePosixPath(_canonical_receipt_path(value)) for value in receipt_paths
    )
    if require_index:
        _require_worktree_matches_index_files(
            repo_root, canonical_paths, "changed receipt"
        )
    for canonical_path in canonical_paths:
        candidate = repo_root / Path(canonical_path)
        _validate_receipt_file(candidate, receipts_dir)
        explicit.append(candidate)
    object_format = _git_object_format(repo_root)
    findings: set[FindingIdentity] = set()
    for finding in scan_receipt_files(explicit):
        try:
            relative = finding.receipt_path.resolve().relative_to(
                receipts_dir.resolve()
            )
        except ValueError as exc:
            msg = f"core scanner escaped receipt root: {finding.receipt_path}"
            raise RatchetError(msg) from exc
        identity_path = _canonical_receipt_path(
            (_RECEIPTS_RELPATH / relative).as_posix()
        )
        raw = finding.receipt_path.read_bytes()
        for violation in finding.violations:
            findings.add(
                _identity_for(
                    path=identity_path,
                    rule=violation.rule.value,
                    raw=raw,
                    object_format=object_format,
                )
            )
    return frozenset(findings)


def origin_identities(repo_root: Path) -> frozenset[FindingIdentity]:
    """Scan the exact frozen provenance tree with the locked core scanner."""
    if not _commit_exists(repo_root, _ORIGIN_COMMIT):
        msg = f"immutable origin commit is unavailable: {_ORIGIN_COMMIT}"
        raise RatchetError(msg)
    blobs = _tree_blobs(repo_root, _ORIGIN_COMMIT)
    with tempfile.TemporaryDirectory(prefix="omn-17495-receipt-honesty-") as temporary:
        root = Path(temporary)
        _extract_origin_receipts(repo_root, root)
        receipts_dir = root / Path(_RECEIPTS_RELPATH)
        if not receipts_dir.is_dir():
            msg = (
                "frozen receipt archive does not contain the canonical receipt "
                "directory"
            )
            raise RatchetError(msg)
        identities = _scan_directory_identities(
            repo_root,
            receipts_dir,
            blob_oids=blobs,
        )
    for identity in identities:
        oid = blobs.get(identity.path)
        if oid != identity.blob_oid:
            msg = f"frozen finding lacks an exact origin blob binding: {identity.path}"
            raise RatchetError(msg)
    return identities


def current_identities(repo_root: Path) -> frozenset[FindingIdentity]:
    """Run the locked core scanner over the actual current working-tree corpus."""
    receipts_dir = repo_root / Path(_RECEIPTS_RELPATH)
    if not receipts_dir.is_dir():
        msg = f"canonical receipt directory is missing: {receipts_dir}"
        raise RatchetError(msg)
    return _scan_directory_identities(repo_root, receipts_dir)


def _validate_provenance(
    repo_root: Path, baseline: Baseline
) -> frozenset[FindingIdentity]:
    """Verify every ledger line's origin tree, object, raw-byte, and rule commitment."""
    origin = origin_identities(repo_root)
    unknown = baseline.identities - origin
    if unknown:
        msg = "baseline contains findings not committed by the immutable origin tree"
        raise RatchetError(_identity_report(msg, unknown))
    return origin


def _identity_report(
    label: str, identities: frozenset[FindingIdentity] | set[FindingIdentity]
) -> str:
    sample = ", ".join(
        f"{item.path} [{item.rule}] sha256={item.sha256}"
        for item in sorted(identities)[:5]
    )
    return f"{label}: {len(identities)} identity(s); sample: {sample}"


def _assert_live_equals_baseline(
    live: frozenset[FindingIdentity], baseline: Baseline
) -> None:
    """Reject both directions: no new/modified identity and no stale ledger line."""
    new = live - baseline.identities
    stale = baseline.identities - live
    if not new and not stale:
        return
    reports: list[str] = [
        "RECEIPT HONESTY RATCHET FAILED: live identity set differs from ledger"
    ]
    if new:
        reports.append(_identity_report("new or modified core finding", new))
    if stale:
        reports.append(_identity_report("stale ledger finding", stale))
    raise RatchetError("\n".join(reports))


def _index_diff_statuses(
    repo_root: Path, base_commit: str, path: PurePosixPath
) -> tuple[tuple[str, str], ...]:
    """Return exact staged path statuses against ``base_commit`` without renames.

    The authoritative gate runs against Git's index: before a local commit this
    is precisely the proposed commit tree, and in CI checkout it is HEAD.  A
    working-tree-only file can therefore never satisfy the genesis exception.
    """
    raw = _git(
        repo_root,
        "diff",
        "--cached",
        "--name-status",
        "--no-renames",
        "-z",
        base_commit,
        "--",
        path.as_posix(),
    )
    records = [record for record in raw.split(b"\0") if record]
    if len(records) % 2:
        msg = f"unreadable staged receipt-honesty diff for {path}"
        raise RatchetError(msg)
    statuses: list[tuple[str, str]] = []
    for status, raw_path in zip(records[::2], records[1::2], strict=True):
        try:
            statuses.append((status.decode("ascii"), raw_path.decode("utf-8")))
        except UnicodeDecodeError as exc:
            msg = f"unreadable staged receipt-honesty diff for {path}"
            raise RatchetError(msg) from exc
    return tuple(statuses)


def _require_staged_addition(
    repo_root: Path, base_commit: str, path: PurePosixPath, label: str
) -> None:
    """Require exactly one literal added file in the current proposed tree."""
    expected = (("A", path.as_posix()),)
    actual = _index_diff_statuses(repo_root, base_commit, path)
    if actual != expected:
        msg = (
            f"receipt-honesty genesis requires {label} to be a newly staged "
            f"literal addition against base {base_commit}; got {actual!r}"
        )
        raise RatchetError(msg)


def _assert_no_staged_receipt_changes(repo_root: Path, base_commit: str) -> None:
    """Forbid any historical receipt mutation in the one-time ledger genesis."""
    raw = _git(
        repo_root,
        "diff",
        "--cached",
        "--name-only",
        "--no-renames",
        "-z",
        base_commit,
        "--",
        _RECEIPTS_RELPATH.as_posix(),
    )
    paths = [
        record.decode("utf-8", errors="strict") for record in raw.split(b"\0") if record
    ]
    if paths:
        msg = (
            "receipt-honesty genesis forbids historical receipt changes; "
            f"found {len(paths)} staged path(s), sample: {', '.join(paths[:5])}"
        )
        raise RatchetError(msg)


def _validate_genesis_contract(raw: bytes, source: str) -> None:
    """Bind the exceptional first ledger introduction to its own ticket contract."""
    try:
        document = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        msg = f"receipt-honesty genesis contract is malformed: {source}"
        raise RatchetError(msg) from exc
    if not isinstance(document, dict) or document.get("ticket_id") != _TICKET:
        msg = (
            "receipt-honesty genesis contract must declare exact ticket identity "
            f"{_TICKET}: {source}"
        )
        raise RatchetError(msg)


def _history_touches(
    repo_root: Path, revision_range: str, path: PurePosixPath
) -> tuple[str, ...]:
    """Return at most two path-touching commits; enough to prove exact cardinality."""
    return tuple(
        item
        for item in _git(
            repo_root,
            "log",
            "-n",
            "2",
            "--format=%H",
            revision_range,
            "--",
            path.as_posix(),
        )
        .decode("ascii", errors="strict")
        .splitlines()
        if item
    )


def _assert_genesis_history_context(repo_root: Path, base_commit: str) -> bool:
    """Reject caller-selected and delete/re-add histories before first introduction.

    ``enforce_corpus`` normalizes ``base_commit`` before it reaches this check;
    repeat the ancestry facts here because this is the sole exceptional path.
    The base must descend the fixed bootstrap commit, not merely the older
    receipt origin, so an arbitrary pre-bootstrap comparison cannot reopen it.
    The bounded history checks distinguish local staged genesis (no path commits
    after base) from CI's one committed initial addition. Both authorization
    surfaces must have the same history: a prior appearance, multiple touches,
    a mismatch, or a later edit all fail closed.
    """
    head = _head_commit(repo_root)
    if not _is_ancestor(repo_root, _BOOTSTRAP_BASE_COMMIT, base_commit):
        msg = (
            "receipt-honesty genesis base must descend from fixed bootstrap base "
            f"{_BOOTSTRAP_BASE_COMMIT}; got {base_commit}"
        )
        raise RatchetError(msg)
    if not _is_ancestor(repo_root, base_commit, head):
        msg = (
            "receipt-honesty genesis base is not the normalized current ancestor "
            f"of HEAD: base={base_commit}, head={head}"
        )
        raise RatchetError(msg)
    paths = ((_BASELINE_RELPATH, "ledger"), (_CONTRACT_RELPATH, "contract"))
    for path, label in paths:
        prior = _history_touches(
            repo_root, f"{_BOOTSTRAP_BASE_COMMIT}..{base_commit}", path
        )
        if prior:
            msg = (
                "receipt-honesty genesis is forbidden because the "
                f"{label} path appeared in committed history before "
                f"missing-ledger base {base_commit}: {prior[0]}"
            )
            raise RatchetError(msg)
    introduced = {
        label: _history_touches(repo_root, f"{base_commit}..{head}", path)
        for path, label in paths
    }
    if all(not touches for touches in introduced.values()):
        # A feature branch may legitimately contain unrelated commits ahead of
        # origin/HEAD before it stages the two authorization surfaces locally.
        return False
    if any(len(touches) != 1 for touches in introduced.values()):
        msg = (
            "receipt-honesty genesis requires exactly one committed introduction "
            "for both ledger and contract between base and HEAD; found "
            + ", ".join(
                f"{label}={len(touches)}" for label, touches in introduced.items()
            )
        )
        raise RatchetError(msg)
    if introduced["ledger"] != introduced["contract"]:
        msg = (
            "receipt-honesty genesis requires ledger and contract introduction in "
            "the same committed change"
        )
        raise RatchetError(msg)
    return True


def _require_index_matches_head(
    repo_root: Path, path: PurePosixPath, label: str
) -> None:
    """CI genesis cannot use a dirty index to alter its one committed introduction."""
    if _GIT_EXECUTABLE is None:
        msg = f"Git executable is unavailable for {label} HEAD/index validation"
        raise RatchetError(msg)
    result = subprocess.run(  # noqa: S603 -- fixed Git plumbing
        [
            _GIT_EXECUTABLE,
            "diff",
            "--cached",
            "--quiet",
            "HEAD",
            "--",
            path.as_posix(),
        ],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return
    detail = result.stderr.decode("utf-8", errors="replace").strip()
    msg = f"{label} staged index differs from HEAD during committed genesis"
    if detail:
        msg = f"{msg}: {detail}"
    raise RatchetError(msg)


def _assert_controlled_genesis(
    repo_root: Path,
    baseline: Baseline,
    base_commit: str,
    origin: frozenset[FindingIdentity],
) -> None:
    """Permit exactly the first ledger introduction, and nothing ledger-like after it.

    A missing base ledger is not enough.  The ledger and its OMN-17495 contract
    must both be literal additions in the current index, receipts must be
    untouched, and the live, schema-validated ledger must equal the immutable
    origin census.  Once any comparison base contains the ledger, this function
    is unreachable and ordinary two-way equality/monotonicity applies.
    """
    committed_genesis = _assert_genesis_history_context(repo_root, base_commit)
    _require_staged_addition(repo_root, base_commit, _BASELINE_RELPATH, "ledger")
    _require_staged_addition(repo_root, base_commit, _CONTRACT_RELPATH, "contract")
    _assert_no_staged_receipt_changes(repo_root, base_commit)
    if committed_genesis:
        _require_index_matches_head(
            repo_root, _BASELINE_RELPATH, "receipt-honesty baseline"
        )
        _require_index_matches_head(
            repo_root, _CONTRACT_RELPATH, "receipt-honesty genesis contract"
        )
    indexed_ledger = _require_worktree_matches_index(
        repo_root, _BASELINE_RELPATH, "receipt-honesty baseline"
    )
    indexed_baseline = parse_baseline(
        indexed_ledger, f":{_BASELINE_RELPATH.as_posix()}"
    )
    if baseline != indexed_baseline:
        msg = "receipt-honesty baseline changed after working-tree validation"
        raise RatchetError(msg)
    indexed_contract = _require_worktree_matches_index(
        repo_root, _CONTRACT_RELPATH, "receipt-honesty genesis contract"
    )
    _validate_genesis_contract(indexed_contract, f":{_CONTRACT_RELPATH.as_posix()}")
    if (
        len(baseline.findings) != _SEED_FINDING_COUNT
        or len({item.path for item in baseline.findings}) != _SEED_RECEIPT_PATH_COUNT
    ):
        msg = "bootstrap baseline does not carry the exact immutable 1142/1055 census"
        raise RatchetError(msg)
    if baseline.identities != origin:
        msg = (
            "bootstrap baseline is not exactly the immutable origin finding "
            "identity set"
        )
        raise RatchetError(msg)


def _assert_base_monotonic(
    repo_root: Path,
    baseline: Baseline,
    base_commit: str,
    origin: frozenset[FindingIdentity],
) -> None:
    """Forbid ledger growth; narrowly permit one controlled ledger genesis."""
    committed_baseline = _baseline_at_commit(repo_root, base_commit)
    if committed_baseline is None:
        _assert_controlled_genesis(repo_root, baseline, base_commit, origin)
        return

    _validate_provenance(repo_root, committed_baseline)
    growth = baseline.identities - committed_baseline.identities
    if growth:
        raise RatchetError(
            _identity_report(
                "RECEIPT HONESTY RATCHET FAILED: baseline growth is forbidden", growth
            )
        )


def enforce_corpus(repo_root: Path, request: CiBaseRequest | None = None) -> None:
    """Authoritative full-corpus gate: provenance, equality, then base monotonicity."""
    baseline = load_baseline(repo_root)
    indexed_baseline = parse_baseline(
        _require_worktree_matches_index(
            repo_root, _BASELINE_RELPATH, "receipt-honesty baseline"
        ),
        f":{_BASELINE_RELPATH.as_posix()}",
    )
    if baseline != indexed_baseline:
        msg = "receipt-honesty baseline changed after working-tree validation"
        raise RatchetError(msg)
    origin = _validate_provenance(repo_root, baseline)
    live = current_identities(repo_root)
    _assert_live_equals_baseline(live, baseline)
    normalized_base = (
        resolve_ci_base(repo_root, request)
        if request is not None
        else resolve_local_base(repo_root)
    )
    _assert_base_monotonic(repo_root, baseline, normalized_base, origin)
    sys.stdout.write(
        "RECEIPT HONESTY RATCHET PASSED: "
        f"{len(live)} findings across "
        f"{len({item.path for item in live})} receipt paths\n"
    )


def enforce_changed(
    repo_root: Path, receipt_paths: list[str], *, require_index: bool = False
) -> None:
    """Fast changed-file gate, preserving historical debt only by exact identity."""
    baseline = load_baseline(repo_root)
    requested_paths = {_canonical_receipt_path(value) for value in receipt_paths}
    live = _scan_explicit_identities(
        repo_root, sorted(requested_paths), require_index=require_index
    )
    existing = frozenset(
        item for item in baseline.identities if item.path in requested_paths
    )
    new = live - baseline.identities
    stale = existing - live
    if new or stale:
        reports = [
            "RECEIPT HONESTY FAST RATCHET FAILED: changed receipt identity "
            "differs from ledger"
        ]
        if new:
            reports.append(_identity_report("new or modified core finding", new))
        if stale:
            reports.append(_identity_report("stale ledger finding", stale))
        raise RatchetError("\n".join(reports))
    sys.stdout.write(
        "RECEIPT HONESTY FAST RATCHET PASSED: "
        f"{len(live)} finding(s) across "
        f"{len(requested_paths)} changed receipt path(s)\n"
    )


def seed_baseline(repo_root: Path) -> None:
    """Create the baseline exactly once, only from the controlled bootstrap state."""
    path = _baseline_path(repo_root)
    if path.exists():
        msg = f"refusing to overwrite existing receipt-honesty baseline: {path}"
        raise RatchetError(msg)
    head = _head_commit(repo_root)
    if head != _BOOTSTRAP_BASE_COMMIT:
        msg = (
            "receipt-honesty bootstrap is allowed only at exact bootstrap HEAD "
            f"{_BOOTSTRAP_BASE_COMMIT}; got {head}"
        )
        raise RatchetError(msg)
    if _baseline_at_commit(repo_root, head) is not None:
        msg = (
            "receipt-honesty bootstrap requires the baseline to be absent from "
            "bootstrap HEAD"
        )
        raise RatchetError(msg)
    origin = origin_identities(repo_root)
    live = current_identities(repo_root)
    if (
        len(origin) != _SEED_FINDING_COUNT
        or len({item.path for item in origin}) != _SEED_RECEIPT_PATH_COUNT
    ):
        msg = (
            "immutable origin receipt-honesty census changed: expected "
            f"{_SEED_FINDING_COUNT}/{_SEED_RECEIPT_PATH_COUNT}, got "
            f"{len(origin)}/{len({item.path for item in origin})}"
        )
        raise RatchetError(msg)
    if (
        len(live) != _SEED_FINDING_COUNT
        or len({item.path for item in live}) != _SEED_RECEIPT_PATH_COUNT
    ):
        msg = (
            "live bootstrap receipt-honesty census changed: expected "
            f"{_SEED_FINDING_COUNT}/{_SEED_RECEIPT_PATH_COUNT}, got "
            f"{len(live)}/{len({item.path for item in live})}"
        )
        raise RatchetError(msg)
    if live != origin:
        msg = "live bootstrap corpus differs from immutable origin finding identities"
        raise RatchetError(msg)
    document: dict[str, object] = {
        "schema_version": 1,
        "origin_commit": _ORIGIN_COMMIT,
        "bootstrap_base_commit": _BOOTSTRAP_BASE_COMMIT,
        "seed_finding_count": _SEED_FINDING_COUNT,
        "seed_receipt_path_count": _SEED_RECEIPT_PATH_COUNT,
        "scanner_limitation": _SCANNER_LIMITATION,
        "findings": [item.as_mapping() for item in sorted(origin)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    sys.stdout.write(
        "RECEIPT HONESTY BASELINE SEEDED: "
        f"{len(origin)} findings across "
        f"{len({item.path for item in origin})} receipt paths\n"
    )


def _load_workflow(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        msg = f"wiring file is missing or malformed: {path}"
        raise RatchetError(msg) from exc
    if not isinstance(data, dict):
        msg = f"wiring file is not a mapping: {path}"
        raise RatchetError(msg)
    return data


def _hook_by_id(config: dict[str, Any], hook_id: str) -> list[dict[str, Any]]:
    repos = config.get("repos")
    if not isinstance(repos, list):
        return []
    return [
        hook
        for repo in repos
        if isinstance(repo, dict) and isinstance(repo.get("hooks"), list)
        for hook in repo["hooks"]
        if isinstance(hook, dict) and hook.get("id") == hook_id
    ]


def _run_blob(job: dict[str, Any]) -> str:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return ""
    return "\n".join(
        step["run"]
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    )


def _strict_gate_jobs(gate_module: Path) -> tuple[str, ...] | None:
    """Extract the literal strict registration without importing executable CI code."""
    try:
        source = gate_module.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(
        r"STRICT_GATE_JOBS\s*:\s*tuple\[str,\s*\.\.\.\]\s*=\s*\((.*?)\n\)",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        return None
    return tuple(re.findall(r'"([^"]+)"', match.group(1)))


def _check_fast_hook(config: dict[str, Any]) -> list[str]:
    """Assert the low-latency changed-file hook stays change scoped."""
    failures: list[str] = []
    fast_hooks = _hook_by_id(config, "check-receipt-honesty")
    if len(fast_hooks) != 1:
        failures.append("fast `check-receipt-honesty` hook is absent or duplicated")
    else:
        hook = fast_hooks[0]
        entry = hook.get("entry")
        if (
            not isinstance(entry, str)
            or f"python -m {_MODULE} --changed --index" not in entry
        ):
            failures.append(
                "fast receipt-honesty hook does not invoke wrapper --changed --index"
            )
        if hook.get("pass_filenames") is False:
            failures.append(
                "fast receipt-honesty hook must receive changed receipt filenames"
            )
        if (
            not isinstance(hook.get("files"), str)
            or "drift/dod_receipts" not in hook["files"]
        ):
            failures.append("fast receipt-honesty hook is not receipt-change scoped")
    return failures


def _check_corpus_hook(config: dict[str, Any], repo_root: Path) -> list[str]:
    """Assert the unbatched authority hook and focused test module remain present."""
    failures: list[str] = []
    corpus_hooks = _hook_by_id(config, "receipt-honesty-corpus-ratchet")
    if len(corpus_hooks) != 1:
        failures.append(
            "authoritative `receipt-honesty-corpus-ratchet` hook is absent or "
            "duplicated"
        )
    else:
        hook = corpus_hooks[0]
        entry = hook.get("entry")
        if not isinstance(entry, str) or f"python -m {_MODULE} --corpus" not in entry:
            failures.append(
                "authoritative receipt-honesty hook does not invoke wrapper --corpus"
            )
        if hook.get("pass_filenames") is not False:
            failures.append(
                "authoritative receipt-honesty hook must set pass_filenames: false"
            )
        if hook.get("always_run") is not True:
            failures.append(
                "authoritative receipt-honesty hook must be always_run and "
                "cannot be bypassed by batching"
            )
        if hook.get("stages") != ["pre-commit"]:
            failures.append(
                "authoritative receipt-honesty hook must run at pre-commit stage"
            )
    if not (
        repo_root / "tests" / "unit" / "validation" / "test_receipt_honesty_ratchet.py"
    ).is_file():
        failures.append("focused receipt-honesty ratchet tests are missing")
    if not (
        repo_root
        / "src"
        / "onex_change_control"
        / "validation"
        / "receipt_honesty_ratchet.py"
    ).is_file():
        failures.append("receipt-honesty ratchet wrapper is missing")
    return failures


def _step_with_id(steps: list[Any], step_id: str) -> dict[str, Any] | None:
    for step in steps:
        if isinstance(step, dict) and step.get("id") == step_id:
            return step
    return None


def _core_checkout_is_dynamic(steps: list[Any]) -> bool:
    for step in steps:
        if not isinstance(step, dict) or step.get("uses") != "actions/checkout@v7":
            continue
        options = step.get("with")
        if not isinstance(options, dict):
            continue
        if (
            options.get("repository") == "OmniNode-ai/omnibase_core"
            and options.get("ref") == "${{ steps.validator_ref.outputs.ref }}"
            and options.get("path") == "omnibase_core_source"
        ):
            return True
    return False


def _uses_uv_python_312(steps: list[Any]) -> bool:
    for step in steps:
        if (
            not isinstance(step, dict)
            or step.get("uses") != "./.github/actions/setup-uv"
        ):
            continue
        options = step.get("with")
        if isinstance(options, dict) and str(options.get("python-version")) == "3.12":
            return True
    return False


def _check_ci_job(  # noqa: C901, PLR0912 -- static fail-closed wiring vectors share one report
    job: dict[str, Any], repo_root: Path
) -> list[str]:
    """Assert unconditional dynamic-core CI enforcement and event-correct bases."""
    failures: list[str] = []
    if job.get("name") != "Receipt Honesty Gate":
        failures.append(
            "CI job `honesty-gate` must retain exact name `Receipt Honesty Gate`"
        )
    if "if" in job or "needs" in job:
        failures.append("Receipt Honesty Gate must remain unconditional (no if/needs)")
    steps = job.get("steps")
    if not isinstance(steps, list):
        return [*failures, "Receipt Honesty Gate has no steps list"]
    validator_ref = _step_with_id(steps, "validator_ref")
    if validator_ref is None or "Resolve omnibase_core validator ref" not in str(
        validator_ref.get("name")
    ):
        failures.append(
            "Receipt Honesty Gate lacks the dynamic omnibase_core ref resolver"
        )
    if not _core_checkout_is_dynamic(steps):
        failures.append(
            "Receipt Honesty Gate lacks dynamic omnibase_core checkout/ref wiring"
        )
    if not _uses_uv_python_312(steps):
        failures.append("Receipt Honesty Gate must use setup-uv with Python 3.12")

    run_blob = _run_blob(job)
    required_fragments = (
        f"python -m {_MODULE} --corpus",
        "--event-name",
        "BEFORE_SHA",
        "test_receipt_honesty_ratchet.py",
        f"python -m {_MODULE} --check-wiring",
        "uv run python",
        "uv run pytest",
        "gh api",
        "default_branch",
        "PR_BASE_REF",
        "MERGE_GROUP_BASE_REF",
    )
    for fragment in required_fragments:
        if fragment not in run_blob:
            failures.append(
                "Receipt Honesty Gate is missing required full-corpus/base-monotonic "
                f"wiring: {fragment}"
            )
    corpus_step = next(
        (
            step
            for step in steps
            if isinstance(step, dict)
            and f"python -m {_MODULE} --corpus" in str(step.get("run"))
        ),
        None,
    )
    corpus_environment = (
        corpus_step.get("env") if isinstance(corpus_step, dict) else None
    )
    if not isinstance(corpus_environment, dict):
        failures.append("Receipt Honesty Gate corpus step must declare an environment")
        return failures
    corpus_run = str(corpus_step.get("run")) if isinstance(corpus_step, dict) else ""
    if "--base" in corpus_run or "--resolve-ci-base" in corpus_run:
        failures.append(
            "Receipt Honesty Gate corpus step must invoke its canonical resolver "
            "directly, not pass a base SHA"
        )
    for fragment in (
        "--event-name",
        "--pr-base-ref",
        "--merge-group-base-ref",
        "--before-sha",
        "--default-branch",
    ):
        if fragment not in corpus_run:
            failures.append(
                "Receipt Honesty Gate corpus command does not feed canonical "
                "resolver input: "
                f"{fragment}"
            )
    if "BASE_REF" in corpus_environment:
        failures.append(
            "Receipt Honesty Gate must not use a fallback BASE_REF for PR or "
            "merge_group"
        )
    if (
        corpus_environment.get("PR_BASE_REF")
        != "${{ github.event.pull_request.base.ref || '' }}"
    ):
        failures.append(
            "Receipt Honesty Gate must use an explicit pull_request base ref"
        )
    if corpus_environment.get("MERGE_GROUP_BASE_REF") != (
        "${{ github.event.merge_group.base_ref || '' }}"
    ):
        failures.append(
            "Receipt Honesty Gate must use an explicit merge_group base ref"
        )
    if not isinstance(corpus_environment, dict) or corpus_environment.get(
        "PYTHONPATH"
    ) != ("${{ github.workspace }}/omnibase_core_source/src"):
        failures.append(
            "Receipt Honesty Gate corpus step must set PYTHONPATH to "
            "omnibase_core_source/src"
        )
    if not isinstance(corpus_environment, dict) or corpus_environment.get(
        "GH_TOKEN"
    ) != ("${{ github.token }}"):
        failures.append("Receipt Honesty Gate default-branch lookup must have GH_TOKEN")
    strict = _strict_gate_jobs(repo_root / "scripts" / "ci" / "ci_summary_gate.py")
    if strict is None or "Receipt Honesty Gate" not in strict:
        failures.append(
            "Receipt Honesty Gate is not registered in CI Summary STRICT_GATE_JOBS"
        )
    return failures


def check_wiring(repo_root: Path) -> list[str]:
    """Static anti-removal anchor for both hooks and the strict CI context."""
    precommit_path = repo_root / ".pre-commit-config.yaml"
    ci_path = repo_root / ".github" / "workflows" / "ci.yml"
    try:
        precommit = _load_workflow(precommit_path)
    except RatchetError as exc:
        return [str(exc)]
    failures = _check_fast_hook(precommit)
    failures.extend(_check_corpus_hook(precommit, repo_root))

    try:
        ci = _load_workflow(ci_path)
    except RatchetError as exc:
        failures.append(str(exc))
        return failures
    jobs = ci.get("jobs")
    job = jobs.get("honesty-gate") if isinstance(jobs, dict) else None
    if not isinstance(job, dict):
        failures.append("CI job `honesty-gate` is missing")
        return failures
    failures.extend(_check_ci_job(job, repo_root))
    return failures


def _check_wiring_or_raise(repo_root: Path) -> None:
    failures = check_wiring(repo_root)
    if failures:
        raise RatchetError(
            "RECEIPT HONESTY RATCHET WIRING FAILED:\n"
            + "\n".join(f"- {item}" for item in failures)
        )
    sys.stdout.write("RECEIPT HONESTY RATCHET WIRING PASSED\n")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--seed-baseline", action="store_true", help="one-time controlled ledger seed"
    )
    mode.add_argument(
        "--corpus", action="store_true", help="authoritative full-corpus identity gate"
    )
    mode.add_argument(
        "--changed", action="store_true", help="fast changed-receipt identity gate"
    )
    mode.add_argument(
        "--check-wiring", action="store_true", help="static anti-removal wiring check"
    )
    mode.add_argument(
        "--resolve-ci-base",
        action="store_true",
        help="resolve the event-correct, ancestor-verified CI ledger base",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="require changed receipt paths to match stage-0 index blobs",
    )
    parser.add_argument("--event-name", default="")
    parser.add_argument("--pr-base-ref", default="")
    parser.add_argument("--merge-group-base-ref", default="")
    parser.add_argument("--before-sha", default="")
    parser.add_argument("--default-branch", default="")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "receipt_paths", nargs="*", help="canonical changed receipt paths for --changed"
    )
    return parser.parse_args(argv)


def _reject_if(condition: Any, message: str) -> None:
    """Raise one consistent CLI-shape error outside main's reporting boundary."""
    if condition:
        raise RatchetError(message)


def _run_mode(args: argparse.Namespace, root: Path) -> None:
    """Dispatch a fully validated command mode."""
    ci_base_args = (
        args.event_name,
        args.pr_base_ref,
        args.merge_group_base_ref,
        args.before_sha,
        args.default_branch,
    )
    if args.resolve_ci_base:
        _reject_if(
            bool(args.receipt_paths) or args.index,
            "--resolve-ci-base accepts no ledger inputs",
        )
        sys.stdout.write(
            resolve_ci_base(
                root,
                CiBaseRequest(
                    event_name=args.event_name,
                    pr_base_ref=args.pr_base_ref,
                    merge_group_base_ref=args.merge_group_base_ref,
                    before_sha=args.before_sha,
                    default_branch=args.default_branch,
                ),
            )
            + "\n"
        )
    elif args.seed_baseline:
        _reject_if(any(ci_base_args), "--seed-baseline does not accept CI event inputs")
        _reject_if(
            args.receipt_paths or args.index,
            "--seed-baseline accepts neither index mode nor receipt paths",
        )
        seed_baseline(root)
    elif args.corpus:
        _reject_if(
            bool(args.receipt_paths) or args.index,
            "--corpus does not accept receipt paths or --index",
        )
        request = (
            CiBaseRequest(
                event_name=args.event_name,
                pr_base_ref=args.pr_base_ref,
                merge_group_base_ref=args.merge_group_base_ref,
                before_sha=args.before_sha,
                default_branch=args.default_branch,
            )
            if any(ci_base_args)
            else None
        )
        enforce_corpus(root, request)
    elif args.changed:
        _reject_if(any(ci_base_args), "--changed does not accept CI event inputs")
        enforce_changed(root, args.receipt_paths, require_index=args.index)
    else:
        _reject_if(any(ci_base_args), "--check-wiring does not accept CI event inputs")
        _reject_if(
            args.receipt_paths or args.index,
            "--check-wiring accepts neither index mode nor receipt paths",
        )
        _check_wiring_or_raise(root)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one fail-closed receipt-honesty ratchet mode."""
    args = _parse_args(argv)
    try:
        root = _repo_root(args.repo_root)
        _run_mode(args, root)
    except RatchetError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
