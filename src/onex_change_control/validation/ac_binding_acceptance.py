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
  falsifier for a criterion whose label the grammar cannot parse (a compound
  ``AC2bb``, two letters rather than the one suffix letter OMN-18356 added), so
  nothing can ever point at it. Declared-and-unbound, never absent: reading it
  as absent is how a ticket with an unbindable criterion passes as fully bound.
* ``ac_binding_static_evidence_on_live_criterion`` — **OMN-18577.** An item
  whose every check only reads the repository tree, bound to a criterion that
  can only be settled by measuring a running system. A grep proves the code was
  written; it never proves the system did anything. See
  ``validation/proof_class`` for both vocabularies. Severity is content-derived:
  a MACHINE TRANSCRIPTION (the binding carries a ``proposed_by``) is REPORTED,
  because the defect is in the producer and refusing every one would fail 40
  live tickets at once; a HAND-WRITTEN claim REFUSES. Measured 2026-09-17: 142
  accepted records across 40 tickets, every one proposed by ``occ-autobind``.
* ``ac_binding_retirement_malformed`` — **OMN-18577.** A
  ``supersedes_ac_binding`` entry that names no item, names an item this
  contract does not declare, names a label that item never bound, or records no
  reason. Refused, and it takes no effect: a typo must not be able to withdraw a
  real binding.
* ``ac_binding_criterion_retired_unbound`` — **OMN-18577.** A criterion whose
  bindings were all retired and which nothing else claims. REPORTED, not
  refused: the withdrawal is recorded in the contract with its reason, so this
  is a visible gap rather than the silent partial coverage the rule above
  refuses.

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

**Retiring a wrong binding (OMN-18577).** A merged ``dod_evidence`` entry is
immutable and the append-only gate is right to refuse an edit, so until now a
binding that turned out to be wrong had no exit at all -- ``#9980`` tried to
delete four and was correctly refused. The add-only marker that did exist,
``evidence_artifact: "supersedes_dod_evidence:<id>"``, is read by the DoD
verifier's lineage resolution and never by THIS gate, so appending one beside a
wrong binding would have looked like a fix and changed nothing. A
``supersedes_ac_binding`` item is the shape this gate reads: it names the item,
the criterion and the reason, and the retired pair stops counting as a claim
while the record itself stays in the contract for audit. Nothing is deleted and
the append-only rule is untouched.

**What it cannot do.** It compares a hash to a hash, and it compares a proof
CLASS to a criterion class. It cannot tell whether the criterion was right when
it was written, it cannot tell whether a right-KIND proof is right on the facts,
and it cannot read a shell command its vocabulary does not cover. Those remain
authorial judgement, which is what the acceptance record is for.

**What it deliberately refuses to judge.** WHO accepted a binding and WHEN.
``accepted_by`` equal to the ticket creator with ``accepted_at`` equal to the
ticket's ``createdAt`` is OMN-18332 AC2's designed output and its AC2c positive
control -- correct by construction, because at the creation revision the creator
IS the actor who wrote the text. It reads like self-approval and is not;
refusing it would refuse the mechanism rather than the defect, and would fail
AC2c fleet-wide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from onex_change_control.validation.ac_criteria import (
    canonical_ac_label,
    criteria_by_label,
    criterion_hash,
    declared_criteria,
    falsifier_of,
    normalise_criterion,
)
from onex_change_control.validation.proof_class import (
    criterion_is_live,
    item_is_static,
    live_terms_in,
)

#: The item key that retires a binding a merged item can no longer be edited to
#: remove. OMN-18577.
_RETIREMENT_FIELD = "supersedes_ac_binding"

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


