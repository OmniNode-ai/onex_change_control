#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""check_dod_compliance.py -- CI DoD compliance audit for recently completed tickets.

Usage:
    python3 scripts/check_dod_compliance.py \
        --contracts-dir contracts/ \
        --exemptions dod_sweep_exemptions.yaml \
        --since-days 7

Checks recently completed Linear tickets for DoD artifact compliance:
  1. Contract exists (contracts/{ticket_id}.yaml)
  2. Evidence receipt exists (.evidence/{ticket_id}/dod_report.json)
  3. Receipt is clean (zero failures)

Scope honesty:
  This CI script is a bounded recent-ticket artifact audit, not a complete
  DoD truth source. Clean results mean no detected artifact gaps within the
  discovered scope, not proof that all recently completed work is fully
  compliant.

  Checked: contract existence, evidence receipt existence, receipt cleanliness.
  NOT checked: PR merge linkage, CI success linkage, targeted receipt freshness,
  integration sweep coverage, full DoD closure semantics.

Exit 0 = all pass or degraded mode.  Exit 1 = any fail.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Linear GraphQL client (stdlib-only)
# ---------------------------------------------------------------------------

LINEAR_API_URL = "https://linear.app/graphql"

_TICKETS_QUERY = """
query CompletedTickets($after: String, $filter: IssueFilter!) {
  issues(first: 50, after: $after, filter: $filter) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      identifier
      title
      completedAt
    }
  }
}
"""


def _linear_request(
    query: str,
    variables: dict[str, object],
    api_key: str,
) -> dict[str, Any]:
    """Execute a Linear GraphQL query using stdlib urllib."""
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(  # noqa: S310 -- URL is a constant HTTPS endpoint
        LINEAR_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode())  # type: ignore[no-any-return]


def fetch_completed_tickets(
    api_key: str,
    since_date: str,
) -> list[dict[str, str]]:
    """Fetch tickets completed since the given ISO date.

    Paginates through all Linear API results.
    """
    tickets: list[dict[str, str]] = []
    cursor: str | None = None

    while True:
        variables: dict[str, object] = {
            "filter": {
                "completedAt": {"gte": since_date},
            },
        }
        if cursor:
            variables["after"] = cursor

        result = _linear_request(_TICKETS_QUERY, variables, api_key)
        data: dict[str, Any] = result.get("data", {})
        issues: dict[str, Any] = data.get("issues", {})
        nodes: list[dict[str, Any]] = issues.get("nodes", [])

        for node in nodes:
            tickets.append(
                {
                    "id": node["identifier"],
                    "title": node["title"],
                    "completed_at": node.get("completedAt", ""),
                }
            )

        page_info: dict[str, Any] = issues.get("pageInfo", {})
        if page_info.get("hasNextPage"):
            cursor = page_info.get("endCursor")
        else:
            break

    return tickets


# ---------------------------------------------------------------------------
# Exemption loading
# ---------------------------------------------------------------------------


def load_exemptions(
    exemptions_path: Path,
) -> tuple[str | None, set[str]]:
    """Load exemptions YAML and return (cutoff_date, set of exempt ticket IDs).

    Returns (None, empty set) if file doesn't exist or yaml not available.
    """
    if yaml is None:
        print("WARNING: pyyaml not installed -- exemption loading disabled")
        return None, set()

    if not exemptions_path.exists():
        return None, set()

    with exemptions_path.open() as f:
        data = yaml.safe_load(f)

    if not data:
        return None, set()

    cutoff_date = data.get("cutoff_date")
    exemptions_list = data.get("exemptions", []) or []

    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    exempt_ids: set[str] = set()
    for entry in exemptions_list:
        ticket_id = entry.get("ticket_id", "")
        expires_on = entry.get("expires_on")
        # Skip expired exemptions
        if expires_on and expires_on < today:
            continue
        if ticket_id:
            exempt_ids.add(ticket_id)

    return cutoff_date, exempt_ids


def is_exempt(
    ticket_id: str,
    completed_at: str,
    cutoff_date: str | None,
    exempt_ids: set[str],
) -> str | None:
    """Return exemption reason string if ticket is exempt, else None."""
    if ticket_id in exempt_ids:
        return "Explicitly exempted in dod_sweep_exemptions.yaml"
    if cutoff_date and completed_at and completed_at[:10] < cutoff_date:
        return f"Completed before cutoff date {cutoff_date}"
    return None


# ---------------------------------------------------------------------------
# Artifact checks (1, 2, 3)
# ---------------------------------------------------------------------------


def check_contract_exists(ticket_id: str, contracts_dir: Path) -> tuple[str, str]:
    """Check 1: Contract YAML exists. Returns (status, detail)."""
    contract_path = contracts_dir / f"{ticket_id}.yaml"
    if contract_path.exists():
        return "PASS", f"Contract found: {contract_path}"
    return "FAIL", f"No contract at {contract_path}"


def check_receipt_exists(ticket_id: str, contracts_dir: Path) -> tuple[str, str]:
    """Check 2: Evidence receipt exists. Returns (status, detail)."""
    # Primary location: .evidence/{ticket_id}/dod_report.json relative to repo root
    repo_root = contracts_dir.parent
    evidence_path = repo_root / ".evidence" / ticket_id / "dod_report.json"
    if evidence_path.exists():
        return "PASS", f"Receipt found: {evidence_path}"
    return "FAIL", f"No receipt at {evidence_path}"


