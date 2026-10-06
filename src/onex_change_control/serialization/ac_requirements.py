# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-19046 — lifting criterion text out of ticket bodies into the contracts.

OMN-18270 built the writer: :mod:`onex_change_control.serialization.ac_section`
renders a contract's ``requirements[].acceptance[]`` into the managed
acceptance-criteria section of its Linear ticket body. OMN-19038 built the
producer at the other end: the autobinder now carries each criterion's text
into every contract it mints from here on.

Neither reaches the contracts that already exist. Measured on ``origin/dev``
2026-09-21: 9,300 contracts, 370 carrying a ``binds_ac`` entry, one carrying
``requirements``. This module is the one-time backfill for the rest.

**The direction of authority is reversed here, exactly once, deliberately.**
Everywhere else in this package the contract is the model and the body is a
rendering of it. For these 370 the body is the only copy that has ever
existed, so this reads the body and writes the contract. After it runs, the
ordinary direction resumes and the serializer keeps the two in step. Doing
this silently would be the defect; the module exists to do it loudly and once.

**The refusals, and why each fails closed.** The temptation in a backfill is
to write something for every ticket and report a big number. Every rule below
exists because the alternative writes a model that reads as authoritative and
is not.

``ac_requirements_ticket_unreadable``
    No body for this ticket in the ``--ticket-bodies`` map. "I could not read
    the ticket" must not resolve to "so it needs no criteria", which is the
    same posture the OMN-18236 gate and the OMN-18270 serializer take.

``ac_requirements_unknown_criterion``
    The contract binds a label the ticket body does not carry. The binding is
    already broken — ``ac_binding_unknown_criterion`` reports it — and
    inventing text for the label would hide that rather than fix it.

``ac_requirements_duplicate_label``
    Two criteria in the body canonicalise to one label. Which one the author
    meant is a human's call; the reader takes the first silently and writing
    that choice into the contract would make an arbitrary pick permanent.

``ac_requirements_stale_pin``
    **The one that matters most, and the least obvious.** A binding record
    pins ``criterion_hash`` to the criterion's text at the moment it was
    accepted. If the body has been edited since, the live text no longer
    hashes to the pin, and ``ac_binding_stale_hash`` correctly reports the
    binding as no longer standing. Writing the LIVE text into the model there
    would produce a contract whose model and whose pin describe different
    sentences, and — worse — it would look like a repair. A stale binding is
    re-accepted by a person, never by a backfill.

**Idempotence follows OMN-19054's rule: compare through the reader, never by
bytes.** A contract whose model already carries these criteria is left
untouched, decided by
:func:`~onex_change_control.validation.ac_criteria.normalise_criterion` over
an ordered ``(label, text)`` list, so re-running writes nothing and a reordered
model is still a difference.

**The text is inserted, not re-serialized.** The contract file is rewritten by
splicing a rendered block in, leaving every other byte alone. Round-tripping
9,300 contracts through a YAML dumper would reformat files this change has
nothing to say about, and a diff nobody can read is a diff nobody reviews.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from onex_change_control.validation.ac_criteria import (
    acceptance_criteria_items,
    canonical_ac_label,
    criteria_by_label,
    criterion_hash,
    item_text,
    normalise_criterion,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "REQUIREMENT_ID",
    "REQUIREMENT_STATEMENT",
    "RULE_DUPLICATE_LABEL",
    "RULE_STALE_PIN",
    "RULE_TICKET_UNREADABLE",
    "RULE_UNKNOWN_CRITERION",
    "AcRequirementsRefusalError",
    "ModelRequirementsPlan",
    "bound_labels_in_order",
    "plan_requirements_backfill",
    "render_requirements_block",
    "splice_requirements_block",
]

RULE_TICKET_UNREADABLE = "ac_requirements_ticket_unreadable"
RULE_UNKNOWN_CRITERION = "ac_requirements_unknown_criterion"
RULE_DUPLICATE_LABEL = "ac_requirements_duplicate_label"
RULE_STALE_PIN = "ac_requirements_stale_pin"

