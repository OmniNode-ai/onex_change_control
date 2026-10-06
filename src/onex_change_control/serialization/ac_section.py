# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18270 — serializing the contract's DoD model into a ticket's body.

OMN-18236 gave `binds_ac` a READER. The validator in
:mod:`onex_change_control.validation.ac_binding_acceptance` and the evidence
closer in `omnibase_infra` both resolve a binding against the LABELLED criteria
in the Linear ticket body. Nothing has ever put them there. Every repair to
date has been a person retyping criteria into Linear — nine of them on
OMN-18270, eight by hand on 2026-09-21 — and a process whose only writer is a
human is a process that goes stale the next time the contract changes.

This module is the writer. The direction of authority it fixes is the load-
bearing decision:

    The contract's ``requirements[].acceptance[]`` is the MODEL.
    The ticket body's acceptance-criteria section is a RENDERING of it.

So a criteria section this module manages is generated output. It carries
sentinel comments, it is rewritten whole when the model changes, and it is left
exactly alone when the model does not.

**Why the labels cannot be computed here.** Every label this module emits, and
every label it reads back, goes through
:func:`onex_change_control.validation.ac_criteria.canonical_ac_label` — the
OMN-18236 reader, which is itself a deliberate port of the closer's reader. A
second normaliser living on the writing side is the one bug this whole job
exists to prevent: a body rendered with labels the gate spells differently is a
ticket that looks serialized and resolves nothing.
:func:`render_acceptance_criteria_section` is therefore written against the
reader's grammar, and ``tests/test_omn_18270_ac_section_serializer.py`` asserts
the round trip rather than the bullet count.

**What this module does NOT do: write to Linear.** This package's
imperative-contract guard blocks raw HTTP from ``src/`` — the reason the
OMN-18236 gate consumes a ``{ticket_id: body}`` JSON file the workflow layer
fetched rather than calling Linear itself. The same split applies here, and it
is a better shape anyway: this module computes a body, deterministically and
idempotently, and hands it to whichever surface already holds the Linear
credential. The dry run and the apply call the SAME function and compare the
SAME bytes, so an apply cannot diverge from the diff somebody approved.

**The refusals, and why each fails closed.**

``ac_section_unenumerated_binding``
    The contract binds a label its own model does not enumerate. This is the
    OMN-18167 shape exactly: one item binding ``AC1``..``AC8`` against a model
    declaring none. Rendering would emit an empty or short section over a
    ticket whose contract claims eight criteria, and the gate would read what
    was rendered — a silent downgrade wearing the costume of a repair. The
    honest outcome is to stop and say the model is the thing that is missing.

``ac_section_unlabelled_criterion``
    A criterion whose ``id`` the reader's grammar cannot parse into a label.
    Nothing can ever bind it, so writing it into the body would add a line that
    reads as a criterion and can never be satisfied through the binding path.

``ac_section_duplicate_label``
    Two criteria canonicalising to one label. The reader takes the first
    occurrence and reports the duplicate; emitting one is authoring a defect on
    purpose.

``ac_section_unmanaged_section``
    The body already carries an acceptance-criteria heading outside this
    module's sentinels. Appending a managed section would leave two criteria
    sections in one body, and the reader reads both — two texts under one
    label, first occurrence winning silently. Which one survives is a human's
    call, and the eight tickets repaired by hand on 2026-09-21 are precisely
    the corpus this guard protects.

**The honest limit.** Nothing here checks that a criterion is a GOOD criterion,
or that the check bound to it proves it. That limit is stated the same way the
binding models state theirs: this makes the claim answerable, not true.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from onex_change_control.models.model_ac_section_plan import ModelAcSectionPlan
from onex_change_control.validation.ac_criteria import (
    canonical_ac_label,
    criteria_by_label,
    is_ac_heading,
    normalise_criterion,
)

__all__ = [
    "BEGIN_MARKER",
    "END_MARKER",
    "RULE_DUPLICATE_LABEL",
    "RULE_UNENUMERATED_BINDING",
    "RULE_UNLABELLED_CRITERION",
    "RULE_UNMANAGED_SECTION",
    "SECTION_HEADING",
    "AcSectionRefusalError",
    "bound_labels",
    "criteria_from_contract",
    "plan_acceptance_criteria_update",
    "render_acceptance_criteria_section",
    "upsert_acceptance_criteria_section",
]

