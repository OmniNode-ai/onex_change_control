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
import warnings
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from onex_change_control.models import ModelTicketContract

_LEGACY_RECEIPT_CUTOFF: date = date(2026, 6, 1)
"""Two-receipt-location reconciliation hard cutoff (OMN-9791, Wave C / Task 11).

Canonical receipt location going forward:
``drift/dod_receipts/<TICKET>/<ITEM_ID>/<run_timestamp>.yaml`` (schema:
``omnibase_core.ModelDodReceipt``).

Legacy receipt location deprecated 2026-04-26 and removed 2026-06-01:
``.evidence/<TICKET>/dod_report.json``.

Before the cutoff the gate accepts both shapes, normalising legacy presence to
PASS-with-warning. On or after the cutoff the gate fails closed when only the
legacy receipt is present. See ``docs/RECEIPT_LOCATIONS.md`` for the migration
path.
"""

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


def _validate_contract_schema(
    contract_path: Path,
) -> tuple[str, str] | None:
    """Validate contract YAML against ModelTicketContract.

    Returns (status, detail) on failure, None on success.
    """
    if yaml is None:
        return None  # Skip schema validation when pyyaml unavailable

    try:
        with contract_path.open() as f:
            data = yaml.safe_load(f)
    except Exception as e:  # noqa: BLE001
        return "FAIL", f"Contract unreadable: {e}"

    if not isinstance(data, dict):
        return "FAIL", f"Contract is not a YAML mapping: {contract_path}"

    try:
        ModelTicketContract.model_validate(data)
    except ValidationError as e:
        errors = e.errors()
        if errors:
            field = ".".join(str(p) for p in errors[0]["loc"])
            msg = errors[0]["msg"]
        else:
            field = ""
            msg = str(e)
        return "FAIL", f"Contract schema invalid — {field}: {msg}"

    return None


def check_contract_exists(ticket_id: str, contracts_dir: Path) -> tuple[str, str]:
    """Check 1: Contract YAML exists and passes ModelTicketContract validation.

    Catches malformed contracts that pass existence check but fail schema
    validation (OMN-8808; motivated by OMN-8606 incident).
    """
    contract_path = contracts_dir / f"{ticket_id}.yaml"
    if not contract_path.exists():
        return "FAIL", f"No contract at {contract_path}"

    schema_result = _validate_contract_schema(contract_path)
    if schema_result is not None:
        return schema_result

    return "PASS", f"Contract valid: {contract_path}"


def check_receipt_exists(
    ticket_id: str,
    contracts_dir: Path,
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Check 2: Evidence receipt exists in canonical or legacy location.

    Reconciles the two known DoD receipt locations (OMN-9791):

    * Canonical: ``drift/dod_receipts/<TICKET>/<ITEM_ID>/<run_timestamp>.yaml``
      (schema: ``ModelDodReceipt``).
    * Legacy:    ``.evidence/<TICKET>/dod_report.json``.

    Behaviour:

    * Canonical present -> ``PASS`` with canonical detail (legacy presence
      is irrelevant once canonical is there).
    * Legacy-only and ``now < _LEGACY_RECEIPT_CUTOFF`` -> ``PASS`` with a
      detail string containing ``DEPRECATED``; a ``DeprecationWarning``
      referencing OMN-9791 is emitted so callers in tooling pipelines can
      capture the signal.
    * Legacy-only and ``now >= _LEGACY_RECEIPT_CUTOFF`` -> ``FAIL`` with a
      detail string referencing OMN-9791 and the cutoff date.
    * Neither present -> ``FAIL``.

    Args:
        ticket_id: Linear ticket identifier.
        contracts_dir: Path to the contracts directory; the repo root is
            inferred as ``contracts_dir.parent``.
        now: Injected wall-clock for deterministic tests. ``None`` resolves
            at call time to ``datetime.now(tz=UTC)``; we never use a
            ``datetime.now`` *default* (per omnibase_core handshake).

    Returns:
        ``(status, detail)`` where ``status`` is one of ``"PASS"`` /
        ``"FAIL"``.
    """
    repo_root = contracts_dir.parent
    canonical_dir = repo_root / "drift" / "dod_receipts" / ticket_id
    legacy_path = repo_root / ".evidence" / ticket_id / "dod_report.json"

    canonical_present = canonical_dir.is_dir() and any(canonical_dir.rglob("*.yaml"))
    legacy_present = legacy_path.is_file()

    if canonical_present:
        return "PASS", f"canonical receipts found: {canonical_dir}"

    if legacy_present:
        effective_now = now if now is not None else datetime.now(tz=UTC)
        cutoff_reached = effective_now.date() >= _LEGACY_RECEIPT_CUTOFF
        if cutoff_reached:
            return (
                "FAIL",
                (
                    f"legacy-only receipt at {legacy_path} rejected: "
                    f"hard cutoff {_LEGACY_RECEIPT_CUTOFF.isoformat()} reached "
                    f"(OMN-9791); migrate to "
                    f"drift/dod_receipts/{ticket_id}/<ITEM_ID>/<run_timestamp>.yaml"
                ),
            )
        warnings.warn(
            (
                f"DEPRECATED legacy DoD receipt at {legacy_path} for {ticket_id}; "
                f"migrate to drift/dod_receipts/{ticket_id}/<ITEM_ID>/<ts>.yaml "
                f"before {_LEGACY_RECEIPT_CUTOFF.isoformat()} (OMN-9791)"
            ),
            DeprecationWarning,
            stacklevel=2,
        )
        return (
            "PASS",
            (
                f"DEPRECATED: only legacy receipt at {legacy_path}; "
                f"migrate to drift/dod_receipts/{ticket_id}/ before "
                f"{_LEGACY_RECEIPT_CUTOFF.isoformat()} (OMN-9791)"
            ),
        )

    return (
        "FAIL",
        f"no receipt at {canonical_dir} or {legacy_path}",
    )


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
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output ModelDodSweepResult as JSON instead of markdown table",
    )
    args = parser.parse_args()

    api_key = os.environ.get("LINEAR_API_KEY", "")

    if args.json:
        from onex_change_control.enums.enum_invariant_status import (
            EnumInvariantStatus,
        )
        from onex_change_control.handlers.handler_dod_sweep import (
            run_dod_sweep,
        )

        result = run_dod_sweep(
            contracts_dir=args.contracts_dir,
            since_days=args.since_days,
            exemptions_path=args.exemptions if args.exemptions.exists() else None,
            api_key=api_key,
        )
        print(result.model_dump_json(indent=2))
        return 1 if result.overall_status == EnumInvariantStatus.FAIL else 0

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
