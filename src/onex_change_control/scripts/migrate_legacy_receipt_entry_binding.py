# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Mint ``contract_entry_sha256`` onto receipts bound only by whole-file hash.

THE PROBLEM
-----------
``ModelDodReceipt`` carries two contract bindings and the validators branch on
them entry-hash-FIRST. ``validator_receipt_gate._contract_binding_failure``
returns as soon as ``contract_entry_sha256`` is present and correct;
``check_receipt_hardening._contract_hash_violation`` does the same. The
whole-file ``contract_sha256`` is only ever the FALLBACK, consulted when no
entry hash exists.

That makes a whole-file-only receipt fragile in one specific way: appending ANY
new ``dod_evidence`` item to its contract changes the file's bytes, so the
fallback no longer matches and previously valid merged evidence turns into a
"contract mutated after this receipt was produced" violation. The
diff-derived behavior-proof backfill (omnimarket
``scripts/ci/occ_behavior_proof_backfill.py``, OMN-17943) therefore REFUSES to
touch any contract carrying such a receipt — ``REFUSED_LEGACY_WHOLE_FILE_BINDING``
— because trading one evidence gap for one broken binding is a net loss. Eight
of the top-40 candidates in the live 2026-09-06 discovery window were refused
for exactly this reason, and they can never become mintable on their own.

THE REPAIR, AND WHY IT IS NOT A REWRITE OF EVIDENCE
---------------------------------------------------
The repair is the one the hardening gate itself names in its failure text:
"mint contract_entry_sha256 per OMN-13888 so future appends to other entries do
not invalidate it again". This tool mints exactly that field and CHANGES
NOTHING ELSE. In particular it does not remove ``contract_sha256``: that value
was true when the receipt was produced and remains a true historical statement
about the contract at mint time. Both validators ignore it once the entry hash
is present, so leaving it costs nothing and deleting it would destroy a record.

The migration is only sound if the entry hash we compute NOW is the same hash
the receipt would have carried when it was minted. That is not assumed, it is
PROVEN per receipt, and the proof is the fallback binding itself: this tool
refuses unless the receipt's ``contract_sha256`` still equals
``sha256(contracts/<ticket>.yaml)`` TODAY. A match means the contract file is
byte-identical to the state the receipt was bound to, so the receipt's own
``dod_evidence`` entry is provably unchanged and its per-entry hash is provably
the one it was minted against.

A receipt whose whole-file binding is ALREADY stale is refused
(``REFUSED_BINDING_ALREADY_STALE``). Minting an entry hash there would compute
it from a contract the receipt was never bound to, which would silently convert
a detectable stale binding into an undetectable false one — manufacturing the
exact class of evidence the gate exists to catch. That case is a supersession
problem and belongs to the append-only supersession path, not here.

WRITING
-------
The insertion is TEXTUAL — a single new line after the existing
``contract_sha256:`` line — and never a YAML round-trip. Re-dumping a receipt
reflows every other field, and OCC's yamlfmt-stability rules (OMN-17794) exist
precisely because a reflow changes a receipt's committed VALUE. One line in,
nothing else touched, so ``git diff`` on a migrated receipt is exactly one
added line and a reviewer can see that at a glance.

Ticket: OMN-17943 (parent OMN-16106).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from omnibase_core.validation.validator_receipt_gate import (
    ContractEntryNotFoundError,
    compute_contract_entry_sha256,
    compute_contract_sha256,
)

__all__ = [
    "EnumEntryBindingDecision",
    "MigrationOutcome",
    "insert_contract_entry_sha256",
    "judge_receipt",
    "main",
    "run",
]

# The line the new field is anchored to. Anchored at column zero because a
# receipt is a flat mapping: a `contract_sha256:` appearing indented would be a
# value inside some other block scalar, not the field.
_CONTRACT_SHA_LINE_RE = re.compile(r"^contract_sha256:\s*(?P<value>.+?)\s*$")


class EnumEntryBindingDecision(StrEnum):
    """What this tool decided for one receipt, and why."""

    MIGRATED = "MIGRATED"
    SKIPPED_NOT_LEGACY = "SKIPPED_NOT_LEGACY"
    REFUSED_NO_CONTRACT = "REFUSED_NO_CONTRACT"
    REFUSED_BINDING_ALREADY_STALE = "REFUSED_BINDING_ALREADY_STALE"
    REFUSED_ENTRY_NOT_IN_CONTRACT = "REFUSED_ENTRY_NOT_IN_CONTRACT"
    REFUSED_UNPARSEABLE = "REFUSED_UNPARSEABLE"


