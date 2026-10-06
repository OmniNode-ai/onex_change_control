# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Validate the canonical pre-execution action-authorization registry.

The registry is intentionally separate from production-promotion and
staging-namespace grants.  A pre-execution authorization is a bounded,
single-use permit for exactly one action and binds all of the following:

* the Linear ticket and its central OCC contract path;
* the immutable OCC commit and SHA-256 digest of that contract at that commit;
* one action, source commit, and immutable artifact digest;
* the exact database, schema, service, and principal; and
* an issuer, absolute expiry, unique nonce, and one-time-use lifecycle.

This validator captures the registry and contract bindings from bounded,
immutable Git objects. An action executor must additionally atomically claim
the nonce before the action begins; a YAML registry cannot itself serialize
concurrent consumers.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REQUIRED_FIELDS = frozenset(
    {
        "authorization_id",
        "ticket_id",
        "contract_path",
        "contract_commit_sha",
        "contract_sha256",
        "action_id",
        "source_sha",
        "artifact_sha256",
        "target_database",
        "target_schema",
        "target_service",
        "target_principal",
        "execute_enabled",
        "issuer",
        "nonce",
        "issued_at",
        "expires_at",
        "one_time_use",
        "reason",
    }
)
OPTIONAL_FIELDS = frozenset({"consumed", "consumed_at", "consumed_by_correlation_id"})