#: The sentinels delimiting the managed span. HTML comments, so Linear renders
#: them as nothing, and they survive a round trip through its markdown editor.
#: They are the ONLY thing that distinguishes generated criteria from
#: hand-written ones, which is why the unmanaged-section guard keys on them
#: rather than on the heading text.
BEGIN_MARKER = "<!-- onex:dod-acceptance-criteria:begin -->"
END_MARKER = "<!-- onex:dod-acceptance-criteria:end -->"

SECTION_HEADING = "## Acceptance criteria"

#: Rule names carried on every refusal, so a caller branches on an identifier
#: rather than matching the message prose. Same convention as the rule strings
#: on :class:`~onex_change_control.validation.ac_binding_acceptance.AcBindingFinding`.
RULE_UNLABELLED_CRITERION = "ac_section_unlabelled_criterion"
RULE_DUPLICATE_LABEL = "ac_section_duplicate_label"
RULE_UNENUMERATED_BINDING = "ac_section_unenumerated_binding"
RULE_UNMANAGED_SECTION = "ac_section_unmanaged_section"

_PROVENANCE = (
    "_Serialized from this ticket's `onex_change_control` contract "
    "(`requirements[].acceptance[]`) by `onex-serialize-ac-section` (OMN-18270). "
    "Edit the contract, not this section — the next run overwrites it._"
)


class AcSectionRefusalError(Exception):
    """A serialization that would have invented, duplicated or clobbered.

    Raised rather than returned so that no caller can reach a write path with a
    refusal in hand and treat it as a warning. Every message names the rule and
    the specific labels, because a count sends somebody hunting.
    """

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        super().__init__(f"{rule}: {message}")