def _checks(item: dict[str, object]) -> list[dict[str, object]]:
    raw = item.get("checks")
    if not isinstance(raw, (list, tuple)):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def _retirement_entries(item: dict[str, object]) -> list[dict[str, object]]:
    raw = item.get(_RETIREMENT_FIELD)
    if not isinstance(raw, (list, tuple)):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def _retired_pairs(contract: object) -> set[tuple[str, str]]:
    """The ``(item id, label)`` pairs some item in this contract has retired.

    Contract-scoped for the same reason re-acceptance is (see
    :func:`_labels_pinned_to_current_text`): the OCC append-only gate refuses an
    edit to a merged ``dod_evidence`` entry, so the ONLY legal correction is a
    NEW item appended beside the wrong one. A retirement that had to be written
    into the entry it retires could never be written at all.

    Malformed entries are EXCLUDED here and refused separately. A retirement
    that names an item nobody can find must not take effect on the strength of
    its label alone -- that would let a typo silently withdraw a real binding.
    """
    valid: set[tuple[str, str]] = set()
    claimed_by_item = _claims_by_item(contract)
    for item in _items(contract):
        for entry in _retirement_entries(item):
            target = str(entry.get("item") or "")
            label = canonical_ac_label(str(entry.get("label") or ""))
            reason = str(entry.get("reason") or "").strip()
            if not target or not label or not reason:
                continue
            if label not in claimed_by_item.get(target, set()):
                continue
            valid.add((target, label))
    return valid


def _claims_by_item(contract: object) -> dict[str, set[str]]:
    """What each item claims, BEFORE any retirement is applied.

    The pre-retirement view is what a retirement is validated against: it can
    only retire something that was actually claimed, and asking the
    post-retirement view would make every retirement self-justifying.
    """
    by_item: dict[str, set[str]] = {}
    for index, item in enumerate(_items(contract)):
        labels = {
            label for label in (canonical_ac_label(e) for e in _claims(item)) if label
        }
        labels |= {
            label
            for label in (
                canonical_ac_label(str(b.get("label") or "")) for b in _bindings(item)
            )
            if label
        }
        by_item[_item_id(item, index)] = labels
    return by_item


def _retirement_findings(ticket_id: str, contract: object) -> list[AcBindingFinding]:
    """Refuse a retirement that cannot be acted on.

    Every clause below is the same principle: a retirement is a WITHDRAWAL of
    evidence, so it has to name what it withdraws and why, precisely enough that
    the next reader can tell whether it was right. A retirement missing any of
    those reads exactly like a valid one to a consumer checking only that the
    key is present, which is the shape this whole module exists to refuse.
    """
    findings: list[AcBindingFinding] = []
    claimed_by_item = _claims_by_item(contract)
    for index, item in enumerate(_items(contract)):
        subject = f"{ticket_id} {_item_id(item, index)}"
        for entry in _retirement_entries(item):
            target = str(entry.get("item") or "")
            raw_label = str(entry.get("label") or "")
            label = canonical_ac_label(raw_label)
            reason = str(entry.get("reason") or "").strip()
            problem: str | None = None
            if not target:
                problem = "names no `item` to retire a binding from"
            elif not label:
                problem = (
                    f"carries label {raw_label!r}, which does not parse as an "
                    "acceptance-criterion label"
                )
            elif not reason:
                problem = (
                    f"retires {label} from {target!r} with no `reason`. A "
                    "withdrawal with no recorded reason is indistinguishable "
                    "from a mistake"
                )
            elif target not in claimed_by_item:
                problem = (
                    f"names item {target!r}, which this contract does not "
                    "declare. Retiring a binding from an item nobody can find "
                    "takes no effect and reads as a correction that was made"
                )
            elif label not in claimed_by_item[target]:
                problem = (
                    f"retires {label} from {target!r}, but that item never "
                    f"bound {label}. It claims: "
                    + (", ".join(sorted(claimed_by_item[target])) or "<nothing>")
                )
            if problem:
                findings.append(
                    AcBindingFinding(
                        rule="ac_binding_retirement_malformed",
                        subject=subject,
                        message=f"a `{_RETIREMENT_FIELD}` entry {problem}",
                    )
                )
    return findings


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
        binds_anything = _binds_anything(contract, _retired_pairs(contract))
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

    # OMN-18577 -- a retirement that cannot be acted on is refused BEFORE it is
    # allowed to withdraw anything, and _retired_pairs independently excludes the
    # same malformed entries, so a typo can never silently un-bind a criterion.
    retired = _retired_pairs(contract)
    findings.extend(_retirement_findings(ticket_id, contract))

    # OMN-18333 -- EVERY declared criterion, before anything else is asked. This
    # runs whether or not the contract claims a single criterion: binding none
    # is the degenerate case of the rule, not an exemption from it.
    findings.extend(
        _unbound_declared_criteria(ticket_id, contract, ticket_body, retired)
    )

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

    re_accepted = _labels_pinned_to_current_text(contract, known, retired)

    for index, item in enumerate(_items(contract)):
        subject = f"{ticket_id} {_item_id(item, index)}"
        findings.extend(_unknown_criteria(ticket_id, subject, item, known))
        findings.extend(_stale_pins(subject, item, known, re_accepted))

    # OMN-18577 -- the proof-class rule runs last: it is the only one that needs
    # both the resolved criterion text and the retirement set.
    findings.extend(
        _static_evidence_on_live_criteria(ticket_id, contract, known, retired)
    )
    return findings