def check_receipt_clean(ticket_id: str, contracts_dir: Path) -> tuple[str, str]:
    """Check 3: Receipt has zero failures. Returns (status, detail)."""
    repo_root = contracts_dir.parent
    evidence_path = repo_root / ".evidence" / ticket_id / "dod_report.json"
    if not evidence_path.exists():
        return "FAIL", "Cannot check cleanliness -- receipt does not exist"
    try:
        with evidence_path.open() as f:
            receipt = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return "FAIL", f"Cannot parse receipt: {e}"
    else:
        failed = receipt.get("result", {}).get("failed", -1)
        if failed == 0:
            return "PASS", "Receipt is clean (0 failures)"
        if failed == -1:
            return "UNKNOWN", "Receipt exists but 'result.failed' field not found"
        return "FAIL", f"Receipt has {failed} failure(s)"


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------


def format_summary_table(
    results: list[dict[str, object]],
    since_days: int,
    *,
    degraded: bool = False,
) -> str:
    """Format results as a markdown summary table for $GITHUB_STEP_SUMMARY."""
    lines: list[str] = []
    lines.append("## DoD Compliance Audit")
    lines.append("")

    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    exempt = sum(1 for r in results if r["status"] == "EXEMPT")
    unknown = total - passed - failed - exempt

    lines.append(f"Audited **{total}** tickets completed in last **{since_days}** days")
    lines.append(f"- Passed: {passed}")
    lines.append(f"- Failed: {failed}")
    lines.append(f"- Exempt: {exempt}")
    if unknown > 0:
        lines.append(f"- Unknown: {unknown}")
    lines.append("")

    if degraded:
        lines.append(
            "> **DEGRADED**: No LINEAR_API_KEY -- ticket discovery disabled, "
            "artifact-only mode"
        )
        lines.append("")

    lines.append(
        "**Scope**: artifact checks only (contract, receipt, receipt cleanliness)"
    )
    lines.append(
        "**NOT checked**: PR merge linkage, CI success linkage, receipt freshness, "
        "integration sweep coverage"
    )
    lines.append("")

    if results:
        lines.append("| Ticket | Title | Status | Details |")
        lines.append("|--------|-------|--------|---------|")
        for r in results:
            title = str(r.get("title", ""))[:50]
            status = r["status"]
            details = str(r.get("details", ""))[:80]
            lines.append(f"| {r['ticket_id']} | {title} | {status} | {details} |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ticket auditing
# ---------------------------------------------------------------------------


def _audit_ticket(
    ticket: dict[str, str],
    contracts_dir: Path,
    cutoff_date: str | None,
    exempt_ids: set[str],
) -> dict[str, object]:
    """Audit a single ticket for DoD artifact compliance."""
    ticket_id = ticket["id"]
    title = ticket["title"]
    completed_at = ticket.get("completed_at", "")

    # Check exemptions
    exempt_reason = is_exempt(ticket_id, completed_at, cutoff_date, exempt_ids)
    if exempt_reason:
        return {
            "ticket_id": ticket_id,
            "title": title,
            "status": "EXEMPT",
            "details": exempt_reason,
        }

    # Run 3 artifact checks
    check_results: list[tuple[str, str]] = [
        check_contract_exists(ticket_id, contracts_dir),
        check_receipt_exists(ticket_id, contracts_dir),
        check_receipt_clean(ticket_id, contracts_dir),
    ]

    ticket_failed = any(s == "FAIL" for s, _ in check_results)
    if ticket_failed:
        status = "FAIL"
    elif all(s == "PASS" for s, _ in check_results):
        status = "PASS"
    else:
        status = "UNKNOWN"

    details_parts = [f"{s}: {d}" for s, d in check_results if s != "PASS"]
    details = (
        "; ".join(details_parts) if details_parts else "All 3 artifact checks pass"
    )

    return {
        "ticket_id": ticket_id,
        "title": title,
        "status": status,
        "details": details,
    }


def _fetch_tickets(api_key: str, since_date: str) -> tuple[list[dict[str, str]], bool]:
    """Fetch tickets from Linear. Returns (tickets, degraded)."""
    if not api_key:
        print(
            "DEGRADED: No LINEAR_API_KEY -- ticket discovery disabled, "
            "artifact-only mode"
        )
        return [], True

    try:
        tickets = fetch_completed_tickets(api_key, since_date)
    except (urllib.error.URLError, OSError) as e:
        print(f"WARNING: Linear API request failed: {e}")
        print("DEGRADED: Falling back to artifact-only mode")
        return [], True
    else:
        return tickets, False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Run DoD compliance audit. Returns exit code."""
    parser = argparse.ArgumentParser(
        description="CI DoD compliance audit for recently completed tickets"
    )
    parser.add_argument(
        "--contracts-dir",
        type=Path,
        default=Path("contracts"),
        help="Path to contracts directory (default: contracts/)",
    )
    parser.add_argument(
        "--exemptions",
        type=Path,
        default=Path("dod_sweep_exemptions.yaml"),
        help="Path to exemptions YAML (default: dod_sweep_exemptions.yaml)",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=7,
        help="Look-back window in days (default: 7)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("LINEAR_API_KEY", "")
    cutoff_date, exempt_ids = load_exemptions(args.exemptions)

    since_date = (datetime.now(tz=UTC) - timedelta(days=args.since_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    tickets, degraded = _fetch_tickets(api_key, since_date)

    if not tickets:
        summary = format_summary_table([], args.since_days, degraded=degraded)
        print(summary)
        if not degraded:
            print(f"\n0 tickets found completed in last {args.since_days} days")
        return 0

    results = [
        _audit_ticket(t, args.contracts_dir, cutoff_date, exempt_ids) for t in tickets
    ]
    any_fail = any(r["status"] == "FAIL" for r in results)

    summary = format_summary_table(results, args.since_days, degraded=degraded)
    print(summary)

    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
