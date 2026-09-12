# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18236 — the gate that makes a `binds_ac` claim answerable to the ticket.

``binds_ac`` has been accepted by the schema since 2026-09-09 and adoption is
real: 61 contracts declare it, carrying 374 label entries, read live from the
default branch on 2026-09-12. Every one of them is an unchecked assertion. The
schema can see that ``AC1`` is a well-formed label; nothing anywhere has ever
asked whether the ticket HAS an AC1, and nothing notices when AC1's text is
rewritten under a check that is still green.

This module asks both questions. Its rules, in the order they fire:

**Local rules — no ticket needed, so they run at commit time too.**

* ``ac_binding_label_malformed`` — a claim or a record that is not a label.
  (The models refuse these outright; the rule exists so a contract that somehow
  reaches the gate unvalidated still fails here rather than passing by default.)
* ``ac_binding_unclaimed`` — a binding record for a label the item's
  ``binds_ac`` does not claim.
* ``ac_binding_duplicate`` — two records for one criterion.

**Ticket rules — they need the ticket body, and an absent body is RED.**

* ``ac_binding_unknown_criterion`` — the item claims a criterion the ticket
  does not have. This is the first half of the acceptance criterion: *an item
  declaring a criterion not present in the ticket fails its own compliance
  check.*
* ``ac_binding_stale_hash`` — a record pinned to a hash that is not the
  criterion's current hash. The second half: the criterion was rewritten, the
  binding no longer stands, and it reverts to unproven until re-accepted.
* ``ac_binding_ticket_unreadable`` — the ticket body could not be read. RED,
  never a skip, for the reason every other fence in this fleet fails closed:
  "I could not check" must not resolve to "so it passes".
* ``ac_binding_ticket_unlabelled`` — the ticket has criteria but none carry a
  label, so nothing in it can be bound at all. Reported in those words, because
  the fix is a ticket-authoring change and a gate that merely said "unknown
  criterion" would send somebody to edit the contract instead.

**What this gate deliberately does NOT do.** It does not require an acceptance.
A draft binding — a record with a hash and no ``accepted_by`` — passes here.
Requiring acceptance is a separate change (OMN-18238), landing after the
proposer exists, because turning the requirement on before anything can produce
an accepted record would hold every contract in the corpus at once.