def _labels_pinned_to_current_text(
    contract: object, known: dict[str, str], retired: set[tuple[str, str]]
) -> set[str]:
    """Labels some record ANYWHERE in this contract pins to the current text.

    OMN-18406 -- WHY THIS IS CONTRACT-SCOPED AND NOT PER ITEM.
    ----------------------------------------------------------
    A merged ``dod_evidence`` entry is immutable: the required OCC Append-Only
    Gate refuses an edit to one with ``kind: entry_edited`` and tells the author
    to "Append a new item or file a supersession record instead". So the only
    re-acceptance an author can actually perform is a NEW record, and it lands
    on a NEW item -- never in place of the stale one, which cannot be corrected
    and cannot be removed.

    Scoring each record on its own therefore made re-acceptance impossible. The
    stale record kept refusing after the author had done the one legal thing,
    and the refusal's own instruction -- re-accept against the current text --
    named an edit no gate permitted. A criterion whose text moved once could
    never bind again.

    The rule here is the one the consumer already applies. ``omnibase_infra``'s
    evidence autoclose sweep collects every pin for a label and releases on ANY
    match, precisely because "a re-acceptance appended beside the original is
    the only shape the OCC append-only validator permits". This gate now reads
    the corpus the same way, so the authoring side and the closing side can no
    longer disagree about whether a criterion is accepted.

    This narrows nothing and releases nothing new: a label still refuses unless
    some record pins the criterion AS IT READS NOW. What it stops doing is
    refusing a label that HAS such a record because an older, unremovable one
    sits beside it.
    """
    current = {label: criterion_hash(text) for label, text in known.items()}
    pinned: set[str] = set()
    for index, item in enumerate(_items(contract)):
        item_id = _item_id(item, index)
        for binding in _bindings(item):
            label = canonical_ac_label(str(binding.get("label") or ""))
            if not label or label not in current:
                continue
            if (item_id, label) in retired:
                continue
            if str(binding.get("criterion_hash") or "") == current[label]:
                pinned.add(label)
    return pinned


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
    subject: str,
    item: dict[str, object],
    known: dict[str, str],
    re_accepted: set[str],
) -> list[AcBindingFinding]:
    """Binding records pinned to a revision the criterion has moved past.

    ``re_accepted`` carries the labels some record in this contract already
    pins to the criterion's current text. A label in that set is not reported
    here: the criterion HAS been re-accepted, and the stale record beside it is
    one the author is forbidden to touch. See
    :func:`_labels_pinned_to_current_text` for why that is the only shape a
    re-acceptance can take.
    """
    findings: list[AcBindingFinding] = []
    for binding in _bindings(item):
        label = canonical_ac_label(str(binding.get("label") or ""))
        if not label or label in re_accepted:
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


