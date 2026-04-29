# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""migrate_dod_contracts.py — backfill ``dod_evidence`` on ticket contracts.

Audits ``contracts/OMN-*.yaml`` against ``ModelTicketContract`` and emits a
ticket-type-aware ``dod_evidence`` block for any contract that lacks one.

Two ticket classes are recognised:

* **code** — touches code or interfaces (``is_seam_ticket=True`` OR
  ``interfaces_touched`` non-empty AND no governance-keyword override). Gets a
  pytest+CI evidence pair templated with ``${PR_NUMBER}`` / ``${REPO}`` so the
  Contract Compliance Check runner can substitute them.
* **governance** — repo-transfer / branch-protection / docs-edit / workflow-lift
  tickets that have no test surface. Gets a contract-validity check plus a
  PR-cites-ticket check.

The classifier consults BOTH the contract's structural fields (``is_seam_ticket``,
``interfaces_touched``) AND a curated set of governance keywords in the summary,
because the upstream contract auto-generator routinely stamps
``is_seam_ticket=True`` on non-code tickets — see OMN-10086 for the failure mode
this addresses.

Usage::

    # Audit only — print which contracts need migration, do not write
    uv run python scripts/migrate_dod_contracts.py --dry-run

    # Apply migration to the listed tickets
    uv run python scripts/migrate_dod_contracts.py --apply --tickets OMN-9829,OMN-9831

    # Apply migration to every contract under contracts/ that lacks dod_evidence
    uv run python scripts/migrate_dod_contracts.py --apply --all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Literal

import yaml

TicketClass = Literal["code", "governance"]

# Governance-keyword phrases (lower-case) that override ``is_seam_ticket=True``.
# These are matched as substrings against the ticket summary. New phrases should
# be added when a non-code ticket pattern is observed in the wild.
_GOVERNANCE_KEYWORDS: tuple[str, ...] = (
    "repo transfer",
    "outside collaborator",
    "make repo public",
    "make ... public",
    "branch protection",
    "merge queue",
    "configure merge",
    "configure v2",
    "claude.md",
    "pull-all",
    "central contracts",
    "document the",
    "add to repository registry",
    "repository registry",
    "lift ",
    "lift omni",
    "repoint",
    "move ",
    "architecture cutover",
    "proof of life",
    "end-to-end verification",
    "irreversible publication",
    "publication gate",
    "central",
    # Workflow-file edit tasks (no test surface — pytest cannot validate
    # the YAML wiring, so these are governance even when interfaces_touched
    # is mistakenly populated by the upstream auto-generator).
    "adapt golden-chain",
    "golden-chain-coverage.yml",
    "type-sync-check.yml",
    "check-handshake.yml",
    "contract-validation.yml",
    "onex-schema-compat.yml",
    "omni-standards-compliance.yml",
)


def classify_ticket(contract: dict[str, Any]) -> TicketClass:
    """Classify a ticket as ``code`` or ``governance``.

    Heuristic order:

    1. If summary matches any governance keyword, ``governance`` wins (this
       overrides spuriously-set ``is_seam_ticket=True`` on lift/cutover tickets).
    2. Else if ``is_seam_ticket=True`` OR ``interfaces_touched`` non-empty,
       ``code``.
    3. Else ``governance``.
    """
    summary = (contract.get("summary") or "").lower()
    if any(kw in summary for kw in _GOVERNANCE_KEYWORDS):
        return "governance"
    is_seam = bool(contract.get("is_seam_ticket", False))
    surfaces = contract.get("interfaces_touched") or []
    if is_seam or surfaces:
        return "code"
    return "governance"


def make_dod_evidence(
    ticket_class: TicketClass,
    ticket_id: str,  # noqa: ARG001  # accepted for symmetry with future per-ticket overrides
) -> list[dict[str, Any]]:
    """Return the ``dod_evidence`` block appropriate for the ticket class.

    Uses ``${PR_NUMBER}`` / ``${REPO}`` / ``${TICKET_ID}`` placeholders so the
    Contract Compliance Check runner can substitute them at execution time.

    ``ticket_id`` is currently informational — the runner reads ``${TICKET_ID}``
    from its env overlay rather than from a Python f-string. The argument is
    kept on the signature so future per-ticket overrides (e.g. emitting custom
    checks for a specific OMN-NNNN) can introduce them without changing the
    call sites in :func:`migrate_contract_file`.
    """
    if ticket_class == "code":
        return [
            {
                "id": "dod-001",
                "description": "Unit tests pass on the PR",
                "source": "generated",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": "uv run pytest tests/ -m unit -x",
                    }
                ],
            },
            {
                "id": "dod-002",
                "description": "CI pipeline green on the PR",
                "source": "generated",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": ("gh pr checks ${PR_NUMBER} --repo ${REPO}"),
                    }
                ],
            },
        ]
    # governance
    return [
        {
            "id": "dod-001",
            "description": "Contract YAML for this ticket parses as valid YAML",
            "source": "generated",
            "checks": [
                {
                    "check_type": "command",
                    "check_value": (
                        'uv run python -c "import yaml; '
                        "yaml.safe_load(open('contracts/${TICKET_ID}.yaml').read()); "
                        "print('ok')\""
                    ),
                }
            ],
        },
        {
            "id": "dod-002",
            "description": "PR title or body cites this ticket",
            "source": "generated",
            "checks": [
                {
                    "check_type": "command",
                    "check_value": (
                        "gh pr view ${PR_NUMBER} --repo ${REPO} "
                        '--json title,body --jq \'.title + " " + (.body // "")\' '
                        "| grep -q '${TICKET_ID}'"
                    ),
                }
            ],
        },
    ]


