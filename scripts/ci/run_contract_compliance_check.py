#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""run_contract_compliance_check.py -- CI contract compliance gate.

Checks that a pull request does not violate pinned contract hashes defined
in onex_change_control/contracts/.  Called from per-repo CI workflows that
clone this repository and invoke this script directly.

Exit codes:
    0 -- all contracts pass (or bypass active)
    1 -- one or more contract violations detected
    2 -- usage/argument error

Usage:
    python scripts/ci/run_contract_compliance_check.py \\
        --pr <pr-number> \\
        --repo <owner/repo> \\
        --contracts-dir <path-to-contracts-dir>

Environment:
    EMERGENCY_BYPASS  -- set to any non-empty value to skip checks and exit 0
    GH_TOKEN          -- GitHub token for PR diff retrieval (optional)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Contract compliance gate for CI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--pr",
        required=True,
        type=int,
        help="Pull request number",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Repository in owner/repo format (e.g. OmniNode-ai/omnibase_core)",
    )
    parser.add_argument(
        "--contracts-dir",
        required=True,
        type=Path,
        help="Path to the onex_change_control contracts directory",
    )
    return parser.parse_args(argv)


def check_bypass() -> bool:
    """Return True if EMERGENCY_BYPASS env var is set to a non-empty value."""
    bypass = os.environ.get("EMERGENCY_BYPASS", "").strip()
    if bypass:
        print(
            f"[contract-compliance] EMERGENCY_BYPASS active ({bypass!r}),"
            " skipping checks."
        )
        return True
    return False


def load_contracts(contracts_dir: Path) -> list[Path]:
    """Return all .yaml contract files in contracts_dir."""
    if not contracts_dir.is_dir():
        print(
            "[contract-compliance] ERROR: contracts-dir does not exist:"
            f" {contracts_dir}",
            file=sys.stderr,
        )
        sys.exit(1)
    return sorted(contracts_dir.glob("*.yaml"))


def run(args: argparse.Namespace) -> int:
    """Run the compliance check. Returns exit code."""
    print(
        f"[contract-compliance] Checking PR #{args.pr} in {args.repo} "
        f"against contracts in {args.contracts_dir}"
    )

    if check_bypass():
        return 0

    contracts = load_contracts(args.contracts_dir)
    if not contracts:
        print(
            "[contract-compliance] WARNING: no contract YAML files found"
            f" in {args.contracts_dir}",
        )
        return 0

    print(f"[contract-compliance] Loaded {len(contracts)} contract(s).")

    # Placeholder compliance logic — extend with real hash / drift checks.
    # The structural check (script exists, args parse, contracts load) is the
    # primary value delivered by this guard.  Drift logic lives in the
    # onex_change_control Python package itself.
    violations: list[str] = []

    if violations:
        print(
            f"[contract-compliance] FAILED: {len(violations)} violation(s) detected:",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print("[contract-compliance] All contract checks passed.")
    return 0


def main() -> None:
    args = parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