def _static_evidence_on_live_criteria(
    ticket_id: str,
    contract: object,
    known: dict[str, str],
    retired: set[tuple[str, str]],
) -> list[AcBindingFinding]:
    """OMN-18577 — an item that only reads the tree, bound to a live criterion.

    A source grep answers *"was this code written"*. A criterion settled by a
    consumer's lag, a projection row or a lane readback asks *"what is the
    system doing"*. Neither answers the other, and the two are distinguishable
    from the contract's own text -- which is what makes this mechanical rather
    than a matter of taste. See ``validation/proof_class`` for both
    vocabularies and for the two asymmetries they are built on.

    **THE SEVERITY SPLIT, and why it is not a cutover.** A binding carrying a
    ``proposed_by`` is a MACHINE TRANSCRIPTION: the autobinder copying the
    author's declaration of WHICH criterion (OMN-18332) onto whatever evidence
    item it minted. The author declared the criterion; nobody chose the item.
    That is a producer defect, it is owned upstream, and refusing it here would
    fail 40 live tickets in one run -- the exact outcome OMN-18333 recorded when
    it refused absence and "took the repository's evidence path offline until a
    transcribing producer was deployed". So a transcription is REPORTED.

    A binding with no proposer is a person writing an accepted claim by hand.
    There is no producer to fix and no fleet to break: that REFUSES.

    The discriminator is the contract's own content, evaluated every run. No
    date, no allowlist, no environment variable, nothing to switch off. The day
    the autobinder stops emitting these the held population reaches zero on its
    own, and a test asserts this function's body reads none of those things.

    **What it deliberately does NOT do.** It never judges WHO accepted a binding
    or WHEN. ``accepted_by`` equal to the ticket creator with ``accepted_at``
    equal to the ticket's creation time is OMN-18332 AC2's designed output and
    its AC2c positive control -- correct by construction, because at the
    creation revision the creator is the actor who wrote the text. Refusing that
    shape would refuse the mechanism, not the defect.
    """
    findings: list[AcBindingFinding] = []
    for index, item in enumerate(_items(contract)):
        item_id = _item_id(item, index)
        is_static, reasons = item_is_static(_checks(item))
        if not is_static:
            continue
        proposers = {
            str(binding.get("proposed_by") or "")
            for binding in _bindings(item)
            if canonical_ac_label(str(binding.get("label") or ""))
        }
        transcribed = any(proposer for proposer in proposers)
        severity = BINDING_HOLD if transcribed else BINDING_REFUSAL
        for entry in _claims(item):
            label = canonical_ac_label(entry)
            if not label or (item_id, label) in retired:
                continue
            criterion = known.get(label)
            if criterion is None:
                # Reported already as an unknown criterion; a proof-class
                # verdict on a criterion that does not exist adds nothing.
                continue
            if not criterion_is_live(criterion, falsifier_of(criterion)):
                continue
            terms = live_terms_in(criterion) or live_terms_in(
                falsifier_of(criterion) or ""
            )
            findings.append(
                AcBindingFinding(
                    rule="ac_binding_static_evidence_on_live_criterion",
                    subject=f"{ticket_id} {item_id}",
                    message=(
                        f"{label} is settled by measuring a running system — it "
                        f"names {', '.join(repr(term) for term in terms[:3])} — "
                        f"but every check on this item only reads the "
                        f"repository tree ({reasons[0]}). A grep proves the code "
                        f"was written, never that the system did anything, so "
                        f"this binding cannot settle {label}"
                        + (
                            _TRANSCRIBED_TAIL
                            if transcribed
                            else _HAND_AUTHORED_TAIL.format(label=label)
                        )
                    ),
                    severity=severity,
                )
            )
    return findings