def _acceptance_entries(contract: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(raw_id, statement)`` for every acceptance criterion in the contract.

    Read off the RAW mapping rather than through ``ModelTicketContract``. The
    gate CLIs in this package all parse contracts as plain YAML for the same
    reason: a contract that fails model validation for an unrelated field must
    still be answerable here, and the installed core version lags the schema
    (see ``scripts/validate_yaml.withhold_unreleased_binds_ac``). Legacy
    ``acceptance: ["text", ...]`` entries are tolerated in shape and then
    refused by the label rule below, which is the correct outcome: a bare
    string carries no stable label and cannot be bound.
    """
    entries: list[tuple[str, str]] = []
    requirements = contract.get("requirements")
    if not isinstance(requirements, Sequence) or isinstance(requirements, str):
        return entries
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            continue
        acceptance = requirement.get("acceptance")
        if not isinstance(acceptance, Sequence) or isinstance(acceptance, str):
            continue
        for criterion in acceptance:
            if isinstance(criterion, Mapping):
                raw_id = str(criterion.get("id", "")).strip()
                statement = str(criterion.get("statement", "")).strip()
            elif isinstance(criterion, str):
                raw_id, statement = "", criterion.strip()
            else:
                continue
            if statement:
                entries.append((raw_id, statement))
    return entries


def _strip_leading_label(label: str, statement: str) -> str:
    """Drop a label the statement repeats, so the rendering is not doubled.

    Cosmetic for the reader — it takes the first label on the line either way —
    and not cosmetic for the person reading the ticket, who would otherwise see
    ``**AC1** — AC1: the criterion``.
    """
    if canonical_ac_label(statement) != label:
        return statement
    remainder = statement
    for index, character in enumerate(statement):
        if character.isalnum():
            continue
        remainder = statement[index:]
        break
    else:  # pragma: no cover — a statement of only its own label
        return statement
    return remainder.lstrip(" \t:.)-*_\u2013\u2014") or statement


def criteria_from_contract(contract: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(label, statement)`` for every criterion the contract's model declares.

    In declaration order, across every requirement. Order is the contract's,
    never sorted: an author who declares ``AC3`` before ``AC1`` has made a
    choice a renderer should not quietly correct, and a positional reordering
    would churn the body on a contract edit that changed nothing.

    Refuses on a criterion whose ``id`` carries no parseable label, and on
    two criteria canonicalising to one label.
    """
    resolved: list[tuple[str, str]] = []
    seen: dict[str, str] = {}
    for raw_id, statement in _acceptance_entries(contract):
        label = canonical_ac_label(raw_id)
        if not label:
            message = (
                f"criterion id {raw_id!r} carries no AC<n>/DoD<n> label, so "
                f"nothing can ever bind it (statement: {statement[:80]!r})"
            )
            raise AcSectionRefusalError(RULE_UNLABELLED_CRITERION, message)
        if label in seen:
            message = (
                f"two criteria resolve to {label}: {seen[label][:60]!r} and "
                f"{statement[:60]!r}"
            )
            raise AcSectionRefusalError(RULE_DUPLICATE_LABEL, message)
        seen[label] = statement
        resolved.append((label, _strip_leading_label(label, statement)))
    return resolved


def bound_labels(contract: Mapping[str, Any]) -> list[str]:
    """Every canonical label the contract's ``dod_evidence`` binds, sorted-unique.

    Sorted here, unlike :func:`criteria_from_contract`, because this is a SET
    membership question and never a rendering order.
    """
    labels: set[str] = set()
    evidence = contract.get("dod_evidence")
    if not isinstance(evidence, Sequence) or isinstance(evidence, str):
        return []
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        binds = item.get("binds_ac")
        if not isinstance(binds, Sequence) or isinstance(binds, str):
            continue
        for entry in binds:
            label = canonical_ac_label(str(entry))
            if label:
                labels.add(label)
    return sorted(labels)


def render_acceptance_criteria_section(contract: Mapping[str, Any]) -> str:
    """The managed section for ``contract``, sentinels included.

    Refuses when the contract binds a label its model does not enumerate,
    under :data:`RULE_UNENUMERATED_BINDING`, and on any refusal
    :func:`criteria_from_contract` raises.
    """
    criteria = criteria_from_contract(contract)
    declared = {label for label, _ in criteria}
    missing = [label for label in bound_labels(contract) if label not in declared]
    if missing:
        message = (
            f"the contract binds {', '.join(missing)} and its model enumerates "
            f"{', '.join(sorted(declared)) or 'no criteria'}. The model is what "
            f"is missing; splitting a block binding into labels the model does "
            f"not declare would invent criteria."
        )
        raise AcSectionRefusalError(RULE_UNENUMERATED_BINDING, message)

    lines = [BEGIN_MARKER, "", SECTION_HEADING, "", _PROVENANCE, ""]
    lines.extend(f"- **{label}** — {statement}" for label, statement in criteria)
    lines.extend(["", END_MARKER])
    return "\n".join(lines)


def _has_unmanaged_ac_heading(body: str) -> bool:
    """True when an acceptance-criteria heading sits outside the managed span."""
    inside = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped == BEGIN_MARKER:
            inside = True
            continue
        if stripped == END_MARKER:
            inside = False
            continue
        if not inside and is_ac_heading(line):
            return True
    return False


def upsert_acceptance_criteria_section(body: str, section: str) -> str:
    """``body`` with ``section`` replacing the managed span, or appended.

    Replacement is span-based rather than line-based so that whatever the
    previous run wrote — including a section a Linear editor re-wrapped — is
    removed whole. Everything outside the sentinels is returned byte for byte.
    """
    start = body.find(BEGIN_MARKER)
    end = body.find(END_MARKER)
    if start != -1 and end != -1 and end > start:
        head = body[:start]
        tail = body[end + len(END_MARKER) :]
        return f"{head}{section}{tail}"
    if not body.strip():
        return f"{section}\n"
    return f"{body.rstrip()}\n\n{section}\n"


def _managed_span(body: str) -> str | None:
    """The sentinel-delimited span ``body`` carries, or ``None`` for no span.

    Sentinels included, so what comes back is exactly what a previous run
    wrote and whatever Linear has since made of it.
    """
    start = body.find(BEGIN_MARKER)
    end = body.find(END_MARKER)
    if start == -1 or end == -1 or end <= start:
        return None
    return body[start : end + len(END_MARKER)]


def _criteria_as_the_reader_sees_them(text: str) -> list[tuple[str, str]]:
    """``(label, normalised criterion)`` for ``text``, through the OMN-18236 reader.

    Both sides of the comparison below go through this, so neither can read the
    criteria differently from the gate that resolves the binding.

    A LIST and not a mapping, so the comparison is order-sensitive.
    :func:`criteria_from_contract` renders in the contract's declaration order
    and refuses to sort it, on the stated grounds that an author who declares
    ``AC3`` before ``AC1`` has made a choice a renderer should not quietly
    correct. A body whose criteria are in a different order from the model's is
    therefore not yet a faithful rendering of that model, even though the gate
    resolves by label and would pass either way, and saying so here keeps this
    module's two halves agreeing about what order means.
    """
    return [
        (label, normalise_criterion(criterion))
        for label, criterion in criteria_by_label(text).items()
    ]


def _already_renders(body: str, section: str) -> bool:
    """True when ``body``'s managed span already carries exactly ``section``'s criteria.

    **Why this is not a byte comparison, which is what it replaced.** Linear
    does not store the bytes it is given. Measured on the first real apply, to
    OMN-18167 on 2026-09-21 — 5,878 characters out, 5,883 back — its markdown
    normaliser rewrote the managed section on save: ``- `` bullets became
    ``* ``, and ``_emphasis_`` became ``*emphasis*``. Not one criterion changed.
    A byte comparison therefore reported a difference on every subsequent run,
    forever, and a corpus sweep would have rewritten byte-identical criteria
    into every serialized ticket on every pass — a Linear revision per ticket
    per run, each one looking like an edit a person made.

    So the question asked here is the one that actually matters: does the body
    already carry these criteria, as the reader that resolves bindings reads
    them. The bullet character is consumed by ``_LIST_ITEM_RE`` in
    :mod:`onex_change_control.validation.ac_criteria` (``[-*+]``), so a hyphen
    and an asterisk bullet yield the identical item text and compare equal
    without this module knowing anything about Linear's normaliser. The
    alternative — emitting whatever markdown Linear happens to normalise to —
    guesses at an undocumented third-party formatter and breaks the next time
    that formatter changes.

    Equality holds in BOTH directions and in order: a model that SHRANK does
    not match a body still rendering the criterion it dropped, and neither does
    a model whose criteria were reordered. Matching only "every declared
    criterion appears" would leave a stale criterion live in Linear under a
    label the contract no longer binds.

    **What this does NOT soften.** A criterion edited by hand inside the
    managed span reads back as different text and plans as ``CHANGED``, so the
    next run overwrites it. The direction of authority is unchanged by this
    comparison: the contract is the model and the span is generated output.
    Tolerating drift there would turn "idempotent" into "stops correcting the
    body", which is the opposite of what this section is for.

    **The stated limit.** Only criteria are compared. A change confined to the
    heading or the provenance line no longer forces a rewrite, because neither
    is a criterion and neither is anything the gate reads. That is deliberate
    and is the price of comparing meaning rather than bytes; a run that needs
    those re-emitted has to change a criterion or clear the span by hand.
    """
    span = _managed_span(body)
    if span is None:
        return False
    return _criteria_as_the_reader_sees_them(span) == _criteria_as_the_reader_sees_them(
        section
    )


def plan_acceptance_criteria_update(
    ticket_id: str, contract: Mapping[str, Any], body: str
) -> ModelAcSectionPlan:
    """What serializing ``contract`` into ``body`` would do, without doing it.

    The dry run and the apply both call this and both write
    :attr:`ModelAcSectionPlan.new_body`, so the bytes reviewed in a diff are
    the bytes written.

    Refuses on any rule above, and additionally on an acceptance-criteria
    heading in ``body`` that this module did not write, under
    :data:`RULE_UNMANAGED_SECTION`.
    """
    section = render_acceptance_criteria_section(contract)
    if _has_unmanaged_ac_heading(body):
        message = (
            f"{ticket_id} already carries an unmanaged acceptance-criteria "
            f"heading. Adding a managed section would leave two criteria "
            f"sections in one body, and the reader reads both: the first "
            f"occurrence of a label silently wins. Fold the existing section "
            f"into the contract, delete it from the body, then re-run."
        )
        raise AcSectionRefusalError(RULE_UNMANAGED_SECTION, message)
    new_body = upsert_acceptance_criteria_section(body, section)
    if _already_renders(body, section):
        new_body = body
    criteria = criteria_from_contract(contract)
    return ModelAcSectionPlan(
        ticket_id=ticket_id,
        old_body=body,
        new_body=new_body,
        section=section,
        labels=tuple(label for label, _ in criteria),
        bound_labels=tuple(bound_labels(contract)),
        changed=new_body != body,
    )