#: Kept identical to the id the OMN-19038 producer emits, so a contract
#: backfilled here and a contract minted fresh are the same shape. A reader
#: who learns one has learned both.
REQUIREMENT_ID = "req-transcribed-acceptance-criteria"

REQUIREMENT_STATEMENT = (
    "The acceptance criteria this ticket's author declared, transcribed from "
    "the ticket body. The ticket body is the source and this block is the "
    "copy; each criterion's id is the label the sibling binds_ac entry claims."
)

#: Where the block is spliced. ``evidence_requirements`` is present on most
#: contracts and ``dod_evidence`` on all of them, so the first of these that
#: appears at column zero is the anchor and the block goes immediately above
#: it — the same position the OMN-19038 producer renders it at.
_ANCHORS = ("evidence_requirements:", "dod_evidence:")

#: yamlfmt refolds a long plain scalar and injects its own marker into the
#: value, which the OMN-15479 contamination ratchet then rejects. A literal
#: block is never refolded. Criterion prose is routinely past any sane fold
#: budget, so this is the normal path and not the exception.
_FOLD_BUDGET = 80


class AcRequirementsRefusalError(Exception):
    """A backfill that would have invented, guessed or papered over.

    Raised rather than returned so no caller can reach a write path holding a
    refusal and treat it as a warning. Every message names the rule, the
    ticket and the specific labels, because a count sends somebody hunting.
    """

    def __init__(self, rule: str, ticket_id: str, message: str) -> None:
        self.rule = rule
        self.ticket_id = ticket_id
        super().__init__(f"{rule}: {message}")


class ModelRequirementsPlan(BaseModel):
    """What backfilling one contract would do, without doing it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: str = Field(..., description="The contract's ticket.")
    labels: tuple[str, ...] = Field(
        default=(), description="The criterion labels the model would declare."
    )
    changed: bool = Field(
        default=False,
        description=(
            "False when the contract already carries these criteria as the "
            "reader reads them, so a re-run writes nothing."
        ),
    )
    old_text: str = Field(default="", description="The contract as it stands.")
    new_text: str = Field(default="", description="The contract after the splice.")


def bound_labels_in_order(contract: Mapping[str, Any]) -> list[str]:
    """Every canonical label the contract binds, in declaration order.

    Declaration order and not sorted, for the reason
    :func:`~onex_change_control.serialization.ac_section.criteria_from_contract`
    refuses to sort: the rendered body follows the model's order, and an
    arbitrary reordering here would churn a body on a contract edit that
    changed nothing.
    """
    labels: list[str] = []
    seen: set[str] = set()
    evidence = contract.get("dod_evidence")
    if not isinstance(evidence, list):
        return labels
    for item in evidence:
        if not isinstance(item, dict):
            continue
        binds = item.get("binds_ac")
        if not isinstance(binds, list):
            continue
        for entry in binds:
            label = canonical_ac_label(str(entry))
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
    return labels


def _pinned_hashes(contract: Mapping[str, Any]) -> dict[str, str]:
    """``{label: criterion_hash}`` over every binding record in the contract."""
    pins: dict[str, str] = {}
    evidence = contract.get("dod_evidence")
    if not isinstance(evidence, list):
        return pins
    for item in evidence:
        if not isinstance(item, dict):
            continue
        records = item.get("ac_bindings")
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            label = canonical_ac_label(str(record.get("label") or ""))
            pinned = str(record.get("criterion_hash") or "")
            if label and pinned:
                pins.setdefault(label, pinned)
    return pins


def _existing_model(contract: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(label, normalised statement)`` already declared, in order."""
    out: list[tuple[str, str]] = []
    requirements = contract.get("requirements")
    if not isinstance(requirements, list):
        return out
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        acceptance = requirement.get("acceptance")
        if not isinstance(acceptance, list):
            continue
        for criterion in acceptance:
            if not isinstance(criterion, dict):
                continue
            label = canonical_ac_label(str(criterion.get("id") or ""))
            statement = str(criterion.get("statement") or "")
            if label and statement:
                out.append((label, normalise_criterion(statement)))
    return out


