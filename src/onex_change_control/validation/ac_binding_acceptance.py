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
* ``ac_binding_criterion_unbound`` — **OMN-18333.** The ticket declares a
  falsifier for a criterion and no evidence item in the contract claims it. The
  rule is EVERY declared criterion: a companion binding some but not all of them
  is refused, because a partially bound companion leaves the closer starved on
  exactly the criteria it omitted while reading as a pass. One finding per
  unbound criterion, naming the label and the ticket — a count would send
  somebody hunting.
* ``ac_binding_criterion_unbindable`` — **OMN-18333.** The ticket declares a
  falsifier for a criterion whose label the grammar cannot parse (``AC2b``), so
  nothing can ever point at it. Declared-and-unbound, never absent: reading it
  as absent is how a ticket with an unbindable criterion passes as fully bound.

**ABSENT HOLDS, PARTIAL REFUSES.** Both coverage rules above carry a severity.
A contract binding NO criterion at all reports one :data:`BINDING_HOLD` per
unbound criterion and does NOT fail the gate; a contract binding something and
omitting a declared criterion fails. The sequencing ruling of 2026-09-14 drew
the line there and the reason is asymmetric harm, not leniency: partiality is
the only shape that reads as a pass while starving the closer, because nothing
downstream distinguishes "bound two of four" from "bound all of them". Absence
is already held by the evidence closer and is visible to anyone looking.
Refusing it here bought no safety and took the repository's evidence path
offline until a transcribing producer was deployed — including the very change
that deploys one, whose own companion binds nothing. The discriminator is the
contract's own content, evaluated every run: no cutover date, no allowlist, no
knob, and nothing to switch off on the day transcription starts working.

**Why OMN-18333 is a rule here rather than a new job.** Steps 5 and 6 of the
mechanical closeout plan make the binding EXIST — the admission guard requires a
named falsifier per criterion at create time, and the autobinder transcribes the
creation-revision declaration into the companion. This rule is the refusal that
makes it STAY. Without it the transcription is advisory and a companion that
quietly binds nothing lands exactly as it does today. It lands inside this job
because this job already resolves a contract's changed scope and already reads
the ticket body, so it inherits both and adds no required status check.

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
from typing import TYPE_CHECKING

from onex_change_control.validation.ac_criteria import (
    canonical_ac_label,
    criteria_by_label,
    criterion_hash,
    declared_criteria,
    normalise_criterion,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "BINDING_HOLD",
    "BINDING_REFUSAL",
    "AcBindingFinding",
    "check_contract_ac_bindings",
    "check_local_ac_bindings",
    "holds",
    "refusals",
]

#: A finding that fails the gate. The default, because every rule here was one
#: until the sequencing correction below.
BINDING_REFUSAL = "refusal"

#: A finding that is REPORTED and does not fail the gate. Exactly one condition
#: produces it -- a contract that binds no criterion at all, against a ticket
#: that declares some -- and the hold exists so that absence stays visible
#: without being fatal. See :func:`_unbound_declared_criteria`.
BINDING_HOLD = "hold"

#: How much of an unbindable criterion is quoted back in its refusal. The point
#: is to identify WHICH criterion, not to reprint the ticket, and an unbounded
#: splice is how a gate message hits a transport limit.
_CRITERION_EXCERPT_CHARS = 120


@dataclass(frozen=True)
class AcBindingFinding:
    """One verdict. ``rule`` is the machine-readable reason code.

    ``severity`` is :data:`BINDING_REFUSAL` unless stated otherwise, so a rule
    added without thinking about the distinction fails the gate rather than
    quietly joining the reported-but-tolerated class.
    """

    rule: str
    subject: str
    message: str
    severity: str = BINDING_REFUSAL

    @property
    def refuses(self) -> bool:
        return self.severity == BINDING_REFUSAL

    def render(self) -> str:
        """A refusal renders byte-identically to before the hold class existed.

        Consumers grep this line. Prefixing every finding would have changed
        the meaning of every existing match, so only the hold carries a marker.
        """
        prefix = "" if self.refuses else "HOLD "
        return f"  [{prefix}{self.rule}] {self.subject}: {self.message}"


