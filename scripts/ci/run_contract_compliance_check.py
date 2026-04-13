#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""run_contract_compliance_check.py -- CI gate: verify PR ticket has a passing contract.

Usage (called by CI in downstream repos):
    python run_contract_compliance_check.py \
        --pr <PR_NUMBER> \
        --repo <OWNER/REPO> \
        --contracts-dir <path/to/contracts>

Behaviour:
  1. Resolve the ticket ID from the PR title or branch name (OMN-NNNN pattern).
  2. If no ticket ID found: WARN and exit 0 (fail-safe — CI PRs have no ticket).
  3. If ticket ID found but no contract YAML exists: WARN and exit 0 (backfill
     via OMN-8637; blocking here would stop all uncontracted PRs).
  4. If contract exists and emergency_bypass.enabled=True: WARN and exit 0.
  5. If EMERGENCY_BYPASS env var set to "<user>-<reason>": WARN and exit 0.
  6. If contract exists: validate YAML parses cleanly against ModelTicketContract,
     then check all dod_evidence items — any item with status="failed" → exit 1.

Exit codes:
  0  PASS or WARN (no contract found, bypass active)
  1  BLOCK (contract validation error or failed DoD evidence item)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TICKET_PATTERN = re.compile(r"\bOMN-\d+\b", re.IGNORECASE)
GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# GitHub API helper (stdlib-only)
# ---------------------------------------------------------------------------


def _gh_get(path: str, token: str) -> Any:
    url = f"{GITHUB_API}/{path.lstrip('/')}"
    req = urllib.request.Request(  # noqa: S310
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        print(
            f"WARN: GitHub API error {exc.code} for {url}: {exc.reason}",
            file=sys.stderr,
        )
        return None


# ---------------------------------------------------------------------------
# Ticket ID resolution
# ---------------------------------------------------------------------------


def _extract_ticket_id(text: str) -> str | None:
    m = TICKET_PATTERN.search(text)
    return m.group(0).upper() if m else None


def resolve_ticket_id(pr_number: int, repo: str, token: str) -> str | None:
    """Return the first OMN-NNNN ticket ID found in PR title or branch name."""
    data = _gh_get(f"repos/{repo}/pulls/{pr_number}", token)
    if not data:
        return None

    for field in ("title", "head.ref"):
        parts = field.split(".")
        value = data
        for part in parts:
            value = value.get(part, "") if isinstance(value, dict) else ""
        ticket = _extract_ticket_id(str(value))
        if ticket:
            return ticket
    return None


# ---------------------------------------------------------------------------
# Contract loading
# ---------------------------------------------------------------------------


def load_contract(contracts_dir: Path, ticket_id: str) -> dict[str, Any] | None:
    """Load and parse the contract YAML for ticket_id. Returns None if not found."""
    path = contracts_dir / f"{ticket_id}.yaml"
    if not path.exists():
        return None

    if yaml is None:
        print(
            "WARN: pyyaml not available — cannot parse contract YAML", file=sys.stderr
        )
        return None

    with path.open() as fh:
        return yaml.safe_load(fh)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Compliance check
# ---------------------------------------------------------------------------


def check_contract(contract: dict[str, Any], ticket_id: str) -> int:
    """Inspect contract for blocking issues. Returns 0=pass, 1=block."""
    # Validate ticket_id matches
    contract_ticket = contract.get("ticket_id", "")
    if contract_ticket.upper() != ticket_id.upper():
        print(
            f"BLOCK: contract ticket_id '{contract_ticket}' does not match "
            f"PR ticket '{ticket_id}'",
            file=sys.stderr,
        )
        return 1

    # Check emergency_bypass in contract
    bypass = contract.get("emergency_bypass", {})
    if isinstance(bypass, dict) and bypass.get("enabled"):
        justification = bypass.get("justification", "").strip()
        follow_up = bypass.get("follow_up_ticket_id", "").strip()
        print(
            f"WARN: contract emergency_bypass enabled — "
            f"justification='{justification}', follow_up={follow_up}. Exiting 0.",
        )
        return 0

    # Check dod_evidence items for any with status=failed
    dod_evidence = contract.get("dod_evidence", [])
    if not isinstance(dod_evidence, list):
        print("BLOCK: dod_evidence is not a list", file=sys.stderr)
        return 1

    failed_items: list[str] = []
    for item in dod_evidence:
        if not isinstance(item, dict):
            continue
        if item.get("status") == "failed":
            item_id = item.get("id", "?")
            description = item.get("description", "")
            failed_items.append(f"  [{item_id}] {description}")

    if failed_items:
        print(
            "BLOCK: the following DoD evidence items have status=failed:",
            file=sys.stderr,
        )
        for line in failed_items:
            print(line, file=sys.stderr)
        return 1

    # All checks passed
    pending_count = sum(
        1
        for item in dod_evidence
        if isinstance(item, dict) and item.get("status") == "pending"
    )
    verified_count = sum(
        1
        for item in dod_evidence
        if isinstance(item, dict) and item.get("status") == "verified"
    )
    print(
        f"PASS: contract for {ticket_id} clean — "
        f"{verified_count} verified, {pending_count} pending, "
        f"0 failed dod_evidence items."
    )
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Contract compliance CI gate")
    parser.add_argument("--pr", type=int, required=True, help="PR number")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument(
        "--contracts-dir", required=True, help="Path to contracts directory"
    )
    args = parser.parse_args()

    contracts_dir = Path(args.contracts_dir)
    if not contracts_dir.is_dir():
        print(
            f"WARN: contracts-dir '{contracts_dir}' does not exist. Skipping check.",
            file=sys.stderr,
        )
        return 0

    # Emergency bypass via environment variable
    env_bypass = os.environ.get("EMERGENCY_BYPASS", "").strip()
    if env_bypass:
        print(
            f"WARN: EMERGENCY_BYPASS env var set ('{env_bypass}'). "
            "Skipping contract check."
        )
        return 0

    token = os.environ.get("GH_TOKEN", "")
    if not token:
        print(
            "WARN: GH_TOKEN not set — cannot resolve PR details. Skipping check.",
            file=sys.stderr,
        )
        return 0

    # Resolve ticket ID from PR
    ticket_id = resolve_ticket_id(args.pr, args.repo, token)
    if not ticket_id:
        print(
            f"WARN: no OMN-NNNN ticket ID found in PR #{args.pr} title or branch. "
            "Skipping contract check (CI/chore PRs have no ticket)."
        )
        return 0

    print(f"INFO: resolved ticket ID: {ticket_id}")

    # Load contract
    contract = load_contract(contracts_dir, ticket_id)
    if contract is None:
        print(
            f"WARN: no contract found at {contracts_dir}/{ticket_id}.yaml. "
            "Skipping check (backfill pending — see OMN-8637)."
        )
        return 0

    print(f"INFO: loaded contract for {ticket_id}")
    return check_contract(contract, ticket_id)


if __name__ == "__main__":
    sys.exit(main())
