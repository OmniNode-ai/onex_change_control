#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OmniNode Team
"""check_seam_contract_coverage.py -- Enforce contract existence for seam tickets.

Detects when a branch modifies interface-surface files (Kafka emitters, Pydantic
models) without a corresponding contract YAML in contracts/<ticket_id>.yaml.

Exit 0 = contract exists or no seam files changed.
Exit 1 = seam files changed but no contract found.
Exit 2 = usage error or git failure.

Usage:
    # Run against the current branch vs origin/main (default)
    python3 scripts/validation/check_seam_contract_coverage.py

    # Specify base branch explicitly
    python3 scripts/validation/check_seam_contract_coverage.py --base origin/main

    # Warn-only (CI info, no failure)
    python3 scripts/validation/check_seam_contract_coverage.py --warn-only

OMN-5388
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Files touching these path patterns are considered seam surface changes.
SEAM_PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^src/onex_change_control/kafka/"),
    re.compile(r"^src/onex_change_control/models/model_.*\.py$"),
    re.compile(r"^src/onex_change_control/enums/enum_interface"),
]

# Branch name patterns for extracting a ticket ID.
TICKET_PATTERN = re.compile(r"(OMN-\d+)", re.IGNORECASE)


def _get_current_branch() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True, check=False,
    )
    if result.returncode != 0:
        print(f"[ERROR] Could not determine current branch: {result.stderr.strip()}")
        sys.exit(2)
    return result.stdout.strip()


def _get_changed_files(base: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        capture_output=True,
        text=True, check=False,
    )
    if result.returncode != 0:
        # Fall back to comparing against HEAD~1 (e.g. on main push)
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1"],
            capture_output=True,
            text=True, check=False,
        )
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


def _seam_files_changed(files: list[str]) -> list[str]:
    return [f for f in files if any(p.search(f) for p in SEAM_PATH_PATTERNS)]


def _extract_ticket_id(branch: str) -> str | None:
    match = TICKET_PATTERN.search(branch)
    if match:
        return match.group(1).upper()
    return None


def _contract_exists(ticket_id: str, contracts_dir: Path) -> bool:
    return (contracts_dir / f"{ticket_id}.yaml").exists()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default="origin/main",
        help="Base ref to diff against (default: origin/main)",
    )
    parser.add_argument(
        "--contracts-dir",
        default="contracts",
        help="Path to contracts directory (default: contracts/)",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Print warning but exit 0 even if contract is missing",
    )
    args = parser.parse_args()

    contracts_dir = Path(args.contracts_dir)
    branch = _get_current_branch()
    changed = _get_changed_files(args.base)
    seam_changed = _seam_files_changed(changed)

    if not seam_changed:
        print("[OK] No interface-surface files changed — no contract required.")
        return 0

    print(f"[INFO] Seam files changed on branch '{branch}':")
    for f in seam_changed:
        print(f"  {f}")

    ticket_id = _extract_ticket_id(branch)
    if not ticket_id:
        msg = (
            f"[WARN] Cannot extract ticket ID from branch '{branch}'. "
            "Rename branch to include OMN-XXXX to enable contract enforcement."
        )
        print(msg)
        # No ticket ID = can't check contract; warn but don't block.
        return 0

    if _contract_exists(ticket_id, contracts_dir):
        print(f"[OK] Contract found: contracts/{ticket_id}.yaml")
        return 0

    msg = (
        f"\n[FAIL] Seam ticket {ticket_id} is missing a contract.\n"
        f"  Interface-surface files were modified but contracts/{ticket_id}.yaml "
        f"does not exist.\n\n"
        f"  To fix: create contracts/{ticket_id}.yaml using the template at\n"
        f"  templates/ticket_contract.template.yaml and set is_seam_ticket: true.\n\n"
        f"  Validate with: uv run validate-yaml contracts/{ticket_id}.yaml\n"
    )
    print(msg)
    return 0 if args.warn_only else 1


if __name__ == "__main__":
    sys.exit(main())
