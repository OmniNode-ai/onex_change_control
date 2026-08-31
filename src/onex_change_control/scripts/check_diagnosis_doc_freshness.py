# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Check diagnosis/audit docs for freshness metadata drift."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from onex_change_control.scripts._governance_checks import (
    collect_diagnosis_docs,
    evaluate_diagnosis_doc_freshness,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate diagnosis/audit doc freshness metadata.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Explicit diagnosis/audit files or directories to scan.",
    )
    parser.add_argument(
        "--docs-root",
        default="docs",
        help="Fallback docs root to scan when no explicit paths are provided.",
    )
    parser.add_argument(
        "--now",
        help="ISO-8601 override for the current time (useful for tests).",
    )
    parser.add_argument(
        "--soft",
        action="store_true",
        help="Advisory mode: emit warnings but exit 0.",
    )
    return parser


def _parse_reference_time(raw_value: str | None) -> datetime:
    if raw_value is None:
        return datetime.now(tz=UTC)
    normalized = raw_value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    reference_time = _parse_reference_time(args.now)
    doc_paths = collect_diagnosis_docs(paths=list(args.paths), docs_root=args.docs_root)
    report = evaluate_diagnosis_doc_freshness(
        doc_paths,
        reference_time=reference_time,
    )
    has_failures = bool(report["stale_docs"])
    report["mode"] = "soft" if args.soft else "strict"
    report["status"] = (
        "warning" if has_failures and args.soft else "fail" if has_failures else "pass"
    )
    print(json.dumps(report, indent=2, sort_keys=True))

    if has_failures and not args.soft:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