@dataclass(frozen=True)
class MigrationOutcome:
    """The plan for one receipt. The writer consumes this and nothing else."""

    receipt_path: str
    ticket_id: str
    evidence_item_id: str
    decision: EnumEntryBindingDecision
    reason: str
    contract_entry_sha256: str = ""

    def as_report_row(self) -> dict[str, Any]:
        """The JSON row a run emits — every field a reader needs to check it."""
        return {
            "receipt_path": self.receipt_path,
            "ticket_id": self.ticket_id,
            "evidence_item_id": self.evidence_item_id,
            "decision": self.decision.value,
            "reason": self.reason,
            "contract_entry_sha256": self.contract_entry_sha256,
        }


def insert_contract_entry_sha256(receipt_text: str, entry_sha: str) -> str:
    """Return ``receipt_text`` with the entry hash added after the whole-file one.

    Raises ``ValueError`` when there is no top-level ``contract_sha256:`` line
    to anchor to — a receipt this tool has no business editing. Refusing to
    guess a position beats appending the field somewhere a reader would not
    look for it.

    The value is quoted the same way the anchor line quotes its own: a receipt
    written with bare scalars stays bare, one written with double quotes stays
    quoted, so the migration cannot be spotted as a formatting outlier.
    """
    lines = receipt_text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = _CONTRACT_SHA_LINE_RE.match(line.rstrip("\n"))
        if match is None:
            continue
        quoted = match.group("value").startswith('"')
        rendered = f'"{entry_sha}"' if quoted else entry_sha
        ending = "\n" if line.endswith("\n") else ""
        lines.insert(index + 1, f"contract_entry_sha256: {rendered}{ending}")
        return "".join(lines)
    message = (
        "receipt carries no top-level `contract_sha256:` line to anchor the "
        "entry hash to; refusing to guess where the field belongs."
    )
    raise ValueError(message)


def judge_receipt(
    *,
    receipt_path: str,
    receipt_body: Any,
    contract_text: str | None,
    contract_data: Any,
    contract_whole_file_sha: str | None,
) -> MigrationOutcome:
    """The whole judgement for one receipt, as a pure function of observed facts.

    ``contract_text`` is accepted but unused by the decision itself; it is part
    of the signature so a caller cannot resolve the contract for the hash and
    forget to resolve it for the entry lookup — the two must come from the same
    read.
    """
    ticket_id = ""
    evidence_item_id = ""
    if isinstance(receipt_body, dict):
        ticket_id = str(receipt_body.get("ticket_id") or "")
        evidence_item_id = str(receipt_body.get("evidence_item_id") or "")

    if not isinstance(receipt_body, dict):
        return MigrationOutcome(
            receipt_path=receipt_path,
            ticket_id=ticket_id,
            evidence_item_id=evidence_item_id,
            decision=EnumEntryBindingDecision.REFUSED_UNPARSEABLE,
            reason="receipt YAML is not a mapping.",
        )

    whole_file = receipt_body.get("contract_sha256")
    if receipt_body.get("contract_entry_sha256") is not None or not whole_file:
        return MigrationOutcome(
            receipt_path=receipt_path,
            ticket_id=ticket_id,
            evidence_item_id=evidence_item_id,
            decision=EnumEntryBindingDecision.SKIPPED_NOT_LEGACY,
            reason=(
                "receipt already carries contract_entry_sha256, or carries no "
                "whole-file binding to migrate; the entry-hash-first branch "
                "already applies and nothing is fragile here."
            ),
        )

    if contract_text is None or contract_whole_file_sha is None:
        return MigrationOutcome(
            receipt_path=receipt_path,
            ticket_id=ticket_id,
            evidence_item_id=evidence_item_id,
            decision=EnumEntryBindingDecision.REFUSED_NO_CONTRACT,
            reason=(
                f"no readable contract for {ticket_id or 'this receipt'}; the "
                "entry hash cannot be computed from anything."
            ),
        )

    if str(whole_file) != contract_whole_file_sha:
        return MigrationOutcome(
            receipt_path=receipt_path,
            ticket_id=ticket_id,
            evidence_item_id=evidence_item_id,
            decision=EnumEntryBindingDecision.REFUSED_BINDING_ALREADY_STALE,
            reason=(
                f"receipt binds contract_sha256={whole_file!r} but the contract "
                f"now hashes to {contract_whole_file_sha!r}. The contract changed "
                "after this receipt was produced, so an entry hash computed now "
                "would bind a state the receipt was never bound to — that "
                "converts a detectable stale binding into an undetectable false "
                "one. This is a supersession case, not a re-hash case."
            ),
        )

    try:
        entry_sha = compute_contract_entry_sha256(contract_data, evidence_item_id)
    except ContractEntryNotFoundError as exc:
        return MigrationOutcome(
            receipt_path=receipt_path,
            ticket_id=ticket_id,
            evidence_item_id=evidence_item_id,
            decision=EnumEntryBindingDecision.REFUSED_ENTRY_NOT_IN_CONTRACT,
            reason=(
                f"dod_evidence item {evidence_item_id!r} is not in the contract: "
                f"{exc}. Do not fabricate a hash for an entry that is gone."
            ),
        )

    return MigrationOutcome(
        receipt_path=receipt_path,
        ticket_id=ticket_id,
        evidence_item_id=evidence_item_id,
        decision=EnumEntryBindingDecision.MIGRATED,
        reason=(
            "the whole-file binding still matches the contract on disk, so the "
            "receipt's own dod_evidence entry is provably unchanged since it was "
            "minted and its per-entry hash is the one it was bound to."
        ),
        contract_entry_sha256=entry_sha,
    )