AUTHORIZATION_ID_RE = re.compile(
    r"^action-auth-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
TICKET_ID_RE = re.compile(r"^OMN-[1-9][0-9]*$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ACTION_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
ISSUER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32,128}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")

CANONICAL_REGISTRY_PATH = Path("grants/pre_execution_action_authorizations.yaml")
GIT_TIMEOUT_SECONDS = 10
TREE_ENTRY_RECORD_COUNT = 2
# The registry is a small, human-reviewed governance document. These caps keep
# one invocation bounded before YAML parsing or any per-entry Git lookup.
MAX_REGISTRY_BLOB_BYTES = 64 * 1024
MAX_CONTRACT_BLOB_BYTES = 256 * 1024
MAX_AUTHORIZATION_COUNT = 128
MAX_REASON_LENGTH = 1024
GitRunner = Callable[[tuple[str, ...], Path], bytes]


@dataclass(frozen=True)
class ModelImmutableOCCSnapshot:
    """Immutable OCC objects that authorize one registry evaluation."""

    repository_root: Path
    head_sha: str
    tree_sha: str
    registry_blob_oid: str
    registry_sha256: str
    registry_bytes: bytes


@dataclass(frozen=True)
class ModelImmutableContractBlob:
    """One immutable ``commit:path`` contract blob used by a registry entry."""

    commit_sha: str
    contract_path: str
    blob_oid: str
    content: bytes


@dataclass(frozen=True)
class ModelValidatedPreExecutionAuthorization:
    """Immutable, validated authorization projection for a future consumer.

    This is intentionally data-only: validating a record never consumes its
    nonce and never enables or executes an action.
    """

    snapshot: ModelImmutableOCCSnapshot
    contract_blob_oid: str
    authorization_id: str
    ticket_id: str
    contract_path: str
    contract_commit_sha: str
    contract_sha256: str
    action_id: str
    source_sha: str
    artifact_sha256: str
    target_database: str
    target_schema: str
    target_service: str
    target_principal: str
    execute_enabled: bool
    issuer: str
    nonce: str
    issued_at: str
    expires_at: str
    one_time_use: bool
    reason: str
    consumed: bool
    consumed_at: str | None
    consumed_by_correlation_id: str | None


@dataclass(frozen=True)
class ModelPreExecutionAuthorizationValidationResult:
    """The fail-closed result of validating a registry file."""

    passed: bool
    errors: list[str]
    entry_count: int
    snapshot: ModelImmutableOCCSnapshot | None = None
    records: tuple[ModelValidatedPreExecutionAuthorization, ...] = ()


def parse_iso8601(value: str) -> datetime | None:
    """Parse a strict ISO-8601 instant."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _repository_root() -> Path:
    """Return this package's checkout root for the CLI default only."""
    return Path(__file__).resolve().parents[3]


def _run_git(arguments: tuple[str, ...], repository_root: Path) -> bytes:
    """Run one bounded Git object query or fail closed."""
    git_executable = shutil.which("git")
    if git_executable is None:
        message = "git executable is unavailable for immutable OCC snapshot"
        raise ValueError(message)
    clean_environment = {
        name: value for name, value in os.environ.items() if not name.startswith("GIT_")
    }
    try:
        completed = subprocess.run(  # noqa: S603
            [git_executable, "-C", str(repository_root), *arguments],
            cwd=repository_root,
            capture_output=True,
            check=False,
            env=clean_environment,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        message = (
            f"git object query timed out after {GIT_TIMEOUT_SECONDS}s: {arguments!r}"
        )
        raise ValueError(message) from exc
    except OSError as exc:
        message = f"git object query could not start: {exc}"
        raise ValueError(message) from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        message = f"git object query failed for {arguments!r}: {detail}"
        raise ValueError(message)
    return completed.stdout


def _git_text(
    git_runner: GitRunner, repository_root: Path, arguments: tuple[str, ...]
) -> str:
    """Return one non-empty, single-line Git object identifier."""
    value = (
        git_runner(arguments, repository_root).decode("utf-8", errors="strict").strip()
    )
    if not value or "\n" in value:
        message = f"git object query returned invalid identifier for {arguments!r}"
        raise ValueError(message)
    return value


def _git_blob_bytes(
    git_runner: GitRunner,
    repository_root: Path,
    blob_oid: str,
    *,
    label: str,
    maximum_size: int,
) -> bytes:
    """Read one Git blob only after exact type and bounded-size checks."""
    object_type = _git_text(git_runner, repository_root, ("cat-file", "-t", blob_oid))
    if object_type != "blob":
        message = f"{label} is not an immutable regular blob"
        raise ValueError(message)
    size_text = _git_text(git_runner, repository_root, ("cat-file", "-s", blob_oid))
    if not size_text.isdecimal():
        message = f"{label} has a malformed Git object size"
        raise ValueError(message)
    object_size = int(size_text)
    if object_size > maximum_size:
        message = f"{label} exceeds the {maximum_size}-byte safety limit"
        raise ValueError(message)
    content = git_runner(("cat-file", "blob", blob_oid), repository_root)
    if len(content) != object_size:
        message = f"{label} changed size while being read"
        raise ValueError(message)
    return content


def _identified_repository_root(repository_root: Path, git_runner: GitRunner) -> Path:
    """Identify and pin the OCC checkout before any registry lookup."""
    requested_root = repository_root.resolve()
    reported_root = Path(
        _git_text(git_runner, requested_root, ("rev-parse", "--show-toplevel"))
    ).resolve()
    if reported_root != requested_root:
        message = (
            "identified OCC repository root does not match requested root: "
            f"{reported_root} != {requested_root}"
        )
        raise ValueError(message)
    return reported_root


def _canonical_registry_path(repository_root: Path, requested_path: Path) -> Path:
    """Reject every registry input except the canonical in-repository path."""
    candidate = (
        requested_path
        if requested_path.is_absolute()
        else repository_root / requested_path
    )
    canonical = repository_root / CANONICAL_REGISTRY_PATH
    try:
        candidate.relative_to(repository_root)
    except ValueError as exc:
        message = "registry path escapes the identified OCC repository"
        raise ValueError(message) from exc
    if candidate != canonical:
        message = (
            "registry path must be exactly "
            f"{CANONICAL_REGISTRY_PATH.as_posix()!r} inside the identified "
            "OCC repository"
        )
        raise ValueError(message)
    return canonical


def _parse_registry_tree_entry(tree_entry: bytes) -> tuple[str, str]:
    """Require a regular canonical registry blob in the immutable tree."""
    records = tree_entry.split(b"\0")
    if len(records) != TREE_ENTRY_RECORD_COUNT or records[1]:
        message = "immutable registry tree lookup returned an ambiguous entry set"
        raise ValueError(message)
    try:
        header, raw_path = records[0].split(b"\t", maxsplit=1)
        mode, kind, blob_oid = header.decode("ascii").split(" ")
        registry_path = raw_path.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        message = "immutable registry tree lookup returned malformed data"
        raise ValueError(message) from exc
    if (
        mode != "100644"
        or kind != "blob"
        or not SHA1_RE.fullmatch(blob_oid)
        or registry_path != CANONICAL_REGISTRY_PATH.as_posix()
    ):
        message = "immutable registry must be one canonical regular blob"
        raise ValueError(message)
    return blob_oid, registry_path


def _capture_snapshot(
    repository_root: Path, git_runner: GitRunner
) -> ModelImmutableOCCSnapshot:
    """Capture registry authority entirely from one pinned Git commit/tree."""
    head_sha = _git_text(
        git_runner, repository_root, ("rev-parse", "--verify", "HEAD^{commit}")
    )
    if not SHA1_RE.fullmatch(head_sha):
        message = "HEAD did not resolve to a full immutable commit SHA"
        raise ValueError(message)
    tree_sha = _git_text(
        git_runner,
        repository_root,
        ("rev-parse", "--verify", f"{head_sha}^{{tree}}"),
    )
    if not SHA1_RE.fullmatch(tree_sha):
        message = "HEAD tree did not resolve to a full immutable tree SHA"
        raise ValueError(message)
    tree_entry = git_runner(
        ("ls-tree", "-z", head_sha, "--", CANONICAL_REGISTRY_PATH.as_posix()),
        repository_root,
    )
    registry_blob_oid, _ = _parse_registry_tree_entry(tree_entry)
    registry_bytes = _git_blob_bytes(
        git_runner,
        repository_root,
        registry_blob_oid,
        label="immutable registry",
        maximum_size=MAX_REGISTRY_BLOB_BYTES,
    )
    return ModelImmutableOCCSnapshot(
        repository_root=repository_root,
        head_sha=head_sha,
        tree_sha=tree_sha,
        registry_blob_oid=registry_blob_oid,
        registry_sha256=f"sha256:{hashlib.sha256(registry_bytes).hexdigest()}",
        registry_bytes=registry_bytes,
    )


class _ImmutableContractLoader:
    """Cache immutable contract blobs resolved only from ``commit:path`` objects."""

    def __init__(
        self,
        snapshot: ModelImmutableOCCSnapshot,
        git_runner: GitRunner,
    ) -> None:
        self._snapshot = snapshot
        self._repository_root = snapshot.repository_root
        self._git_runner = git_runner
        self._cache: dict[tuple[str, str], ModelImmutableContractBlob] = {}
        self._validated_commits: set[str] = set()

    def _validate_ancestral_commit(self, commit_sha: str) -> None:
        """Require each referenced contract commit to precede snapshot HEAD once."""
        if commit_sha in self._validated_commits:
            return
        resolved_commit = _git_text(
            self._git_runner,
            self._repository_root,
            ("rev-parse", "--verify", f"{commit_sha}^{{commit}}"),
        )
        if resolved_commit != commit_sha:
            message = (
                f"contract commit {commit_sha} did not resolve exactly as a commit"
            )
            raise ValueError(message)
        try:
            self._git_runner(
                (
                    "merge-base",
                    "--is-ancestor",
                    commit_sha,
                    self._snapshot.head_sha,
                ),
                self._repository_root,
            )
        except ValueError as exc:
            message = (
                f"contract commit {commit_sha} is not an ancestor of immutable "
                f"snapshot HEAD {self._snapshot.head_sha}"
            )
            raise ValueError(message) from exc
        self._validated_commits.add(commit_sha)

    def __call__(
        self, commit_sha: str, contract_path: str
    ) -> ModelImmutableContractBlob:
        key = (commit_sha, contract_path)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        self._validate_ancestral_commit(commit_sha)
        blob_oid = _git_text(
            self._git_runner,
            self._repository_root,
            ("rev-parse", "--verify", f"{commit_sha}:{contract_path}"),
        )
        if not SHA1_RE.fullmatch(blob_oid):
            message = f"contract blob for {commit_sha}:{contract_path} is not immutable"
            raise ValueError(message)
        content = _git_blob_bytes(
            self._git_runner,
            self._repository_root,
            blob_oid,
            label=f"contract blob for {commit_sha}:{contract_path}",
            maximum_size=MAX_CONTRACT_BLOB_BYTES,
        )
        loaded = ModelImmutableContractBlob(
            commit_sha=commit_sha,
            contract_path=contract_path,
            blob_oid=blob_oid,
            content=content,
        )
        self._cache[key] = loaded
        return loaded


def _error_for_exact_fields(index: int, entry: dict[str, Any]) -> list[str]:
    present = set(entry)
    missing = REQUIRED_FIELDS - present
    extra = present - REQUIRED_FIELDS - OPTIONAL_FIELDS
    errors: list[str] = []
    if missing:
        errors.append(f"Entry[{index}]: missing required fields: {sorted(missing)}")
    if extra:
        errors.append(f"Entry[{index}]: unexpected fields: {sorted(extra)}")
    return errors


def _error_for_contract_identity(index: int, entry: dict[str, Any]) -> list[str]:
    prefix = f"Entry[{index}]"
    errors: list[str] = []
    if not isinstance(
        entry["authorization_id"], str
    ) or not AUTHORIZATION_ID_RE.fullmatch(entry["authorization_id"]):
        errors.append(f"{prefix}: authorization_id must match action-auth-<uuid4>")
    if not isinstance(entry["ticket_id"], str) or not TICKET_ID_RE.fullmatch(
        entry["ticket_id"]
    ):
        errors.append(f"{prefix}: ticket_id must match OMN-<positive integer>")
    expected_path = f"contracts/{entry['ticket_id']}.yaml"
    if entry["contract_path"] != expected_path:
        errors.append(
            f"{prefix}: contract_path must be exactly {expected_path!r}, got "
            f"{entry['contract_path']!r}"
        )
    for field, pattern, description in (
        ("contract_commit_sha", SHA1_RE, "40 lowercase hex characters"),
        ("source_sha", SHA1_RE, "40 lowercase hex characters"),
        ("contract_sha256", SHA256_RE, "sha256:<64 lowercase hex>"),
        ("artifact_sha256", SHA256_RE, "sha256:<64 lowercase hex>"),
    ):
        value = entry[field]
        if not isinstance(value, str) or not pattern.fullmatch(value):
            errors.append(f"{prefix}: {field} must be {description}, got {value!r}")
    return errors


def _error_for_action_and_target(index: int, entry: dict[str, Any]) -> list[str]:
    prefix = f"Entry[{index}]"
    errors: list[str] = []
    for field, pattern, description in (
        ("action_id", ACTION_ID_RE, "a bounded lowercase action identifier"),
        ("issuer", ISSUER_RE, "a bounded issuer identity"),
        ("nonce", NONCE_RE, "32-128 lowercase hex characters"),
    ):
        value = entry[field]
        if not isinstance(value, str) or not pattern.fullmatch(value):
            errors.append(f"{prefix}: {field} must be {description}, got {value!r}")
    for field in (
        "target_database",
        "target_schema",
        "target_service",
        "target_principal",
    ):
        value = entry[field]
        if not isinstance(value, str) or not IDENTITY_RE.fullmatch(value):
            errors.append(
                f"{prefix}: {field} must be one exact lowercase identity without "
                f"wildcards, got {value!r}"
            )
    if entry["execute_enabled"] is not False:
        errors.append(
            f"{prefix}: execute_enabled must be exactly false for a pre-execution "
            "authorization"
        )
    if entry["one_time_use"] is not True:
        errors.append(f"{prefix}: one_time_use must be exactly true")
    if not isinstance(entry["reason"], str) or not entry["reason"].strip():
        errors.append(f"{prefix}: reason must be a non-empty string")
    elif len(entry["reason"]) > MAX_REASON_LENGTH:
        errors.append(
            f"{prefix}: reason exceeds the {MAX_REASON_LENGTH}-character safety limit"
        )
    return errors


def _error_for_timestamps(
    index: int, entry: dict[str, Any], now: datetime
) -> list[str]:
    prefix = f"Entry[{index}]"
    parsed: dict[str, datetime | None] = {}
    errors: list[str] = []
    for field in ("issued_at", "expires_at"):
        value = entry[field]
        if not isinstance(value, str) or not ISO8601_RE.fullmatch(value):
            errors.append(f"{prefix}: {field} must be an ISO-8601 instant")
            parsed[field] = None
        else:
            parsed[field] = parse_iso8601(value)
    issued_at = parsed.get("issued_at")
    expires_at = parsed.get("expires_at")
    if issued_at is not None and expires_at is not None and expires_at <= issued_at:
        errors.append(f"{prefix}: expires_at must be strictly after issued_at")
    if expires_at is not None and expires_at <= now:
        errors.append(f"{prefix}: authorization is EXPIRED")
    return errors


def _error_for_lifecycle(index: int, entry: dict[str, Any]) -> list[str]:
    prefix = f"Entry[{index}]"
    consumed = entry.get("consumed", False)
    consumed_at = entry.get("consumed_at")
    correlation_id = entry.get("consumed_by_correlation_id")
    errors: list[str] = []
    if not isinstance(consumed, bool):
        errors.append(f"{prefix}: consumed must be a bool when present")
        return errors
    if consumed:
        if not isinstance(consumed_at, str) or not ISO8601_RE.fullmatch(consumed_at):
            errors.append(
                f"{prefix}: consumed authorization requires ISO-8601 consumed_at"
            )
        if not isinstance(correlation_id, str) or not UUID_RE.fullmatch(correlation_id):
            errors.append(
                f"{prefix}: consumed authorization requires UUID "
                "consumed_by_correlation_id"
            )
    elif consumed_at is not None or correlation_id is not None:
        errors.append(
            f"{prefix}: unconsumed authorization may not carry consumption markers"
        )
    return errors


def _error_for_contract_binding(
    index: int,
    entry: dict[str, Any],
    contract_loader: Callable[[str, str], ModelImmutableContractBlob],
) -> tuple[list[str], ModelImmutableContractBlob | None]:
    prefix = f"Entry[{index}]"
    if not isinstance(entry["contract_path"], str):
        return [f"{prefix}: contract_path must be a string"], None
    if not isinstance(entry["contract_commit_sha"], str):
        return [f"{prefix}: contract_commit_sha must be a string"], None
    if not isinstance(entry["contract_sha256"], str):
        return [f"{prefix}: contract_sha256 must be a string"], None
    try:
        contract_blob = contract_loader(
            entry["contract_commit_sha"], entry["contract_path"]
        )
    except ValueError as exc:
        return [f"{prefix}: contract binding failed: {exc}"], None
    actual = f"sha256:{hashlib.sha256(contract_blob.content).hexdigest()}"
    if actual != entry["contract_sha256"]:
        return (
            [
                f"{prefix}: contract_sha256 mismatch for "
                f"{entry['contract_commit_sha']}:{entry['contract_path']}"
            ],
            None,
        )
    return [], contract_blob


def _validate_entry(
    index: int,
    entry: Any,
    *,
    now: datetime,
    contract_loader: Callable[[str, str], ModelImmutableContractBlob],
) -> tuple[list[str], ModelImmutableContractBlob | None]:
    if not isinstance(entry, dict):
        return [f"Entry[{index}]: must be a mapping"], None
    errors = _error_for_exact_fields(index, entry)
    if errors:
        return errors, None
    errors = [
        *_error_for_contract_identity(index, entry),
        *_error_for_action_and_target(index, entry),
        *_error_for_timestamps(index, entry, now),
        *_error_for_lifecycle(index, entry),
    ]
    # Do not ask the loader to read an invalid path/ref shape. The entry is
    # already rejected, and this avoids turning a useful validation finding
    # into an exception from an injected loader.
    if not errors:
        binding_errors, contract_blob = _error_for_contract_binding(
            index, entry, contract_loader
        )
        errors.extend(binding_errors)
        return errors, contract_blob
    return errors, None


def _entry_string(entry: dict[str, Any], field: str) -> str:
    """Return an already-validated primitive without exposing a mutable mapping."""
    value = entry[field]
    if not isinstance(value, str):  # defensive: validation has already checked this
        message = f"validated entry field {field!r} was not a string"
        raise TypeError(message)
    return value


def _entry_bool(entry: dict[str, Any], field: str, *, default: bool = False) -> bool:
    value = entry.get(field, default)
    if not isinstance(value, bool):  # defensive: validation has already checked this
        message = f"validated entry field {field!r} was not a bool"
        raise TypeError(message)
    return value


def _optional_entry_string(entry: dict[str, Any], field: str) -> str | None:
    value = entry.get(field)
    if value is None:
        return None
    if not isinstance(value, str):  # defensive: lifecycle validation checked it
        message = f"validated entry field {field!r} was not a string or null"
        raise TypeError(message)
    return value


def _immutable_projection(
    entry: dict[str, Any],
    snapshot: ModelImmutableOCCSnapshot,
    contract_blob: ModelImmutableContractBlob,
) -> ModelValidatedPreExecutionAuthorization:
    """Project one validated mapping into immutable consumer-facing data."""
    return ModelValidatedPreExecutionAuthorization(
        snapshot=snapshot,
        contract_blob_oid=contract_blob.blob_oid,
        authorization_id=_entry_string(entry, "authorization_id"),
        ticket_id=_entry_string(entry, "ticket_id"),
        contract_path=_entry_string(entry, "contract_path"),
        contract_commit_sha=_entry_string(entry, "contract_commit_sha"),
        contract_sha256=_entry_string(entry, "contract_sha256"),
        action_id=_entry_string(entry, "action_id"),
        source_sha=_entry_string(entry, "source_sha"),
        artifact_sha256=_entry_string(entry, "artifact_sha256"),
        target_database=_entry_string(entry, "target_database"),
        target_schema=_entry_string(entry, "target_schema"),
        target_service=_entry_string(entry, "target_service"),
        target_principal=_entry_string(entry, "target_principal"),
        execute_enabled=_entry_bool(entry, "execute_enabled"),
        issuer=_entry_string(entry, "issuer"),
        nonce=_entry_string(entry, "nonce"),
        issued_at=_entry_string(entry, "issued_at"),
        expires_at=_entry_string(entry, "expires_at"),
        one_time_use=_entry_bool(entry, "one_time_use"),
        reason=_entry_string(entry, "reason"),
        consumed=_entry_bool(entry, "consumed"),
        consumed_at=_optional_entry_string(entry, "consumed_at"),
        consumed_by_correlation_id=_optional_entry_string(
            entry, "consumed_by_correlation_id"
        ),
    )


def _validate_snapshot_entries(
    entries: list[Any],
    snapshot: ModelImmutableOCCSnapshot,
    *,
    now: datetime,
    git_runner: GitRunner,
) -> tuple[list[str], tuple[ModelValidatedPreExecutionAuthorization, ...]]:
    """Validate snapshot entries and construct projections only after success."""
    errors: list[str] = []
    projections: list[ModelValidatedPreExecutionAuthorization] = []
    authorization_ids: set[str] = set()
    nonces: set[str] = set()
    contract_loader = _ImmutableContractLoader(snapshot, git_runner)
    for index, entry in enumerate(entries):
        entry_errors, contract_blob = _validate_entry(
            index, entry, now=now, contract_loader=contract_loader
        )
        errors.extend(entry_errors)
        if not isinstance(entry, dict):
            continue
        for field, seen in (
            ("authorization_id", authorization_ids),
            ("nonce", nonces),
        ):
            value = entry.get(field)
            if isinstance(value, str):
                if value in seen:
                    errors.append(f"Entry[{index}]: duplicate {field} {value!r}")
                seen.add(value)
        if not entry_errors and contract_blob is not None:
            projections.append(_immutable_projection(entry, snapshot, contract_blob))
    return errors, tuple(projections)


def _entry_bounds_errors(entries: list[Any]) -> list[str]:
    """Reject oversized free text across the whole registry before Git reads."""
    errors: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        reason = entry.get("reason")
        if isinstance(reason, str) and len(reason) > MAX_REASON_LENGTH:
            errors.append(
                f"Entry[{index}]: reason exceeds the {MAX_REASON_LENGTH}-character "
                "safety limit"
            )
    return errors


def _snapshot_head_error(
    snapshot: ModelImmutableOCCSnapshot, git_runner: GitRunner
) -> str | None:
    """Detect any HEAD change after all immutable objects have been resolved."""
    try:
        final_head = _git_text(
            git_runner,
            snapshot.repository_root,
            ("rev-parse", "--verify", "HEAD^{commit}"),
        )
    except ValueError as exc:
        return f"immutable OCC snapshot final HEAD check failed: {exc}"
    if final_head != snapshot.head_sha:
        return (
            "immutable OCC snapshot HEAD changed during validation: "
            f"{snapshot.head_sha} -> {final_head}"
        )
    return None


def validate_pre_execution_authorizations(
    file_path: Path,
    *,
    repository_root: Path | None = None,
    now: datetime | None = None,
    git_runner: GitRunner = _run_git,
) -> ModelPreExecutionAuthorizationValidationResult:
    """Validate one canonical registry from immutable OCC Git objects only.

    ``file_path`` is an admission boundary, not an input source: it must name
    the canonical registry beneath the identified OCC root, and its mutable
    worktree bytes are never read.  The returned records carry the immutable
    snapshot that authorized them so a consumer cannot accidentally treat a
    later worktree read as equivalent evidence.
    """
    try:
        identified_root = _identified_repository_root(
            repository_root or _repository_root(), git_runner
        )
        _canonical_registry_path(identified_root, file_path)
        snapshot = _capture_snapshot(identified_root, git_runner)
        data = yaml.safe_load(snapshot.registry_bytes)
    except (ValueError, yaml.YAMLError) as exc:
        return ModelPreExecutionAuthorizationValidationResult(
            passed=False,
            errors=[f"Cannot capture immutable registry snapshot: {exc}"],
            entry_count=0,
        )
    if not isinstance(data, dict) or set(data) != {"entries"}:
        return ModelPreExecutionAuthorizationValidationResult(
            passed=False,
            errors=[
                f"{CANONICAL_REGISTRY_PATH.as_posix()} in immutable snapshot "
                "must contain exactly one top-level key: entries"
            ],
            entry_count=0,
            snapshot=snapshot,
        )
    entries = data["entries"]
    if not isinstance(entries, list):
        return ModelPreExecutionAuthorizationValidationResult(
            passed=False,
            errors=[f"{CANONICAL_REGISTRY_PATH.as_posix()} entries must be a list"],
            entry_count=0,
            snapshot=snapshot,
        )
    if len(entries) > MAX_AUTHORIZATION_COUNT:
        return ModelPreExecutionAuthorizationValidationResult(
            passed=False,
            errors=[
                f"{CANONICAL_REGISTRY_PATH.as_posix()} has {len(entries)} entries; "
                f"the safety limit is {MAX_AUTHORIZATION_COUNT}"
            ],
            entry_count=len(entries),
            snapshot=snapshot,
        )
    bounds_errors = _entry_bounds_errors(entries)
    if bounds_errors:
        return ModelPreExecutionAuthorizationValidationResult(
            passed=False,
            errors=bounds_errors,
            entry_count=len(entries),
            snapshot=snapshot,
        )
    errors, projections = _validate_snapshot_entries(
        entries, snapshot, now=now or datetime.now(UTC), git_runner=git_runner
    )
    head_error = _snapshot_head_error(snapshot, git_runner)
    if head_error is not None:
        errors.append(head_error)
    return ModelPreExecutionAuthorizationValidationResult(
        passed=not errors,
        errors=errors,
        entry_count=len(entries),
        snapshot=snapshot,
        records=projections if not errors else (),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the canonical registry validator."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--file",
        default=CANONICAL_REGISTRY_PATH.as_posix(),
        help=(
            "Must be the canonical in-repository pre-execution authorization "
            "registry path. Other paths are rejected."
        ),
    )
    parser.add_argument(
        "--repository-root",
        default=None,
        help="Identified OCC checkout root; defaults to this installed checkout.",
    )
    args = parser.parse_args(argv)
    result = validate_pre_execution_authorizations(
        Path(args.file),
        repository_root=Path(args.repository_root) if args.repository_root else None,
    )
    if not result.passed:
        print(f"FAIL: {args.file} has {len(result.errors)} violation(s):")
        for error in result.errors:
            print(f"  - {error}")
        return 1
    print(f"PASS: {args.file} — {result.entry_count} authorization(s) validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