def _texts_by_label(body: str) -> dict[str, list[str]]:
    """Every criterion text the reader sees, grouped by label.

    :func:`criteria_by_label` resolves a duplicate label by taking the first
    occurrence, deliberately and stably. That is right for a reader answering
    "what is AC1", and it is exactly wrong as an input to a writer: by the
    time the map is built the second criterion is gone, so a backfill reading
    only the map would pick the author's first draft and make it permanent
    without ever knowing there was a choice.

    This walks the same two passes ``criteria_by_label`` walks — the criteria
    sections, then the whole-body fallback — and keeps every text each pass
    produced, so the caller can refuse an ambiguity instead of inheriting a
    silent pick.

    **The fallback pass obeys the reader's precedence, and it has to.**
    Measured over the live corpus on 2026-09-21: treating a fallback line as a
    competing variant for a label the SECTION already resolved refused 48 of
    374 contracts, and on inspection almost none of them were ambiguous. The
    bodies carry lines like ``AC2 MET 2026-09-15T17:48Z on the worker`` —
    verdict annotations a lane appended elsewhere in the ticket, which is the
    OMN-18404 habit. The reader is not confused by those: the section pass has
    already resolved AC2 and the fallback is skipped for it. A writer that
    called them ambiguous would refuse the corpus over a disagreement with a
    reader that does not exist.

    So a duplicate here means what it means to the reader: two items inside
    the criteria sections resolving to one label, or two fallback lines for a
    label the sections never carried.
    """
    grouped: dict[str, list[str]] = {}
    for item in acceptance_criteria_items(body):
        label = canonical_ac_label(item)
        if label:
            grouped.setdefault(label, []).append(item)
    from_sections = set(grouped)
    for raw in body.splitlines():
        text = item_text(raw)
        label = canonical_ac_label(text) if text else ""
        if not label or label in from_sections:
            continue
        bucket = grouped.setdefault(label, [])
        if text not in bucket:
            bucket.append(text)
    return grouped


def _scalar(key: str, value: str, indent: int) -> str:
    """One mapping line, fold-proof, matching the producer's rendering."""
    pad = " " * indent
    if len(f"{pad}{key}: {value}") <= _FOLD_BUDGET and "\n" not in value:
        return f'{pad}{key}: "{value}"\n'
    return f"{pad}{key}: |-\n{pad}  {value}\n"


def render_requirements_block(criteria: Sequence[tuple[str, str]]) -> str:
    """The ``requirements:`` block for ``(label, statement)`` pairs."""
    if not criteria:
        return ""
    lines = [
        "requirements:\n",
        f'  - id: "{REQUIREMENT_ID}"\n',
        _scalar("statement", REQUIREMENT_STATEMENT, 4),
        "    acceptance:\n",
    ]
    for label, statement in criteria:
        lines.append(f'      - id: "{label}"\n')
        lines.append(_scalar("statement", statement, 8))
    return "".join(lines)


def splice_requirements_block(text: str, block: str) -> str:
    """``text`` with ``block`` inserted above the first top-level anchor.

    Every other byte is returned unchanged. A contract that already carries a
    top-level ``requirements:`` key is not spliced — the caller decides that
    case, because replacing an existing model is a different act from filling
    an absent one.
    """
    for anchor in _ANCHORS:
        match = re.search(rf"^{re.escape(anchor)}", text, re.MULTILINE)
        if match:
            return text[: match.start()] + block + text[match.start() :]
    msg = "contract carries neither evidence_requirements nor dod_evidence"
    raise ValueError(msg)


