# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
# ruff: noqa: C901, EM101, EM102, PLR0912, PLR2004, TRY003, TRY301

"""Fail-closed gate for canonical cross-repository receipt subjects.

The online lane consumes only effective receipts from the append-only receipt
store. A v2 receipt with a nested v1 metadata subject is parsed in full before
any GitHub request; the resolver then obtains the immutable evidence artifact
itself from the exact repository, canonical top-level commit SHA, and bounded
path recorded in that receipt.
"""

from __future__ import annotations

import re
import stat
import sys
from pathlib import Path
from typing import Any, cast

import yaml
from omnibase_core.validation.validator_receipt_supersession import (
    resolve_supersession,
)

from onex_change_control.validation.cross_repo_subject import (
    MAX_HTTP_BODY_BYTES,
    MAX_RECEIPT_AGGREGATE_BYTES,
    MAX_RECEIPT_BYTES,
    MAX_RECEIPT_COUNT,
    CrossRepoReceipt,
    CrossRepoReceiptSupersession,
    CrossRepoStatus,
    CrossRepoSubjectResolver,
    parse_canonical_receipt,
)

_RECEIPT_ROOT = Path("drift/dod_receipts")
_SUPERSEDE_RE = re.compile(r"^(?P<check>[^./]+)\.supersede\.(?P<suffix>[^./]+)\.ya?ml$")


def _parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Validate canonical cross-repository receipt subjects. Offline mode "
            "never contacts GitHub and reports v2 subjects as UNEVALUATED."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--online", action="store_true")
    parser.add_argument(
        "--all-receipts",
        action="store_true",
        help="In online mode, scan all effective canonical receipt YAML files.",
    )
    parser.add_argument("paths", nargs="*", type=Path)
    return parser


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _assert_no_symlink_components(path: Path, root: Path | None = None) -> None:
    """Reject symlink files and parent components before opening a path."""

    absolute = path.absolute()
    stop = root.absolute() if root is not None else absolute.anchor
    current = absolute
    while True:
        try:
            info = current.lstat()
        except OSError:
            pass
        else:
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f"symlink path component is not allowed: {path}")
        if str(current) == str(stop) or current.parent == current:
            break
        current = current.parent


def _read_bounded(path: Path, limit: int, *, root: Path | None = None) -> bytes | None:
    try:
        if root is not None:
            absolute_root = root.absolute()
            absolute_path = path.absolute()
            if not _is_within(absolute_path, absolute_root):
                raise ValueError(f"path escapes the receipt root: {path}")
            _assert_no_symlink_components(absolute_path, absolute_root)
        else:
            _assert_no_symlink_components(path)
        try:
            info = path.lstat()
        except OSError as exc:
            raise ValueError(f"receipt path could not be inspected: {path}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"receipt path is not a regular file: {path}")
        with path.open("rb") as stream:
            return stream.read(limit + 1)
    except (OSError, ValueError):
        return None


def _validate_yaml_budget(value: object) -> None:
    nodes = 0
    seen: set[int] = set()
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_RECEIPT_COUNT:
            raise ValueError("receipt YAML object count exceeds the bound")
        if depth > 64:
            raise ValueError("receipt YAML nesting depth exceeds the bound")
        if isinstance(item, str) and len(item.encode("utf-8")) > MAX_HTTP_BODY_BYTES:
            raise ValueError("receipt YAML contains an oversized scalar")
        if isinstance(item, dict):
            object_id = id(item)
            if object_id in seen:
                continue
            seen.add(object_id)
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            object_id = id(item)
            if object_id in seen:
                continue
            seen.add(object_id)
            stack.extend((child, depth + 1) for child in item)


def _load_raw(path: Path, *, root: Path | None = None) -> dict[str, Any] | None:
    raw_bytes = _read_bounded(path, MAX_RECEIPT_BYTES, root=root)
    if raw_bytes is None or len(raw_bytes) > MAX_RECEIPT_BYTES:
        return None
    try:
        raw = yaml.safe_load(raw_bytes)
        _validate_yaml_budget(raw)
    except (yaml.YAMLError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _load_mapping(path: Path) -> dict[str, Any] | None:
    """Load a receipt or its replacement while preserving tombstone records."""

    raw = _load_raw(path)
    if raw is None:
        print(f"{path}: unable to read bounded YAML", file=sys.stderr)
        return None
    if ".supersede." in path.name:
        if raw.get("tombstone") is True and "replacement" not in raw:
            return raw
        candidate = raw.get("replacement")
    else:
        candidate = raw
    if not isinstance(candidate, dict):
        print(f"{path}: receipt replacement must contain a mapping", file=sys.stderr)
        return None
    return candidate


def _bounded_receipt_paths(root: Path) -> list[Path]:
    """Enumerate bounded regular files without following symlink components."""

    if not root.exists():
        return []
    _assert_no_symlink_components(root)
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode):
        raise ValueError(f"receipt root is not a directory: {root}")
    paths: list[Path] = []
    aggregate = 0
    for candidate in sorted(root.rglob("*")):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"receipt tree contains a symlink: {candidate}")
        if not stat.S_ISREG(info.st_mode):
            continue
        if candidate.suffix not in {".yaml", ".yml"}:
            continue
        if info.st_size > MAX_RECEIPT_BYTES:
            raise ValueError(
                f"receipt exceeds {MAX_RECEIPT_BYTES}-byte bound: {candidate}"
            )
        aggregate += info.st_size
        if aggregate > MAX_RECEIPT_AGGREGATE_BYTES:
            raise ValueError("receipt tree exceeds aggregate byte bound")
        paths.append(candidate)
        if len(paths) > MAX_RECEIPT_COUNT:
            raise ValueError("receipt tree exceeds file-count bound")
    return paths


