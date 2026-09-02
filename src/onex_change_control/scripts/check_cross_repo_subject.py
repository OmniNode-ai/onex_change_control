# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Offline and online CLI gate for ``occ-cross-repo-subject/v1`` receipts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, cast

import yaml

from onex_change_control.validation.cross_repo_subject import (
    MAX_ARTIFACT_BYTES,
    MAX_CONTRACT_BYTES,
    MAX_HTTP_BODY_BYTES,
    CrossRepoStatus,
    CrossRepoSubjectResolver,
    validate_offline,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate explicit cross-repository receipt subjects. Offline mode "
            "never contacts GitHub and reports valid subjects as UNEVALUATED."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--online", action="store_true")
    parser.add_argument(
        "--all-receipts",
        action="store_true",
        help="In online mode, scan all canonical receipt YAML files.",
    )
    parser.add_argument(
        "--artifact-file",
        type=Path,
        help="Explicit artifact bytes to verify in online mode.",
    )
    parser.add_argument("paths", nargs="*", type=Path)
    return parser


def _receipt_paths(args: argparse.Namespace) -> list[Path]:
    if args.all_receipts:
        receipt_root = Path("drift/dod_receipts")
        return sorted({*receipt_root.rglob("*.yaml"), *receipt_root.rglob("*.yml")})
    return cast("list[Path]", args.paths)


def _load_mapping(path: Path) -> dict[str, Any] | None:
    raw_bytes = _read_bounded(path, MAX_HTTP_BODY_BYTES)
    if raw_bytes is None:
        print(f"{path}: unable to read YAML", file=sys.stderr)
        return None
    if len(raw_bytes) > MAX_HTTP_BODY_BYTES:
        print(
            f"{path}: receipt YAML exceeds the {MAX_HTTP_BODY_BYTES}-byte bound",
            file=sys.stderr,
        )
        return None
    try:
        raw = yaml.safe_load(raw_bytes)
    except yaml.YAMLError as exc:
        print(f"{path}: unable to read YAML: {exc}", file=sys.stderr)
        return None
    if not isinstance(raw, dict):
        print(f"{path}: receipt YAML must contain a mapping", file=sys.stderr)
        return None
    if ".supersede." in path.name:
        # Tombstones intentionally carry no replacement receipt. They have no
        # cross-repo subject to validate and must remain inert in this hook.
        if raw.get("tombstone") is True and "replacement" not in raw:
            return raw
        candidate = raw.get("replacement")
    else:
        candidate = raw
    if not isinstance(candidate, dict):
        print(f"{path}: receipt replacement must contain a mapping", file=sys.stderr)
        return None
    return candidate


def _local_contract_bytes(subject: dict[str, Any]) -> bytes | None:
    source = subject.get("contract_source")
    if not isinstance(source, dict) or not isinstance(source.get("path"), str):
        return None
    path = Path(source["path"])
    return _read_bounded(path, MAX_CONTRACT_BYTES)


def _read_bounded(path: Path, limit: int) -> bytes | None:
    try:
        with path.open("rb") as stream:
            return stream.read(limit + 1)
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:  # noqa: C901, PLR0912
    args = _parser().parse_args(argv)
    paths = _receipt_paths(args)
    if not paths:
        print("No receipt paths supplied; cross-repo subject gate is clean.")
        return 0
    artifact_bytes: bytes | None = None
    if args.artifact_file is not None:
        artifact_bytes = _read_bounded(args.artifact_file, MAX_ARTIFACT_BYTES)
        if artifact_bytes is None:
            print(f"{args.artifact_file}: unable to read artifact", file=sys.stderr)
            return 1
    failed = False
    unevaluated = 0
    for path in paths:
        raw = _load_mapping(path)
        if raw is None:
            failed = True
            continue
        subject = raw.get("cross_repo_subject")
        if subject is None:
            continue
        if not isinstance(subject, dict):
            print(f"{path}: cross_repo_subject must contain a mapping", file=sys.stderr)
            failed = True
            continue
        evidence_item_id = raw.get("evidence_item_id")
        if not isinstance(evidence_item_id, str) or not evidence_item_id:
            print(
                f"{path}: evidence_item_id is required for cross-repo validation",
                file=sys.stderr,
            )
            failed = True
            continue
        if args.offline:
            result = validate_offline(
                subject,
                evidence_item_id=evidence_item_id,
                contract_bytes=_local_contract_bytes(subject),
                artifact_bytes=artifact_bytes,
            )
        else:
            if artifact_bytes is None:
                print(
                    f"{path}: --artifact-file is required for online validation",
                    file=sys.stderr,
                )
                failed = True
                continue
            result = CrossRepoSubjectResolver().resolve(
                subject,
                evidence_item_id=evidence_item_id,
                artifact_bytes=artifact_bytes,
            )
        print(f"{path}: {result.status.value}")
        for detail in result.details:
            print(f"  {detail}")
        if result.status is CrossRepoStatus.FAIL:
            failed = True
        elif result.status is CrossRepoStatus.UNEVALUATED:
            unevaluated += 1
    if failed:
        return 1
    if args.offline and unevaluated:
        print(
            f"{unevaluated} cross-repo subject(s) are UNEVALUATED; "
            "offline validation cannot satisfy online merge eligibility."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
