# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Check whether plan topics are represented in the integration map."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from onex_change_control.scripts._governance_checks import (
    evaluate_integration_map_freshness,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate that plan topics are present in the integration map.",
    )
    parser.add_argument(
        "--map",
        dest="map_path",
        required=True,
        help="Path to the integration map markdown file.",
    )
    parser.add_argument(
        "plan_files",
        nargs="+",
        help="One or more plan markdown files to validate.",
    )
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="Warn-only mode: emit JSON but exit 0.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = evaluate_integration_map_freshness(
        map_path=Path(args.map_path),
        plan_paths=[Path(path) for path in args.plan_files],
    )
    has_failure = bool(report["missing_topics"])
    report["mode"] = "advisory" if args.advisory else "strict"
    report["status"] = (
        "warning"
        if has_failure and args.advisory
        else "fail"
        if has_failure
        else "pass"
    )
    print(json.dumps(report, indent=2, sort_keys=True))

    if has_failure and not args.advisory:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
