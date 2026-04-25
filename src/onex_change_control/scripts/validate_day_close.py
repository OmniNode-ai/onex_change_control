# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Validate a day-close YAML file against ModelDayClose.

CLI entry point for external consumers (e.g., close-day in omniclaude) that
need to validate day-close artifacts without managing PYTHONPATH manually.

Usage::

    # Via uv run from the onex_change_control repo
    uv run validate-day-close drift/day_close/2026-03-19.yaml

    # Via uv run from any directory (with --project)
    uv run --project /path/to/onex_change_control validate-day-close file.yaml

    # Validate from stdin
    echo '...' | uv run validate-day-close -
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from onex_change_control.models import ModelDayClose


def main(argv: list[str] | None = None) -> int:
    """Validate day-close YAML file(s) against ModelDayClose."""
    parser = argparse.ArgumentParser(
        description="Validate day-close YAML against ModelDayClose schema",
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="YAML file path(s) to validate. Use '-' for stdin.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress success messages; only print errors.",
    )
    args = parser.parse_args(argv)

    errors = 0
    for file_path in args.files:
        if file_path == "-":
            label = "<stdin>"
            try:
                data = yaml.safe_load(sys.stdin)
            except yaml.YAMLError as exc:
                print(f"ERROR: {label}: YAML parse error: {exc}", file=sys.stderr)
                errors += 1
                continue
        else:
            label = file_path
            path = Path(file_path)
            if not path.exists():
                print(f"ERROR: {label}: file not found", file=sys.stderr)
                errors += 1
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                print(f"ERROR: {label}: YAML parse error: {exc}", file=sys.stderr)
                errors += 1
                continue

        if not isinstance(data, dict):
            print(
                f"ERROR: {label}: expected a YAML mapping, got {type(data).__name__}",
                file=sys.stderr,
            )
            errors += 1
            continue

        try:
            ModelDayClose.model_validate(data)
            if not args.quiet:
                print(f"OK: {label}")
        except Exception as exc:  # noqa: BLE001  Why: validation harness must catch all errors per-file
            print(f"ERROR: {label}: validation failed: {exc}", file=sys.stderr)
            errors += 1

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
