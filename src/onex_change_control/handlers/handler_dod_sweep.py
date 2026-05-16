# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""DoD sweep handler -- structured handler returning ModelDodSweepResult.

Lifts logic from scripts/check_dod_compliance.py into a handler that returns
typed Pydantic models instead of printing markdown to stdout.

The check functions and Linear API client are inlined here because the
original script (scripts/check_dod_compliance.py) lives outside the
installable package boundary. A future refactor should extract shared
logic into a neutral library module (onex_change_control.sweep or similar)
so both the handler and the script can import from it.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import uuid
import warnings
from datetime import (
    UTC,
    date,
    datetime,
    timedelta,
)
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.enums.enum_dod_sweep_check import EnumDodSweepCheck
from onex_change_control.enums.enum_invariant_status import EnumInvariantStatus
from onex_change_control.models.model_dod_sweep import (
    ModelDodSweepCheckResult,
    ModelDodSweepResult,
    ModelDodSweepTicketResult,
)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]  # Why: optional dep; guarded by `if yaml is None` on L136

_LEGACY_RECEIPT_CUTOFF: date = date(2026, 6, 1)
"""Two-receipt-location reconciliation hard cutoff (OMN-9791, Wave C / Task 11).

Mirrors ``scripts/check_dod_compliance._LEGACY_RECEIPT_CUTOFF``; both call
sites must move together. After this date the legacy
``.evidence/<TICKET>/dod_report.json`` shape is rejected outright. See
``docs/RECEIPT_LOCATIONS.md`` for the migration path.
"""

_CRON_SCRIPT_NAMES = ("cron-closeout.sh", "cron-buildloop.sh")
_INFRA_HOST_PATTERN = re.compile(r"\bINFRA_HOST\b", re.IGNORECASE)
_LOCALHOST_PATTERN = re.compile(r"\b(?:localhost|127\.0\.0\.1)\b", re.IGNORECASE)
_PASS_STATUS_PATTERN = re.compile(r"(?im)^\s*status:\s*[\"']?PASS[\"']?\s*$")
_INFRA_RECEIPT_MARKERS = (
    "infra_consistency",
    "infra consistency",
    "INFRA_CONSISTENCY",
    "check_closeout_phase_compliance",
    "CLOSEOUT PHASE COMPLIANCE",
)

# ---------------------------------------------------------------------------
# Linear GraphQL client (stdlib-only, lifted from scripts/check_dod_compliance.py)
# ---------------------------------------------------------------------------

LINEAR_API_URL = "https://api.linear.app/graphql"

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
    req = urllib.request.Request(  # noqa: S310  Why: URL is a constant HTTPS endpoint
        LINEAR_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310  Why: URL is a constant HTTPS endpoint
        return cast("dict[str, Any]", json.loads(resp.read().decode()))


def fetch_completed_tickets(
    api_key: str,
    since_date: str,
) -> list[dict[str, str]]:
    """Fetch tickets completed since the given ISO date."""
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
                    "completedAt": node.get("completedAt", ""),
                }
            )

        page_info: dict[str, Any] = issues.get("pageInfo", {})
        if page_info.get("hasNextPage"):
            cursor = page_info.get("endCursor")
        else:
            break

    return tickets


# ---------------------------------------------------------------------------
# Exemption loading (lifted from scripts/check_dod_compliance.py)
# ---------------------------------------------------------------------------


def load_exemptions(
    exemptions_path: Path,
) -> tuple[str | None, set[str]]:
    """Load exemptions YAML and return (cutoff_date, set of exempt ticket IDs)."""
    if yaml is None:
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
# Artifact checks (lifted from scripts/check_dod_compliance.py)
# ---------------------------------------------------------------------------


def check_contract_exists(ticket_id: str, contracts_dir: Path) -> tuple[str, str]:
    """Check 1: Contract YAML exists. Returns (status, detail)."""
    contract_path = contracts_dir / f"{ticket_id}.yaml"
    if contract_path.exists():
        return "PASS", f"Contract found: {contract_path}"
    return "FAIL", f"No contract at {contract_path}"


