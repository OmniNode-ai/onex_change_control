#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OmniNode Team
"""check_published_events_structure.py -- Validate published_events in contract.yaml.

Usage:
    python3 scripts/validation/check_published_events_structure.py --root /path/to/nodes

Checks every contract.yaml found recursively under --root for structural issues
in the ``published_events`` section:

  1. No duplicate ``event_type`` values within a single contract
  2. Every entry has both ``topic`` and ``event_type`` fields
  3. Topic strings match ONEX 5-segment format (onex.<kind>.<producer>.<name>.v<N>)
  4. event_type values are PascalCase identifiers

Exit 0 = all valid.  Exit 1 = violations found.  Exit 2 = usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

# ONEX 5-segment topic format: onex.<kind>.<producer>.<event-name>.v<N>
_RE_ONEX_TOPIC = re.compile(r"^onex\.(evt|cmd|intent)\.[a-z0-9-]+\.[a-z0-9._-]+\.v\d+$")

# PascalCase: starts with uppercase, at least two chars, only alphanumeric
_RE_PASCAL_CASE = re.compile(r"^[A-Z][a-zA-Z0-9]+$")


def check_contract(path: Path) -> list[str]:
    """Validate the published_events section of a single contract.yaml.

    Returns a list of violation messages (empty if clean).
    """
    with path.open() as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        return []

    published = data.get("published_events")
    if not published:
        return []

    if not isinstance(published, list):
        return [
            f"{path}: published_events must be a list, got {type(published).__name__}"
        ]

    violations: list[str] = []
    seen_event_types: dict[str, int] = {}

    for idx, entry in enumerate(published):
        prefix = f"{path}:published_events[{idx}]"

        if not isinstance(entry, dict):
            violations.append(
                f"{prefix}: entry must be a mapping, got {type(entry).__name__}"
            )
            continue

        # Check required fields
        topic = entry.get("topic")
        event_type = entry.get("event_type")

        if topic is None:
            violations.append(f"{prefix}: missing required field 'topic'")
        elif not isinstance(topic, str):
            violations.append(f"{prefix}: 'topic' must be a string")
        elif not _RE_ONEX_TOPIC.match(topic):
            violations.append(
                f"{prefix}: topic '{topic}' does not match ONEX 5-segment format "
                f"(onex.<kind>.<producer>.<name>.v<N>)"
            )

        if event_type is None:
            violations.append(f"{prefix}: missing required field 'event_type'")
        elif not isinstance(event_type, str):
            violations.append(f"{prefix}: 'event_type' must be a string")
        else:
            # Check PascalCase
            if not _RE_PASCAL_CASE.match(event_type):
                violations.append(
                    f"{prefix}: event_type '{event_type}' is not PascalCase"
                )

            # Check duplicates
            if event_type in seen_event_types:
                first_idx = seen_event_types[event_type]
                violations.append(
                    f"{prefix}: duplicate event_type '{event_type}' "
                    f"(first seen at index {first_idx})"
                )
            else:
                seen_event_types[event_type] = idx

    return violations


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate published_events structure in contract.yaml files."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Root directory to scan recursively for contract.yaml files",
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Explicit contract.yaml file paths (used by pre-commit)",
    )
    args = parser.parse_args()

    # Collect contract paths: either from --root (recursive scan) or explicit files
    contract_paths: list[Path] = []
    display_root: Path | None = None

    if args.root:
        display_root = args.root.resolve()
        if not display_root.is_dir():
            print(f"ERROR: {display_root} is not a directory", file=sys.stderr)
            sys.exit(2)
        contract_paths = sorted(display_root.rglob("contract.yaml"))
    elif args.files:
        contract_paths = [p.resolve() for p in args.files if p.name == "contract.yaml"]
    else:
        print("ERROR: provide --root or explicit file paths", file=sys.stderr)
        sys.exit(2)

    total_violations = 0
    contracts_checked = 0

    for contract_path in contract_paths:
        if not contract_path.exists():
            continue
        violations = check_contract(contract_path)
        contracts_checked += 1

        label = (
            str(contract_path.relative_to(display_root))
            if display_root
            else str(contract_path)
        )

        if violations:
            total_violations += len(violations)
            print(f"\n{label} -- {len(violations)} violation(s):")
            for v in violations:
                print(f"  - {v}")
        else:
            # Only print OK for contracts that actually have published_events
            with contract_path.open() as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict) and data.get("published_events"):
                print(f"  {label} -- OK")

    print(f"\n{'=' * 60}")
    print(f"Contracts checked: {contracts_checked}")
    print(f"Total violations: {total_violations}")
    print(f"{'=' * 60}")

    if total_violations > 0:
        sys.exit(1)

    print("All published_events entries are structurally valid.")


if __name__ == "__main__":
    main()