def run(*, occ_root: Path, tickets: list[str], apply: bool) -> dict[str, Any]:
    """Judge (and optionally migrate) every receipt under each ticket."""
    outcomes: list[MigrationOutcome] = []
    written = 0

    for ticket_id in tickets:
        contract_path = occ_root / "contracts" / f"{ticket_id}.yaml"
        contract_text: str | None = None
        contract_data: Any = None
        whole_file_sha: str | None = None
        if contract_path.is_file():
            try:
                contract_text = contract_path.read_text(encoding="utf-8")
                contract_data = yaml.safe_load(contract_text)
                whole_file_sha = f"sha256:{compute_contract_sha256(contract_path)}"
            except (OSError, yaml.YAMLError) as exc:
                print(
                    f"::warning::unreadable contract {contract_path}: {exc}",
                    file=sys.stderr,
                )
                contract_text = None
                contract_data = None
                whole_file_sha = None

        base = occ_root / "drift" / "dod_receipts" / ticket_id
        for path in sorted(base.rglob("*.yaml")) if base.is_dir() else []:
            rel = str(path.relative_to(occ_root))
            try:
                receipt_text = path.read_text(encoding="utf-8")
                receipt_body = yaml.safe_load(receipt_text)
            except (OSError, yaml.YAMLError) as exc:
                outcomes.append(
                    MigrationOutcome(
                        receipt_path=rel,
                        ticket_id=ticket_id,
                        evidence_item_id="",
                        decision=EnumEntryBindingDecision.REFUSED_UNPARSEABLE,
                        reason=f"receipt is unreadable: {exc}",
                    )
                )
                continue

            outcome = judge_receipt(
                receipt_path=rel,
                receipt_body=receipt_body,
                contract_text=contract_text,
                contract_data=contract_data,
                contract_whole_file_sha=whole_file_sha,
            )
            outcomes.append(outcome)

            if outcome.decision is not EnumEntryBindingDecision.MIGRATED or not apply:
                continue
            path.write_text(
                insert_contract_entry_sha256(
                    receipt_text, outcome.contract_entry_sha256
                ),
                encoding="utf-8",
            )
            written += 1

    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.decision.value] = counts.get(outcome.decision.value, 0) + 1

    return {
        "dry_run": not apply,
        "tickets_scanned": len(tickets),
        "receipts_scanned": len(outcomes),
        "migrated": written,
        "would_migrate": (
            sum(
                1
                for outcome in outcomes
                if outcome.decision is EnumEntryBindingDecision.MIGRATED
            )
            if not apply
            else written
        ),
        "counts": counts,
        "outcomes": [outcome.as_report_row() for outcome in outcomes],
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: ``migrate-legacy-receipt-entry-binding``."""
    parser = argparse.ArgumentParser(
        description=(
            "Mint contract_entry_sha256 onto receipts bound only by the "
            "whole-file contract_sha256, so appending a dod_evidence item no "
            "longer restales them (OMN-17943)."
        )
    )
    parser.add_argument(
        "--occ-root",
        type=Path,
        default=Path(),
        help="Root of the onex_change_control checkout to operate on.",
    )
    parser.add_argument(
        "--tickets",
        required=True,
        help="Whitespace- or comma-separated OMN ids whose receipts to judge.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the migrations. Omitted, the run reports what it would do.",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    tickets: list[str] = []
    for match in re.findall(r"OMN-[0-9]+", args.tickets or ""):
        if match not in tickets:
            tickets.append(match)
    if not tickets:
        print("::error::--tickets named no OMN ids.", file=sys.stderr)
        return 2

    report = run(occ_root=args.occ_root, tickets=tickets, apply=bool(args.apply))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
