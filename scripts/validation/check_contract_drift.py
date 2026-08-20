#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OmniNode Team
"""check_contract_drift.py -- Detect contract/runtime topic mapping drift.

Computes a deterministic SHA-256 hash of all ``published_events`` and
``event_bus`` sections across all ``contract.yaml`` files under a root
directory.  If the hash changes between a saved snapshot and a fresh scan,
the runtime container was likely built from a stale contract set.

Usage:
    # Print the current hash
    python3 scripts/validation/check_contract_drift.py --root /path/to/nodes --print

    # Save a snapshot
    python3 scripts/validation/check_contract_drift.py --root /path/to/nodes --snapshot drift.sha256

    # Check against a snapshot (exit 1 if drifted)
    python3 scripts/validation/check_contract_drift.py --root /path/to/nodes --check drift.sha256

Exit 0 = clean (or snapshot written).  Exit 1 = drift detected.  Exit 2 = usage error.

OMN-5162
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml


def _extract_hashable_sections(contract_path: Path) -> dict[str, object] | None:
    """Extract published_events and event_bus from a contract, if present."""
    with contract_path.open() as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        return None

    sections: dict[str, object] = {}

    published = data.get("published_events")
    if published:
        sections["published_events"] = published

    event_bus = data.get("event_bus")
    if event_bus:
        sections["event_bus"] = event_bus

    if not sections:
        return None

    return sections


def compute_contracts_hash(root: Path) -> str:
    """Compute deterministic SHA-256 hash of all event-related contract sections.

    Contracts are sorted by relative path. Sections are serialized to JSON with
    sorted keys to ensure deterministic output regardless of YAML key ordering.

    Args:
        root: Root directory to scan for contract.yaml files.

    Returns:
        Hex-encoded SHA-256 hash string.
    """
    hasher = hashlib.sha256()
    contracts_found = 0

    for contract_path in sorted(root.rglob("contract.yaml")):
        sections = _extract_hashable_sections(contract_path)
        if sections is None:
            continue

        contracts_found += 1
        rel_path = str(contract_path.relative_to(root))
        # Include the path in the hash to detect file moves/renames
        entry = {
            "path": rel_path,
            "sections": sections,
        }
        hasher.update(json.dumps(entry, sort_keys=True, default=str).encode())

    if contracts_found == 0:
        return hashlib.sha256(b"<empty>").hexdigest()

    return hasher.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect contract/runtime topic mapping drift."
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Root directory containing nodes with contract.yaml files",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--print",
        action="store_true",
        dest="print_hash",
        help="Print the current hash to stdout",
    )
    group.add_argument(
        "--snapshot",
        type=Path,
        help="Save the current hash to a file",
    )
    group.add_argument(
        "--check",
        type=Path,
        help="Compare current hash to a saved snapshot file",
    )

    args = parser.parse_args()

    root: Path = args.root.resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        sys.exit(2)

    current_hash = compute_contracts_hash(root)

    if args.print_hash:
        print(current_hash)

    elif args.snapshot:
        args.snapshot.write_text(current_hash + "\n")
        print(f"Snapshot written to {args.snapshot}: {current_hash}")

    elif args.check:
        if not args.check.exists():
            print(f"ERROR: snapshot file {args.check} not found", file=sys.stderr)
            sys.exit(2)

        saved_hash = args.check.read_text().strip()
        if current_hash == saved_hash:
            print(f"OK: contract hash matches snapshot ({current_hash[:12]}...)")
        else:
            print(
                f"DRIFT DETECTED:\n"
                f"  snapshot: {saved_hash}\n"
                f"  current:  {current_hash}\n"
                f"\n"
                f"Contract event sections have changed since the snapshot was taken.\n"
                f"Rebuild the runtime container or update the snapshot."
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