def needs_migration(contract: dict[str, Any]) -> bool:
    """A contract needs migration if it has no ``dod_evidence`` block, OR any
    check uses a legacy ``gh pr ...`` invocation that omits required context.

    Legacy forms (all stamped before OMN-10086) fail in detached-HEAD CI runs
    with "could not determine current branch":

    * ``gh pr checks`` (bare)
    * ``gh pr checks --watch``
    * ``gh pr view --json state -q .state``
    * ``gh pr view --json mergedAt -q .mergedAt``

    Any ``gh pr`` command lacking ``${PR_NUMBER}`` or ``${REPO}`` is treated as
    legacy.
    """
    dod_evidence = contract.get("dod_evidence") or []
    if not dod_evidence:
        return True
    for item in dod_evidence:
        for check in item.get("checks") or []:
            value = check.get("check_value")
            if not isinstance(value, str):
                continue
            stripped = value.strip()
            if stripped.startswith(("gh pr checks", "gh pr view")) and (
                "${PR_NUMBER}" not in stripped or "${REPO}" not in stripped
            ):
                return True
    return False


def migrate_contract_file(
    path: Path,
    *,
    apply: bool,
) -> tuple[bool, TicketClass | None]:
    """Migrate a single contract file in place when ``apply`` is true.

    Returns ``(migrated, ticket_class)``. ``migrated`` is true when the file
    needed migration; ``ticket_class`` is the class that was applied (or would
    have been applied in dry-run).
    """
    raw = path.read_text(encoding="utf-8")
    contract = yaml.safe_load(raw) or {}
    if not needs_migration(contract):
        return False, None

    ticket_id = contract.get("ticket_id") or path.stem
    ticket_class = classify_ticket(contract)
    contract["dod_evidence"] = make_dod_evidence(ticket_class, ticket_id)

    if apply:
        # Preserve a leading '---' if the original had one — yaml.safe_dump
        # does not emit it by default.
        prefix = "---\n" if raw.lstrip().startswith("---") else ""
        path.write_text(
            prefix
            + yaml.safe_dump(contract, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
    return True, ticket_class


def _resolve_targets(
    contracts_dir: Path,
    tickets: list[str] | None,
    *,
    all_flag: bool,
) -> list[Path]:
    if tickets:
        return [contracts_dir / f"{t}.yaml" for t in tickets]
    if all_flag:
        return sorted(contracts_dir.glob("OMN-*.yaml"))
    msg = "Specify either --tickets OMN-1,OMN-2 or --all"
    raise SystemExit(msg)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contracts-dir",
        default="contracts",
        help="Directory holding OMN-*.yaml contracts (default: ./contracts)",
    )
    parser.add_argument(
        "--tickets",
        default="",
        help="Comma-separated list of ticket IDs to migrate, e.g. OMN-9829,OMN-9831",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Migrate every contract under --contracts-dir that lacks dod_evidence",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to disk; default is dry-run (no writes)",
    )
    args = parser.parse_args()

    contracts_dir = Path(args.contracts_dir).resolve()
    if not contracts_dir.is_dir():
        print(f"[ERROR] Not a directory: {contracts_dir}", file=sys.stderr)
        return 1

    tickets = [t.strip() for t in args.tickets.split(",") if t.strip()]
    targets = _resolve_targets(contracts_dir, tickets or None, all_flag=args.all)

    migrated = 0
    skipped = 0
    missing = 0
    for path in targets:
        if not path.exists():
            print(f"[MISS] {path.name}: no such file")
            missing += 1
            continue
        was_migrated, klass = migrate_contract_file(path, apply=args.apply)
        if was_migrated:
            verb = "WROTE" if args.apply else "WOULD"
            print(f"[{verb}] {path.name}: class={klass}")
            migrated += 1
        else:
            skipped += 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n[{mode}] {migrated} migrated / {skipped} skipped / {missing} missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