def check_receipt_exists(
    ticket_id: str,
    contracts_dir: Path,
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Check 2: Evidence receipt exists in canonical or legacy location.

    Mirror of ``scripts/check_dod_compliance.check_receipt_exists`` for the
    structured-output (``run_dod_sweep``) path. See that function's docstring
    and ``docs/RECEIPT_LOCATIONS.md`` for the reconciliation contract
    (OMN-9791, hard cutoff ``_LEGACY_RECEIPT_CUTOFF``).

    Args:
        ticket_id: Linear ticket identifier.
        contracts_dir: Path to the contracts directory; the repo root is
            inferred as ``contracts_dir.parent``.
        now: Injected wall-clock for deterministic tests. ``None`` resolves
            at call time to ``datetime.now(tz=UTC)``.

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


def _read_text_if_present(path: Path) -> str:
    """Return UTF-8 text for an optional artifact, or an empty string."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _canonical_receipt_files(ticket_id: str, contracts_dir: Path) -> list[Path]:
    """Return canonical receipt YAML files for a ticket."""
    repo_root = contracts_dir.parent
    canonical_dir = repo_root / "drift" / "dod_receipts" / ticket_id
    if not canonical_dir.is_dir():
        return []
    return sorted(canonical_dir.rglob("*.yaml"))


def _text_mentions_cron_script(text: str) -> bool:
    """Return whether text references a governed cron script."""
    lower_text = text.lower()
    return any(name in lower_text for name in _CRON_SCRIPT_NAMES)


def _ticket_mentions_cron_script(
    ticket_id: str,
    contracts_dir: Path,
    *,
    extra_text: str = "",
) -> bool:
    """Return whether the central contract or receipts reference a cron script."""
    contract_text = _read_text_if_present(contracts_dir / f"{ticket_id}.yaml")
    receipt_text = "\n".join(
        _read_text_if_present(path)
        for path in _canonical_receipt_files(ticket_id, contracts_dir)
    )
    return _text_mentions_cron_script(f"{extra_text}\n{contract_text}\n{receipt_text}")


def _discover_local_cron_scripts(contracts_dir: Path) -> list[Path]:
    """Find cron scripts inside the repo under audit, if present."""
    repo_root = contracts_dir.parent
    candidates: list[Path] = []
    for script_name in _CRON_SCRIPT_NAMES:
        candidates.extend(
            [
                repo_root / "scripts" / script_name,
                repo_root / script_name,
            ]
        )
    return [path for path in candidates if path.is_file()]


def scan_cron_script_infra_consistency(script_path: Path) -> tuple[str, str]:
    """Verify one cron script does not mix INFRA_HOST and localhost references."""
    try:
        content = script_path.read_text(encoding="utf-8")
    except OSError as exc:
        return "FAIL", f"unreadable cron script: {script_path}: {exc}"

    infra_lines: list[int] = []
    localhost_lines: list[int] = []

    for line_number, line in enumerate(content.splitlines(), start=1):
        if _INFRA_HOST_PATTERN.search(line):
            infra_lines.append(line_number)
        if _LOCALHOST_PATTERN.search(line):
            localhost_lines.append(line_number)

    if infra_lines and localhost_lines:
        return (
            "FAIL",
            (
                f"{script_path} mixes INFRA_HOST lines {infra_lines} with "
                f"localhost lines {localhost_lines}"
            ),
        )
    if infra_lines:
        return "PASS", f"{script_path} uses INFRA_HOST consistently"
    if localhost_lines:
        return "PASS", f"{script_path} uses localhost consistently"
    return "FAIL", f"{script_path} has no INFRA_HOST or localhost references"


def _has_infra_consistency_receipt(
    ticket_id: str,
    contracts_dir: Path,
) -> tuple[bool, str]:
    """Check for canonical PASS receipt evidence for cron infra consistency."""
    receipt_files = _canonical_receipt_files(ticket_id, contracts_dir)
    for receipt_file in receipt_files:
        text = _read_text_if_present(receipt_file)
        if not _PASS_STATUS_PATTERN.search(text):
            continue
        if any(marker in text for marker in _INFRA_RECEIPT_MARKERS):
            return True, f"INFRA_CONSISTENCY PASS receipt found: {receipt_file}"
    return (
        False,
        (
            "cron script change requires canonical PASS receipt evidence for "
            "INFRA_CONSISTENCY"
        ),
    )


def check_infra_consistency(
    ticket_id: str,
    contracts_dir: Path,
    *,
    cron_script_paths: list[Path] | None = None,
) -> tuple[str, str] | None:
    """Check cron-script infrastructure consistency and evidence.

    Returns ``None`` when the ticket is not cron-script applicable, so the
    sweep can avoid turning unrelated tickets UNKNOWN solely because a
    cron-only check exists.
    """
    explicit_paths = cron_script_paths or []
    local_paths = explicit_paths or _discover_local_cron_scripts(contracts_dir)
    applies = bool(local_paths) or _ticket_mentions_cron_script(
        ticket_id, contracts_dir
    )
    if not applies:
        return None

    for script_path in local_paths:
        status, detail = scan_cron_script_infra_consistency(script_path)
        if status == "FAIL":
            return status, detail

    has_receipt, receipt_detail = _has_infra_consistency_receipt(
        ticket_id, contracts_dir
    )
    if not has_receipt:
        return "FAIL", receipt_detail

    if local_paths:
        scanned = ", ".join(str(path) for path in local_paths)
        return "PASS", f"{receipt_detail}; scanned {scanned}"
    return "PASS", receipt_detail


# ---------------------------------------------------------------------------
# Status conversion
# ---------------------------------------------------------------------------


def _status_from_tuple(result: tuple[str, str]) -> EnumInvariantStatus:
    """Convert (status_str, detail) tuple to enum."""
    status_str = result[0].upper()
    if status_str == "PASS":
        return EnumInvariantStatus.PASS
    if status_str == "FAIL":
        return EnumInvariantStatus.FAIL
    return EnumInvariantStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Core handler
# ---------------------------------------------------------------------------


def _audit_ticket_structured(
    ticket: dict[str, str],
    contracts_dir: Path,
    cutoff_date: str | None,
    exempt_ids: set[str],
    *,
    now: datetime | None = None,
) -> ModelDodSweepTicketResult:
    """Audit a single ticket and return structured result.

    ``now`` is forwarded to :func:`check_receipt_exists` so the receipt-
    location reconciliation cutoff (OMN-9791) is testable deterministically.
    """
    ticket_id = ticket["id"]
    completed_at = ticket.get("completedAt", "") or ticket.get("completed_at", "")
    cron_script_applicable = _ticket_mentions_cron_script(
        ticket_id,
        contracts_dir,
        extra_text=ticket.get("title", ""),
    )

    exemption_reason = is_exempt(ticket_id, completed_at, cutoff_date, exempt_ids)
    if exemption_reason and not cron_script_applicable:
        return ModelDodSweepTicketResult(
            ticket_id=ticket_id,
            title=ticket.get("title", ""),
            completed_at=completed_at or None,
            checks=[],
            exempted=True,
            exemption_reason=exemption_reason,
        )

    checks: list[ModelDodSweepCheckResult] = []
    contract_status, contract_detail = check_contract_exists(ticket_id, contracts_dir)
    checks.append(
        ModelDodSweepCheckResult(
            check=EnumDodSweepCheck.CONTRACT_EXISTS,
            status=_status_from_tuple((contract_status, contract_detail)),
            detail=contract_detail,
        )
    )
    receipt_status, receipt_detail = check_receipt_exists(
        ticket_id, contracts_dir, now=now
    )
    checks.append(
        ModelDodSweepCheckResult(
            check=EnumDodSweepCheck.RECEIPT_EXISTS,
            status=_status_from_tuple((receipt_status, receipt_detail)),
            detail=receipt_detail,
        )
    )
    clean_status, clean_detail = check_receipt_clean(ticket_id, contracts_dir)
    checks.append(
        ModelDodSweepCheckResult(
            check=EnumDodSweepCheck.RECEIPT_CLEAN,
            status=_status_from_tuple((clean_status, clean_detail)),
            detail=clean_detail,
        )
    )
    infra_consistency_result = check_infra_consistency(
        ticket_id,
        contracts_dir,
    )
    if infra_consistency_result is not None:
        infra_status, infra_detail = infra_consistency_result
        checks.append(
            ModelDodSweepCheckResult(
                check=EnumDodSweepCheck.INFRA_CONSISTENCY,
                status=_status_from_tuple((infra_status, infra_detail)),
                detail=infra_detail,
            )
        )

    return ModelDodSweepTicketResult(
        ticket_id=ticket_id,
        title=ticket.get("title", ""),
        completed_at=completed_at or None,
        checks=checks,
    )


def run_dod_sweep(
    *,
    contracts_dir: Path,
    since_days: int = 7,
    exemptions_path: Path | None = None,
    api_key: str,
    now: datetime | None = None,
) -> ModelDodSweepResult:
    """Run DoD compliance sweep and return structured result.

    Args:
        contracts_dir: Path to contracts directory (for artifact checks).
        since_days: Look-back window in days.
        exemptions_path: Optional path to exemptions YAML.
        api_key: Linear API key.
        now: Injected wall-clock used both for the look-back-window anchor
            and for the OMN-9791 legacy-receipt cutoff. ``None`` resolves at
            call time to ``datetime.now(tz=UTC)``.
    Returns:
        ModelDodSweepResult with per-ticket check results and aggregate status.
    """
    effective_now = now if now is not None else datetime.now(tz=UTC)
    since_date = (effective_now.date() - timedelta(days=since_days)).isoformat()

    cutoff_date: str | None = None
    exempt_ids: set[str] = set()
    if exemptions_path:
        cutoff_date, exempt_ids = load_exemptions(exemptions_path)

    tickets = fetch_completed_tickets(api_key, since_date)

    ticket_results = [
        _audit_ticket_structured(
            t,
            contracts_dir,
            cutoff_date,
            exempt_ids,
            now=effective_now,
        )
        for t in tickets
    ]

    return ModelDodSweepResult(
        schema_version="1.0.0",
        date=effective_now.date().isoformat(),
        run_id=str(uuid.uuid4()),
        mode="batch",
        lookback_days=since_days,
        tickets=ticket_results,
    )