#: Appended to a held finding. A hold that read like a refusal would be acted on
#: as one, and a hold with no stated owner is a line nobody ever clears.
_TRANSCRIBED_TAIL = (
    " -- REPORTED, NOT REFUSED: this binding carries a `proposed_by`, so it is "
    "the autobinder transcribing the author's declaration of WHICH criterion "
    "(OMN-18332) onto an item nobody chose for it. The defect is in the "
    "producer, and refusing every transcription here would take the evidence "
    "path offline fleet-wide. Retire the binding with a "
    "`supersedes_ac_binding` item and bind the criterion to evidence that "
    "measures it"
)

_HAND_AUTHORED_TAIL = (
    ". This binding names no proposer, so it is a hand-written claim: bind "
    "{label} to an item whose checks measure it, or retire this binding with a "
    "`supersedes_ac_binding` item naming the reason"
)


def _claimed_labels(
    contract: object, retired: set[tuple[str, str]] | None = None
) -> set[str]:
    """Every criterion label ANY evidence item in the contract STILL claims.

    The union across items, not a per-item set: a contract that proves AC1 with
    one item and AC2 with another has bound both, and asking each item to claim
    every criterion would refuse a correct companion.

    A retired ``(item, label)`` pair is subtracted from the union (OMN-18577).
    A retirement whose label another item still binds changes nothing here --
    which is the point: retiring one wrong binding does not un-prove a criterion
    that correct evidence elsewhere still covers.
    """
    retired = retired or set()
    labels: set[str] = set()
    for index, item in enumerate(_items(contract)):
        item_id = _item_id(item, index)
        for entry in _claims(item):
            label = canonical_ac_label(entry)
            if label and (item_id, label) not in retired:
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


def _binds_anything(
    contract: object, retired: set[tuple[str, str]] | None = None
) -> bool:
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
    retired = retired or set()
    if _claimed_labels(contract, retired):
        return True
    return any(
        any(
            (_item_id(item, index), canonical_ac_label(str(b.get("label") or "")))
            not in retired
            for b in _bindings(item)
        )
        for index, item in enumerate(_items(contract))
    )


def _unbound_declared_criteria(
    ticket_id: str,
    contract: object,
    ticket_body: str,
    retired: set[tuple[str, str]],
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

    claimed = _claimed_labels(contract, retired)
    retired_labels = {label for _, label in retired}
    severity = BINDING_REFUSAL if _binds_anything(contract, retired) else BINDING_HOLD
    findings: list[AcBindingFinding] = []
    for label, text in declared:
        if label and label in claimed:
            continue
        excerpt = normalise_criterion(text)[:_CRITERION_EXCERPT_CHARS]
        if label and label in retired_labels:
            # OMN-18577. A RETIRED criterion is unbound in the one way the
            # partial-coverage refusal exists to catch and does not need to
            # catch: VISIBLY, in the contract, with a recorded reason. The harm
            # the refusal prevents is a reviewer reading `binds_ac: [AC1, AC2]`
            # with no way to know the ticket declared four. A retirement is that
            # reviewer being told, in the artifact, which binding was withdrawn
            # and why -- so this reports and does not refuse.
            #
            # This is not a way to launder a partial companion into a pass. A
            # retirement has to name a real item, a label that item actually
            # bound, and a reason, or it is refused outright and takes no effect
            # (see _retirement_findings and _retired_pairs). And the closer holds
            # the ticket unclosed on an unbound criterion either way: what a
            # retirement buys is a correction that CAN be landed, not a flip.
            findings.append(
                AcBindingFinding(
                    rule="ac_binding_criterion_retired_unbound",
                    subject=ticket_id,
                    message=(
                        f"{label} had its binding retired by a "
                        f"`{_RETIREMENT_FIELD}` entry and no other evidence "
                        f"item claims it, so it is UNBOUND again: {excerpt!r}. "
                        "Bind it to evidence that measures it. -- REPORTED, NOT "
                        "REFUSED: the withdrawal is recorded in the contract "
                        "with its reason, so this is a visible gap rather than "
                        "the silent partial coverage the refusal above exists "
                        "to catch. The evidence closer holds the ticket on it"
                    ),
                    severity=BINDING_HOLD,
                )
            )
            continue
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