def refusals(findings: Iterable[AcBindingFinding]) -> list[AcBindingFinding]:
    """The findings that fail the gate. One of these anywhere is a red run."""
    return [finding for finding in findings if finding.refuses]


def holds(findings: Iterable[AcBindingFinding]) -> list[AcBindingFinding]:
    """The findings that are reported and do not fail the gate."""
    return [finding for finding in findings if not finding.refuses]


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

    if ticket_body is None:
        # Unreachable mirrors the strictest verdict the readable path could have
        # reached for THIS contract, which is what fail-closed means -- never
        # stricter than any outcome the rule permits. A contract that CLAIMS
        # something could have been refused, so an unreadable ticket refuses it.
        # A contract that binds nothing could at most have been held, so an
        # unreadable ticket holds it: refusing there would make a Linear outage
        # stricter than Linear itself and re-create the bootstrap deadlock every
        # time the fetch step fails. (OMN-18333 originally widened this verdict
        # to every contract, justified by the coverage rule refusing a contract
        # that claims nothing. That verdict is now a hold, so the justification
        # is gone with it.)
        binds_anything = _binds_anything(contract)
        findings.append(
            AcBindingFinding(
                rule="ac_binding_ticket_unreadable",
                subject=ticket_id,
                message=(
                    "the ticket body could not be read, so neither this "
                    "contract's claims nor the criteria it is required to "
                    "cover can be checked. Unreachable is RED (fail-closed), "
                    "never a skip"
                )
                if binds_anything
                else (
                    "the ticket body could not be read. This contract binds no "
                    "criterion, so the only verdict a readable ticket could "
                    "have produced is a hold, and an unreadable one is held on "
                    "the same terms -- REPORTED, NOT REFUSED. A contract that "
                    "claims a criterion is refused here"
                ),
                severity=BINDING_REFUSAL if binds_anything else BINDING_HOLD,
            )
        )
        return findings

    # OMN-18333 -- EVERY declared criterion, before anything else is asked. This
    # runs whether or not the contract claims a single criterion: binding none
    # is the degenerate case of the rule, not an exemption from it.
    findings.extend(_unbound_declared_criteria(ticket_id, contract, ticket_body))

    claims_anything = any(_claims(item) or _bindings(item) for item in _items(contract))
    if not claims_anything:
        # A contract that claims no criterion has nothing left for the
        # per-claim rules below to say.
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


def _claimed_labels(contract: object) -> set[str]:
    """Every criterion label ANY evidence item in the contract claims to bind.

    The union across items, not a per-item set: a contract that proves AC1 with
    one item and AC2 with another has bound both, and asking each item to claim
    every criterion would refuse a correct companion.
    """
    labels: set[str] = set()
    for item in _items(contract):
        for entry in _claims(item):
            label = canonical_ac_label(entry)
            if label:
                labels.add(label)
    return labels


#: What each severity of a coverage finding tells the reader to DO. Appended to
#: the finding's own message so the verdict and its consequence arrive together:
#: a hold that read exactly like a refusal would be acted on as one.
_SEVERITY_TAIL: dict[str, str] = {
    BINDING_REFUSAL: "",
    BINDING_HOLD: (
        " -- REPORTED, NOT REFUSED: this contract binds no criterion at all, "
        "so there is no partial claim for a reviewer to misread. The ticket "
        "is held unclosed by the evidence closer until every declared "
        "criterion is bound; binding some but not all of them from here is "
        "what this gate refuses"
    ),
}


def _binds_anything(contract: object) -> bool:
    """Does this contract bind ANY criterion — the absent/partial discriminator.

    Content, never a spelled key. A producer that transcribes nothing emits no
    key at all (an empty render is byte-identical to no render, measured on the
    born path at 3ef4aa48), and an explicit ``binds_ac: []`` is the same
    producer saying the same thing in a different spelling. Reading those two
    differently would refuse on a distinction no consumer downstream can see.

    An ``ac_bindings`` record with no matching claim counts. It is already
    refused as ``ac_binding_unclaimed``; treating the contract as binding
    nothing would additionally downgrade the coverage verdict on a contract
    that is demonstrably mid-transcription.
    """
    if _claimed_labels(contract):
        return True
    return any(_bindings(item) for item in _items(contract))


