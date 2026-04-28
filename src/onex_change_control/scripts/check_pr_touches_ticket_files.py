# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Check whether a PR touches ticket-required files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from onex_change_control.scripts._governance_checks import (
    evaluate_ticket_file_intersection,
    load_pr_files,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate that a PR diff intersects required ticket paths.",
    )
    parser.add_argument(
        "--ticket",
        required=True,
        help="Ticket identifier such as OMN-10175.",
    )
    description_group = parser.add_mutually_exclusive_group(required=True)
    description_group.add_argument(
        "--ticket-description",
        help="Raw ticket description text.",
    )
    description_group.add_argument(
        "--ticket-description-file",
        help="Path to a file containing the ticket description.",
    )
    parser.add_argument(
        "--pr-files-file",
        help="Path to JSON or newline-delimited PR file list.",
    )
    parser.add_argument(
        "--pr-file",
        action="append",
        default=[],
        help="Repeatable PR file path override.",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Warn-only mode: emit JSON but exit 0.",
    )
    return parser


def _read_ticket_description(args: argparse.Namespace) -> str:
    if args.ticket_description is not None:
        return args.ticket_description
    if args.ticket_description_file is None:
        return ""
    return Path(args.ticket_description_file).read_text(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    description = _read_ticket_description(args)
    pr_files = list(args.pr_file)
    if args.pr_files_file:
        file_payload = Path(args.pr_files_file).read_text(encoding="utf-8")
        pr_files.extend(load_pr_files(file_payload))

    report = evaluate_ticket_file_intersection(
        ticket_id=args.ticket,
        description=description,
        pr_files=sorted(set(pr_files)),
    )
    has_failure = report["status"] == "fail"
    report["mode"] = "advisory" if args.advisory else "strict"
    if has_failure and args.advisory:
        report["status"] = "warning"

    print(json.dumps(report, indent=2, sort_keys=True))

    if has_failure and not args.advisory:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