def plan_requirements_backfill(  # noqa: C901 - one refusal rule per branch, each named
    ticket_id: str,
    contract: Mapping[str, Any],
    contract_text: str,
    body: str | None,
) -> ModelRequirementsPlan:
    """What backfilling this one contract would do. Refuses rather than guesses.

    ``contract`` is the parsed mapping and ``contract_text`` its exact bytes;
    both are taken so the plan can decide from the parse and write from the
    text without re-serializing anything.
    """
    labels = bound_labels_in_order(contract)
    if not labels:
        return ModelRequirementsPlan(
            ticket_id=ticket_id, changed=False, old_text=contract_text
        )

    if body is None:
        msg = (
            f"{ticket_id} binds {', '.join(labels)} and no body for it was "
            f"supplied, so its criteria cannot be read. A ticket that could "
            f"not be read is never treated as a ticket with nothing to say"
        )
        raise AcRequirementsRefusalError(RULE_TICKET_UNREADABLE, ticket_id, msg)

    known = criteria_by_label(body)
    missing = [label for label in labels if label not in known]
    if missing:
        which = "that label" if len(missing) == 1 else "those labels"
        msg = (
            f"{ticket_id} binds {', '.join(missing)} and its ticket body "
            f"declares no criterion under {which}. The binding is already "
            f"unresolvable; inventing text for it would hide that rather "
            f"than repair it"
        )
        raise AcRequirementsRefusalError(RULE_UNKNOWN_CRITERION, ticket_id, msg)

    pins = _pinned_hashes(contract)
    grouped = _texts_by_label(body)
    for label in labels:
        variants: list[str] = []
        for text in grouped.get(label, []):
            normalised = normalise_criterion(text)
            if normalised not in variants:
                variants.append(normalised)
        if len(variants) <= 1:
            continue
        # THE PIN ARBITRATES, when there is one. A duplicate label is only
        # genuinely ambiguous if nothing recorded which text was accepted. The
        # binding record did exactly that: `criterion_hash` is the digest of
        # the criterion at the moment a person accepted it. When it matches
        # the text the reader resolves, the author's own accepted binding has
        # answered the question and taking that text is evidence, not a guess.
        #
        # Measured over the live corpus 2026-09-21: 8 of 374 contracts reach
        # here, and in every one the second variant is a lane's annotation
        # written INTO the criteria section -- "AC2 MET 2026-09-15T17:48Z",
        # "AC3 status, 2026-09-16" -- which is the OMN-18404 habit. Refusing
        # those would hold the backfill on a question the contract already
        # answers.
        if label in pins and pins[label] == criterion_hash(known[label]):
            continue
        why = (
            "the pinned hash matches neither"
            if label in pins
            else "no binding record pins the label"
        )
        msg = (
            f"{ticket_id} carries {len(variants)} criteria resolving to "
            f"{label}: {variants[0][:60]!r} and {variants[1][:60]!r}, and "
            f"{why}, so nothing recorded which text was accepted. Which one "
            f"the author meant is not a choice this backfill may make"
        )
        raise AcRequirementsRefusalError(RULE_DUPLICATE_LABEL, ticket_id, msg)

    stale = [
        label
        for label in labels
        if label in pins and pins[label] != criterion_hash(known[label])
    ]
    if stale:
        msg = (
            f"{ticket_id} pins {', '.join(stale)} to a hash the ticket's "
            f"current text does not produce, so the binding is STALE and the "
            f"criterion was edited after it was accepted. Writing the live "
            f"text into the model would make the model and the pin describe "
            f"different sentences while looking like a repair. A stale "
            f"binding is re-accepted by a person"
        )
        raise AcRequirementsRefusalError(RULE_STALE_PIN, ticket_id, msg)

    criteria = [(label, known[label]) for label in labels]
    desired = [(label, normalise_criterion(text)) for label, text in criteria]
    if _existing_model(contract) == desired:
        return ModelRequirementsPlan(
            ticket_id=ticket_id,
            labels=tuple(labels),
            changed=False,
            old_text=contract_text,
            new_text=contract_text,
        )
    if contract.get("requirements") is not None:
        # An existing model that DISAGREES is not this backfill's to overwrite.
        # It was authored by somebody, and replacing it is an edit rather than
        # a fill. Reported as unchanged so a sweep neither writes nor claims.
        return ModelRequirementsPlan(
            ticket_id=ticket_id,
            labels=tuple(labels),
            changed=False,
            old_text=contract_text,
            new_text=contract_text,
        )

    new_text = splice_requirements_block(
        contract_text, render_requirements_block(criteria)
    )
    return ModelRequirementsPlan(
        ticket_id=ticket_id,
        labels=tuple(labels),
        changed=new_text != contract_text,
        old_text=contract_text,
        new_text=new_text,
    )