def _unbound_declared_criteria(
    ticket_id: str, contract: object, ticket_body: str
) -> list[AcBindingFinding]:
    """OMN-18333 — every criterion the ticket declares a falsifier for is bound.

    Returns no findings for a ticket that declares no falsifier at all. That is
    not leniency, it is scope: such a ticket is pre-cutover, the autobinder
    transcribes nothing for it, and the closer holds it on an unbound criterion
    exactly as it does today. Refusing it here would retroactively block the
    legacy corpus on an authoring requirement that did not exist when it was
    written, and would do it from a gate whose whole subject is companions.

    ``superseded`` items are NOT excluded from the union. A superseded item's
    claim was true when it was made and its evidence is preserved for audit; a
    superseding item that re-states the claim covers the same label anyway. The
    narrower reading would refuse a contract whose supersession chain is
    correct, which is the wrong direction for a rule this broad.

    **ABSENT HOLDS, PARTIAL REFUSES — the sequencing correction.** A contract
    that binds NOTHING reports a hold per unbound criterion and does not fail
    the gate. A contract that binds SOMETHING and omits a declared criterion
    fails. The line is there because partiality is the only shape that reads as
    a pass while starving the closer: a reviewer seeing ``binds_ac: [AC1, AC2]``
    has no way to know the ticket declared four, whereas a contract claiming
    nothing is held by the closer already (OMN-18330) and is visibly unclaimed
    to anybody looking. Refusing absence bought no safety and took the whole
    evidence path offline until a producer that did not exist yet was deployed —
    including the change that deploys it, whose own companion binds nothing.
    Ruling of 2026-09-14, recorded at ``docs/tracking/ROLLING_WORK_LEDGER.md``.

    This is NOT a cutover, a date, an allowlist or a knob. It is computed from
    the contract's own content on every run: the day a producer transcribes, its
    companions bind something and every one of them is judged on the partial
    rule from that moment, with nothing to switch off.
    """
    declared = declared_criteria(ticket_body)
    if not declared:
        return []

    claimed = _claimed_labels(contract)
    severity = BINDING_REFUSAL if _binds_anything(contract) else BINDING_HOLD
    findings: list[AcBindingFinding] = []
    for label, text in declared:
        if label and label in claimed:
            continue
        excerpt = normalise_criterion(text)[:_CRITERION_EXCERPT_CHARS]
        if not label:
            findings.append(
                AcBindingFinding(
                    rule="ac_binding_criterion_unbindable",
                    subject=ticket_id,
                    message=(
                        f"{ticket_id} declares a falsifier for a criterion "
                        "carrying no parseable acceptance-criterion label, so "
                        "no evidence item can ever bind it: "
                        f"{excerpt!r}. Give the criterion a bare `AC<n>` / "
                        "`DoD<n>` label on the ticket -- a suffixed or "
                        "compound label parses as no label at all, and a "
                        "criterion nothing can point at is unbound, never absent"
                        + _SEVERITY_TAIL[severity]
                    ),
                    severity=severity,
                )
            )
            continue
        findings.append(
            AcBindingFinding(
                rule="ac_binding_criterion_unbound",
                subject=ticket_id,
                message=(
                    f"{label} is declared with a falsifier on {ticket_id} and "
                    "no dod_evidence item in this contract claims it via "
                    f"binds_ac: {excerpt!r}. Every declared criterion must be "
                    "bound -- a companion binding some but not all of them "
                    "leaves the closer starved on exactly the ones it omitted "
                    "while reading as a pass. This contract claims: "
                    + (", ".join(sorted(claimed)) or "<nothing>")
                    + _SEVERITY_TAIL[severity]
                ),
                severity=severity,
            )
        )
    return findings