**What it cannot do.** It compares a hash to a hash. It cannot tell whether the
criterion was right when it was written, and it cannot tell whether the item's
checks actually prove the criterion its label names. Both remain authorial
judgement, which is what the acceptance record is for.
"""

from __future__ import annotations

from dataclasses import dataclass

from onex_change_control.validation.ac_criteria import (
    canonical_ac_label,
    criteria_by_label,
    criterion_hash,
)

__all__ = [
    "AcBindingFinding",
    "check_contract_ac_bindings",
    "check_local_ac_bindings",
]


@dataclass(frozen=True)
class AcBindingFinding:
    """One fail-closed verdict. ``rule`` is the machine-readable reason code."""

    rule: str
    subject: str
    message: str

    def render(self) -> str:
        return f"  [{self.rule}] {self.subject}: {self.message}"


def _items(contract: object) -> list[dict[str, object]]:
    if not isinstance(contract, dict):
        return []
    raw = contract.get("dod_evidence")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _item_id(item: dict[str, object], index: int) -> str:
    raw = item.get("id")
    return str(raw) if isinstance(raw, str) and raw else f"dod_evidence[{index}]"


def _claims(item: dict[str, object]) -> list[str]:
    raw = item.get("binds_ac")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(entry) for entry in raw]


def _bindings(item: dict[str, object]) -> list[dict[str, object]]:
    raw = item.get("ac_bindings")
    if not isinstance(raw, (list, tuple)):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def check_local_ac_bindings(ticket_id: str, contract: object) -> list[AcBindingFinding]:
    """The rules that need no ticket body. Safe to run at commit time."""
    findings: list[AcBindingFinding] = []
    for index, item in enumerate(_items(contract)):
        subject = f"{ticket_id} {_item_id(item, index)}"
        claimed: set[str] = set()
        for entry in _claims(item):
            label = canonical_ac_label(entry)
            if not label:
                findings.append(
                    AcBindingFinding(
                        rule="ac_binding_label_malformed",
                        subject=subject,
                        message=(
                            f"binds_ac entry {entry!r} does not parse as an "
                            "acceptance-criterion label, so it can never join "
                            "to a criterion and reads downstream as a "
                            "criterion nothing claims"
                        ),
                    )
                )
                continue
            claimed.add(label)

        seen: set[str] = set()
        for binding in _bindings(item):
            raw_label = str(binding.get("label") or "")
            label = canonical_ac_label(raw_label)
            if not label:
                findings.append(
                    AcBindingFinding(
                        rule="ac_binding_label_malformed",
                        subject=subject,
                        message=(
                            f"ac_bindings record carries label {raw_label!r}, "
                            "which does not parse as an acceptance-criterion "
                            "label"
                        ),
                    )
                )
                continue
            if label in seen:
                findings.append(
                    AcBindingFinding(
                        rule="ac_binding_duplicate",
                        subject=subject,
                        message=(
                            f"more than one ac_bindings record for {label}; a "
                            "consumer stopping at the first match would report "
                            "whichever was written first"
                        ),
                    )
                )
            seen.add(label)
            if label not in claimed:
                findings.append(
                    AcBindingFinding(
                        rule="ac_binding_unclaimed",
                        subject=subject,
                        message=(
                            f"ac_bindings records {label} but binds_ac does "
                            "not claim it, so the acceptance is invisible to "
                            "every consumer that reads the claim"
                        ),
                    )
                )
    return findings


def check_contract_ac_bindings(
    ticket_id: str, contract: object, ticket_body: str | None
) -> list[AcBindingFinding]:
    """Every rule. ``ticket_body`` is ``None`` only to express *unreachable*.

    Unreachable is RED. There is no offline pass and no cached pass: a gate
    that answered "I could not read the ticket" with silence would report green
    on exactly the contracts nobody can check.
    """
    findings = check_local_ac_bindings(ticket_id, contract)

    claims_anything = any(_claims(item) or _bindings(item) for item in _items(contract))
    if not claims_anything:
        # A contract that claims no criterion has nothing for these rules to
        # say. That is a COVERAGE gap the closer already holds on; it is not
        # this gate's finding, and reporting it here would double-report one
        # fact through two mechanisms.
        return findings

    if ticket_body is None:
        findings.append(
            AcBindingFinding(
                rule="ac_binding_ticket_unreadable",
                subject=ticket_id,
                message=(
                    "the ticket body could not be read, so no claim in this "
                    "contract can be checked against a criterion. Unreachable "
                    "is RED (fail-closed), never a skip"
                ),
            )
        )
        return findings

    known = criteria_by_label(ticket_body)

    if not known:
        findings.append(
            AcBindingFinding(
                rule="ac_binding_ticket_unlabelled",
                subject=ticket_id,
                message=(
                    "this contract binds acceptance criteria but the ticket "
                    "carries no LABELLED criterion for anything to point at. "
                    "Label the ticket's criteria (`AC1:`, `DoD2:`) — a binding "
                    "cannot be pinned to a criterion identified only by its "
                    "position in a list"
                ),
            )
        )
        return findings

    for index, item in enumerate(_items(contract)):
        subject = f"{ticket_id} {_item_id(item, index)}"
        findings.extend(_unknown_criteria(ticket_id, subject, item, known))
        findings.extend(_stale_pins(subject, item, known))
    return findings


def _unknown_criteria(
    ticket_id: str, subject: str, item: dict[str, object], known: dict[str, str]
) -> list[AcBindingFinding]:
    """Claims naming a criterion the ticket does not have."""
    findings: list[AcBindingFinding] = []
    for entry in _claims(item):
        label = canonical_ac_label(entry)
        if not label or label in known:
            continue
        findings.append(
            AcBindingFinding(
                rule="ac_binding_unknown_criterion",
                subject=subject,
                message=(
                    f"binds_ac claims {label}, which is not an acceptance "
                    f"criterion on {ticket_id}. The ticket declares: "
                    f"{', '.join(sorted(known))}"
                ),
            )
        )
    return findings


def _stale_pins(
    subject: str, item: dict[str, object], known: dict[str, str]
) -> list[AcBindingFinding]:
    """Binding records pinned to a revision the criterion has moved past."""
    findings: list[AcBindingFinding] = []
    for binding in _bindings(item):
        label = canonical_ac_label(str(binding.get("label") or ""))
        if not label:
            continue
        criterion = known.get(label)
        if criterion is None:
            # Already reported against the claim; a second finding for the
            # same absent criterion adds no information.
            continue
        pinned = str(binding.get("criterion_hash") or "")
        current = criterion_hash(criterion)
        if pinned == current:
            continue
        findings.append(
            AcBindingFinding(
                rule="ac_binding_stale_hash",
                subject=subject,
                message=(
                    f"the binding for {label} is pinned to "
                    f"{pinned or '<absent>'} but {label} now hashes to "
                    f"{current}. The criterion's text changed after this "
                    "binding was recorded, so the binding no longer stands "
                    "and reverts to unproven until it is re-accepted "
                    "against the current text"
                ),
            )
        )
    return findings