def _receipt_paths(args: Any) -> list[Path]:
    root = _RECEIPT_ROOT
    if args.all_receipts:
        return _bounded_receipt_paths(root)
    paths = cast("list[Path]", args.paths)
    absolute_root = root.absolute()
    validated: list[Path] = []
    aggregate = 0
    for path in paths:
        if not _is_within(path.absolute(), absolute_root):
            raise ValueError(f"receipt path escapes canonical root: {path}")
        _assert_no_symlink_components(path, absolute_root)
        try:
            info = path.lstat()
        except OSError as exc:
            raise ValueError(f"receipt path could not be inspected: {path}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"receipt path is not a regular file: {path}")
        if info.st_size > MAX_RECEIPT_BYTES:
            raise ValueError(f"receipt exceeds {MAX_RECEIPT_BYTES}-byte bound: {path}")
        aggregate += info.st_size
        if aggregate > MAX_RECEIPT_AGGREGATE_BYTES:
            raise ValueError("receipt paths exceed aggregate byte bound")
        validated.append(path)
        if len(validated) > MAX_RECEIPT_COUNT:
            raise ValueError("receipt paths exceed file-count bound")
    return validated


def _receipt_key(path: Path) -> tuple[str, str, str] | None:
    relative = path.resolve(strict=False).relative_to(
        _RECEIPT_ROOT.resolve(strict=False)
    )
    if len(relative.parts) != 3:
        return None
    ticket, item, filename = relative.parts
    match = _SUPERSEDE_RE.fullmatch(filename)
    if match is not None:
        return ticket, item, match.group("check")
    if filename.endswith((".yaml", ".yml")):
        return ticket, item, Path(filename).stem
    return None


def _effective_receipts(paths: list[Path]) -> list[tuple[Path, dict[str, Any]]]:
    """Resolve every key first, then parse only its active receipt bytes."""

    keys: set[tuple[str, str, str]] = set()
    for path in paths:
        try:
            with path.open("rb") as stream:
                raw_bytes = stream.read(MAX_RECEIPT_BYTES + 1)
        except OSError as exc:
            raise ValueError(f"receipt could not be read: {path}") from exc
        if len(raw_bytes) > MAX_RECEIPT_BYTES:
            raise ValueError(f"receipt exceeds {MAX_RECEIPT_BYTES}-byte bound: {path}")
        # This is only a candidate index. No receipt is parsed or validated
        # until its append-only key has been resolved below; in particular a
        # superseded base is never treated as an active receipt.
        if b"cross_repo_subject" not in raw_bytes and b"2.0.0" not in raw_bytes:
            continue
        key = _receipt_key(path)
        if key is not None:
            keys.add(key)
    effective: list[tuple[Path, dict[str, Any]]] = []
    for ticket, item, check in sorted(keys):
        _validate_supersession_candidates(ticket, item, check)
        resolution = resolve_supersession(_RECEIPT_ROOT, ticket, item, check)
        if resolution is not None:
            if resolution.error is not None:
                # The canonical core resolver predates the v2 extension and
                # therefore rejects a v2 replacement as an unknown field.
                # Keep using it for all ordinary keys; this narrow adapter
                # applies the same highest-numeric-record rule to v2 only.
                cross_source = _v2_supersession_source(ticket, item, check)
                if cross_source is None:
                    raise ValueError(resolution.error)
                source = cross_source
                raw = _load_mapping(source)
                if raw is None:
                    raise ValueError(f"effective receipt could not be loaded: {source}")
                effective.append((source, raw))
                continue
            if resolution.tombstoned or resolution.receipt is None:
                continue
            source = resolution.source_path
            raw = _load_mapping(source)
        else:
            source = _RECEIPT_ROOT / ticket / item / f"{check}.yaml"
            if not source.exists():
                source = _RECEIPT_ROOT / ticket / item / f"{check}.yml"
            raw = _load_mapping(source)
        if raw is None:
            raise ValueError(f"effective receipt could not be loaded: {source}")
        effective.append((source, raw))
    return effective


def _validate_supersession_candidates(ticket: str, item: str, check: str) -> None:
    """Bound and reject links in records the canonical resolver may inspect."""

    key_dir = _RECEIPT_ROOT / ticket / item
    if not key_dir.exists():
        return
    _assert_no_symlink_components(key_dir, _RECEIPT_ROOT)
    for candidate in key_dir.glob(f"{check}.supersede.*.yaml"):
        _assert_no_symlink_components(candidate, _RECEIPT_ROOT)
        try:
            candidate_info = candidate.lstat()
        except OSError as exc:
            raise ValueError(
                f"supersession record could not be inspected: {candidate}"
            ) from exc
        if not stat.S_ISREG(candidate_info.st_mode):
            raise ValueError(f"supersession record is not a regular file: {candidate}")
        if candidate_info.st_size > MAX_RECEIPT_BYTES:
            raise ValueError(
                f"receipt exceeds {MAX_RECEIPT_BYTES}-byte bound: {candidate}"
            )


def _v2_supersession_source(ticket: str, item: str, check: str) -> Path | None:
    """Adapt the core supersession resolver for a v2 replacement receipt."""

    key_dir = _RECEIPT_ROOT / ticket / item
    candidates = sorted(key_dir.glob(f"{check}.supersede.*.yaml"))
    numeric = [
        candidate
        for candidate in candidates
        if (match := _SUPERSEDE_RE.fullmatch(candidate.name)) is not None
        and match.group("suffix").isdigit()
    ]
    if not numeric:
        return None
    winner = max(numeric, key=_numeric_supersession_suffix)
    raw = _load_raw(winner, root=_RECEIPT_ROOT)
    if raw is None or raw.get("tombstone") is True:
        return None
    replacement = raw.get("replacement")
    if (
        not isinstance(replacement, dict)
        or replacement.get("schema_version") != "2.0.0"
    ):
        return None
    expected_target = (
        Path("drift") / "dod_receipts" / ticket / item / f"{check}.yaml"
    ).as_posix()
    if raw.get("supersedes") != expected_target:
        return None
    try:
        CrossRepoReceiptSupersession.model_validate(raw)
    except Exception as exc:
        raise ValueError(f"v2 supersession record is invalid: {exc}") from exc
    return winner


def _numeric_supersession_suffix(path: Path) -> int:
    match = _SUPERSEDE_RE.fullmatch(path.name)
    if match is None or not match.group("suffix").isdigit():
        return -1
    return int(match.group("suffix"))


def _print_result(
    path: Path, status: CrossRepoStatus, details: tuple[str, ...]
) -> None:
    print(f"{path}: {status.value}")
    for detail in details:
        print(f"  {detail}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        paths = _receipt_paths(args)
        effective = _effective_receipts(paths)
    except ValueError as exc:
        print(f"receipt enumeration failed closed: {exc}", file=sys.stderr)
        return 1
    if not paths:
        print("No receipt paths supplied; no cross-repo subject was evaluated.")
        return 0

    resolver = CrossRepoSubjectResolver()
    if args.online:
        resolver.start_batch()
    failed = False
    unevaluated = 0
    subjects = 0
    for path, raw in effective:
        if "cross_repo_subject" not in raw and raw.get("schema_version") != "2.0.0":
            continue
        subjects += 1
        try:
            receipt = parse_canonical_receipt(raw)
        except (ValueError, yaml.YAMLError) as exc:
            _print_result(
                path,
                CrossRepoStatus.FAIL,
                (f"invalid canonical receipt: {exc}",),
            )
            failed = True
            continue
        if not isinstance(receipt, CrossRepoReceipt):
            _print_result(
                path,
                CrossRepoStatus.FAIL,
                ("cross_repo_subject is forbidden on v1/same-repository receipts",),
            )
            failed = True
            continue
        if not args.online:
            _print_result(
                path,
                CrossRepoStatus.UNEVALUATED,
                ("cross-repo subject requires the online GitHub snapshot",),
            )
            unevaluated += 1
            continue
        result = resolver.resolve_receipt(receipt)
        _print_result(path, result.status, result.details)
        if result.status is CrossRepoStatus.FAIL:
            failed = True
        elif result.status is CrossRepoStatus.UNEVALUATED:
            unevaluated += 1

    if failed:
        return 1
    if args.online and unevaluated:
        print(
            f"{unevaluated} cross-repo subject(s) are UNEVALUATED; online CI "
            "cannot establish merge eligibility.",
            file=sys.stderr,
        )
        return 1
    if args.offline and unevaluated:
        print(
            f"{unevaluated} cross-repo subject(s) are UNEVALUATED; offline "
            "validation cannot satisfy online merge eligibility."
        )
    if args.online and subjects == 0:
        print("No cross-repo subjects found among effective receipts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
