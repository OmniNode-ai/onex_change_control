# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
# ruff: noqa: C901, EM101, EM102, TRY003, TRY004
"""Run bounded offline or online validation for cross-repository receipts.

Offline mode is deliberately useful for pre-commit shape and digest checks,
but it reports ``UNEVALUATED`` for a valid subject: only the immutable GitHub
snapshot consumed by online CI can produce ``PASS``.  Online mode uses the
same resolver instance for the invocation so the API-call and deadline bounds
cover the complete receipt batch.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from omnibase_core.models.contracts.ticket.model_dod_receipt import ModelDodReceipt

from onex_change_control.validation.cross_repo_subject import (
    MAX_RECEIPT_AGGREGATE_BYTES,
    MAX_RECEIPT_BYTES,
    MAX_RECEIPT_COUNT,
    CanonicalSubjectUnavailable,
    CrossRepoStatus,
    CrossRepoSubjectResolver,
    ParsedCrossRepoReceipt,
    parse_canonical_receipt,
    validate_offline,
)

_SUPERSEDE_TOKEN_RE = re.compile(r"\.supersede\.(\d+)")
_RECEIPT_SUFFIXES = {".yaml", ".yml"}


@dataclass(frozen=True, slots=True)
class _ReceiptFile:
    path: Path
    raw: dict[str, object]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate canonical cross-repository receipt subjects."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--offline",
        action="store_true",
        help="validate local shape/digests and return UNEVALUATED for valid subjects",
    )
    modes.add_argument(
        "--online",
        action="store_true",
        help="resolve subjects against the bounded immutable GitHub snapshot",
    )
    parser.add_argument(
        "--all-receipts",
        action="store_true",
        help="scan drift/dod_receipts beneath the current repository",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="receipt YAML paths (default: drift/dod_receipts when omitted)",
    )
    return parser


def _paths(args: argparse.Namespace) -> list[Path]:
    if args.all_receipts or not args.paths:
        root = Path("drift/dod_receipts")
        if not root.is_dir():
            return []
        paths = sorted(
            path for suffix in _RECEIPT_SUFFIXES for path in root.rglob(f"*{suffix}")
        )
    else:
        paths = [Path(value) for value in args.paths]
    if len(paths) > MAX_RECEIPT_COUNT:
        raise ValueError("receipt input exceeds the bounded file-count limit")
    return paths


def _load_files(paths: list[Path]) -> list[_ReceiptFile]:
    loaded: list[_ReceiptFile] = []
    total = 0
    for path in paths:
        if path.suffix.lower() not in _RECEIPT_SUFFIXES:
            continue
        if path.name.startswith(".env") or ".env" in path.name:
            raise ValueError("environment files are not receipt inputs")
        if path.is_symlink():
            raise ValueError(f"symlink receipt input is not permitted: {path}")
        if not path.is_file():
            raise ValueError(f"receipt input is not a regular file: {path}")
        data = path.read_bytes()
        if len(data) > MAX_RECEIPT_BYTES:
            raise ValueError(f"receipt exceeds the bounded size: {path}")
        total += len(data)
        if total > MAX_RECEIPT_AGGREGATE_BYTES:
            raise ValueError("receipt inputs exceed the aggregate size limit")
        parsed = yaml.safe_load(data)
        if not isinstance(parsed, dict):
            raise ValueError(f"receipt must contain a mapping: {path}")
        loaded.append(_ReceiptFile(path, parsed))
    return loaded


def _supersession_target(path: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    return path.parent / Path(value).name


def _supersession_token(path: Path) -> int | None:
    match = _SUPERSEDE_TOKEN_RE.search(path.name)
    return int(match.group(1)) if match is not None else None


def _effective_files(files: list[_ReceiptFile]) -> list[_ReceiptFile]:
    """Select the highest direct supersession without mutating base receipts."""

    by_path = {item.path: item for item in files}
    replacements: dict[Path, tuple[int, _ReceiptFile]] = {}
    for item in files:
        token = _supersession_token(item.path)
        target = _supersession_target(item.path, item.raw.get("supersedes"))
        replacement = item.raw.get("replacement")
        if token is None or target is None or not isinstance(replacement, dict):
            continue
        current = replacements.get(target)
        if current is None or token > current[0]:
            replacements[target] = (token, item)
    effective: list[_ReceiptFile] = []
    emitted: set[Path] = set()
    for item in files:
        active = replacements.get(item.path)
        if active is not None:
            continue
        if ".supersede." in item.path.name:
            replacement = item.raw.get("replacement")
            if isinstance(replacement, dict):
                effective.append(_ReceiptFile(item.path, replacement))
                emitted.add(item.path)
            continue
        effective.append(item)
    for target, (_, item) in replacements.items():
        if target not in by_path and item.path not in emitted:
            replacement = item.raw.get("replacement")
            if isinstance(replacement, dict):
                effective.append(_ReceiptFile(item.path, replacement))
    return effective


def _subject_raw(raw: dict[str, object]) -> object | None:
    return raw.get("cross_repo_subject")


def _validate_offline_receipt_envelope(raw: dict[str, object]) -> None:
    """Validate receipt bindings even when the Core subject model is absent."""

    if raw.get("schema_version") != "2.0.0" or raw.get("status") != "PASS":
        raise ValueError("cross-repository receipt must be a PASS schema 2.0.0 receipt")
    if raw.get("pr_number") is not None:
        raise ValueError("cross-repository receipt must use nested PR identity")
    base = dict(raw)
    base.pop("cross_repo_subject", None)
    try:
        receipt = ModelDodReceipt.model_validate(base)
    except ValueError as exc:
        raise ValueError(f"invalid cross-repository receipt envelope: {exc}") from exc
    subject = raw.get("cross_repo_subject")
    if not isinstance(subject, dict):
        raise ValueError("cross_repo_subject must be a mapping")
    revision = subject.get("revision")
    contract = subject.get("contract_source")
    if not isinstance(revision, dict) or not isinstance(contract, dict):
        raise ValueError("cross_repo_subject is missing revision or contract source")
    if receipt.commit_sha != revision.get("sha"):
        raise ValueError("receipt commit_sha must equal subject revision.sha")
    if receipt.contract_sha256 != contract.get("file_sha256"):
        raise ValueError("receipt contract_sha256 must equal subject file digest")
    if receipt.contract_entry_sha256 != contract.get("entry_sha256"):
        raise ValueError(
            "receipt contract_entry_sha256 must equal subject entry digest"
        )


def _check_offline(item: _ReceiptFile) -> tuple[CrossRepoStatus, tuple[str, ...]]:
    subject = _subject_raw(item.raw)
    if subject is None:
        return CrossRepoStatus.PASS, ()
    try:
        parsed = parse_canonical_receipt(item.raw)
    except CanonicalSubjectUnavailable:
        _validate_offline_receipt_envelope(item.raw)
    except (ValueError, TypeError) as exc:
        return CrossRepoStatus.FAIL, (str(exc),)
    else:
        if isinstance(parsed, ParsedCrossRepoReceipt):
            subject = parsed.subject
    result = validate_offline(subject)
    return result.status, result.details


def _check_online(
    item: _ReceiptFile, resolver: CrossRepoSubjectResolver
) -> tuple[CrossRepoStatus, tuple[str, ...]]:
    if _subject_raw(item.raw) is None:
        return CrossRepoStatus.PASS, ()
    result = resolver.resolve_receipt(item.raw)
    return result.status, result.details


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.offline and not args.online:
        args.offline = True
    try:
        files = _effective_files(_load_files(_paths(args)))
        resolver = CrossRepoSubjectResolver() if args.online else None
        if resolver is not None:
            resolver.start_batch()
        failures = 0
        subjects = 0
        for item in files:
            if _subject_raw(item.raw) is None:
                continue
            subjects += 1
            if resolver is None:
                status, details = _check_offline(item)
            else:
                status, details = _check_online(item, resolver)
            print(f"{status.value} {item.path}")
            for detail in details:
                print(f"  {detail}")
            if status is CrossRepoStatus.FAIL:
                failures += 1
        if subjects == 0:
            print("PASS no cross-repository receipt subjects found")
        if failures:
            return 1
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"FAIL cross-repository receipt preflight: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
