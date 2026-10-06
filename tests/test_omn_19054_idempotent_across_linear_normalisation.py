# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-19054 — re-running the serializer on a body Linear has stored changes nothing.

OMN-18270 asserted idempotence and got it wrong in the one way that only an
actual write could expose. Its test re-planned against a body the serializer
itself had produced, and byte equality held trivially. **Linear does not store
the bytes it is given.** Measured on the first real apply, to OMN-18167 on
2026-09-21 — 5,878 characters out, 5,883 characters back — its markdown
normaliser rewrites the managed section on save:

* ``- `` list bullets become ``* ``
* ``_emphasis_`` becomes ``*emphasis*``, split around inline code spans

So the second run's byte comparison always differed, the planner always said
``CHANGED``, and a sweep would have rewritten byte-identical criteria into every
serialized ticket on every pass — a Linear revision per ticket per run, every
one of them looking like an edit somebody made.

**The fix compares what the reader sees, not what the bytes are.** The bullet
character is consumed by ``_LIST_ITEM_RE`` in
``src/onex_change_control/validation/ac_criteria.py:65``, which accepts
``[-*+]``, so a hyphen bullet and an asterisk bullet yield the identical item
text. Comparing through ``criteria_by_label`` — the OMN-18236 reader the gate
and the evidence closer both resolve bindings with — therefore makes the two
compare equal without this module knowing anything about Linear's normaliser.
Emitting whatever markdown Linear happens to normalise to would have been the
other option and is the wrong one: it guesses at an undocumented third-party
formatter and breaks the next time that formatter changes.

The fixture is the real post-write body of OMN-18167, committed under OMN-19046
and reused here rather than copied, so there is one recorded answer to "what
does Linear actually store" and not two.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from onex_change_control.serialization import ac_section
from onex_change_control.serialization.ac_section import (
    BEGIN_MARKER,
    END_MARKER,
    plan_acceptance_criteria_update,
)
from onex_change_control.validation.ac_criteria import criteria_by_label

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONTRACT_PATH = _REPO_ROOT / "contracts" / "OMN-18167.yaml"
_STORED_BODY = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "omn_19046"
    / "omn_18167_body_as_linear_normalised_it.md"
)

_TICKET_ID = "OMN-18167"

#: The character count Linear reported for the stored body at the moment it was
#: fetched. Pinned so an edit to the shared fixture is a failing test here too,
#: not only in the OMN-19046 suite that owns it.
_CHARS_STORED = 5883


def _stored_body() -> str:
    """The fixture as LINEAR holds it, with this repository's added newline removed.

    The end-of-file hook appends a trailing newline to every committed text file
    and Linear's stored body carries none. Stripped in one place, the same way
    the OMN-19046 suite strips it, so both read identical bytes.
    """
    text = _STORED_BODY.read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    loaded = yaml.safe_load(_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.unit
def test_the_fixture_really_is_normalised_differently_from_what_we_emit(
    contract: dict[str, Any],
) -> None:
    """The premise, asserted before anything is concluded from it.

    Without this, the idempotence test below could pass because the bytes happen
    to match, and would then prove nothing about normalisation at all. This is
    the negative control for the whole file: the stored body and the body this
    module renders are NOT byte-equal, and the difference is the bullet.
    """
    stored = _stored_body()
    assert len(stored) == _CHARS_STORED

    section = ac_section.render_acceptance_criteria_section(contract)
    assert "\n- **AC1** " in section, "this module emits hyphen bullets"
    assert "\n* **AC1** " in stored, "Linear stored asterisk bullets"
    assert section not in stored, "the premise is that the bytes differ"


@pytest.mark.unit
def test_replanning_a_body_linear_stored_changes_nothing(
    contract: dict[str, Any],
) -> None:
    """AC1 — the defect. Byte comparison said CHANGED here, forever.

    ``new_body`` is asserted byte-identical to the input rather than merely
    equivalent: a planner that returned a re-rendered body with ``changed``
    False would still hand an apply path different bytes to write.
    """
    stored = _stored_body()

    plan = plan_acceptance_criteria_update(_TICKET_ID, contract, stored)

    assert plan.changed is False
    assert plan.new_body == stored


@pytest.mark.unit
def test_a_real_model_change_still_replans_across_the_same_normalisation(
    contract: dict[str, Any],
) -> None:
    """AC2 — the fix must not buy idempotence by going blind.

    The same stored body, against a model whose first criterion has been
    rewritten. If this passed as UNCHANGED the serializer would have stopped
    doing its job, and the idempotence test above would be satisfied by a
    planner that never writes anything.
    """
    edited = copy.deepcopy(contract)
    criterion = edited["requirements"][0]["acceptance"][0]
    assert criterion["id"] == "AC1"
    criterion["statement"] = (
        "The C6 golden leg runs on the STAGING plane. -- falsifier: a rewritten "
        "criterion this test inserted, which no stored body carries."
    )

    plan = plan_acceptance_criteria_update(_TICKET_ID, edited, _stored_body())

    assert plan.changed is True
    assert "STAGING plane" in plan.new_body


@pytest.mark.unit
def test_a_dropped_criterion_replans_even_though_the_rest_still_match(
    contract: dict[str, Any],
) -> None:
    """A subset must not read as a match.

    A comparison written as "every criterion the model declares is present in
    the body" passes when the model SHRANK, leaving a stale criterion rendered
    in Linear under a label the contract no longer binds. Set equality in both
    directions is what rules that out, so it gets its own test rather than
    riding on the edited-statement case above.
    """
    shrunk = copy.deepcopy(contract)
    shrunk["requirements"][0]["acceptance"] = shrunk["requirements"][0]["acceptance"][
        :4
    ]
    # The binding still names AC1..AC8, and a model that no longer enumerates
    # them must be refused rather than rendered short -- which is the OMN-18270
    # refusal, not this ticket's concern. Drop the binding to isolate the
    # comparison being tested here.
    for item in shrunk["dod_evidence"]:
        item.pop("binds_ac", None)

    plan = plan_acceptance_criteria_update(_TICKET_ID, shrunk, _stored_body())

    assert plan.changed is True
    assert plan.labels == ("AC1", "AC2", "AC3", "AC4")


@pytest.mark.unit
def test_a_body_with_no_managed_section_is_still_changed(
    contract: dict[str, Any],
) -> None:
    """The first write must not be mistaken for a no-op.

    The reader is generous about WHERE it finds a labelled criterion, so a body
    that happened to mention the same labels outside the sentinels could satisfy
    a naive comparison. The span is what is compared, and a body without one has
    nothing to compare.
    """
    plain = "Some ticket prose with no criteria section at all.\n"

    plan = plan_acceptance_criteria_update(_TICKET_ID, contract, plain)

    assert plan.changed is True
    assert BEGIN_MARKER in plan.new_body
    assert END_MARKER in plan.new_body


@pytest.mark.unit
def test_the_comparison_reuses_the_reader_rather_than_parsing_again() -> None:
    """AC3 — one grammar, not two.

    A second parser on the writing side is the single bug this whole path exists
    to prevent: a body compared with labels spelled differently from the ones the
    gate reads is a ticket that looks in step and resolves nothing. Asserted two
    ways — the module calls the reader, and it defines no regex of its own.
    """
    source = Path(ac_section.__file__).read_text(encoding="utf-8")

    assert "criteria_by_label" in source
    # Read out of the module namespace rather than as an attribute: the reader
    # is imported for use here, not re-exported, so it is deliberately absent
    # from this module's __all__ and an attribute access would be a strict-mode
    # type error. The identity is what the assertion is about either way.
    assert vars(ac_section)["criteria_by_label"] is criteria_by_label
    assert "re.compile" not in source, "the serializer must own no grammar"


@pytest.mark.unit
def test_reordering_the_model_replans_even_though_every_criterion_still_matches(
    contract: dict[str, Any],
) -> None:
    """Order is part of the rendering, so a reorder is a change.

    ``criteria_from_contract`` renders in the contract's declaration order and
    refuses to sort, on the stated grounds that an author who declares ``AC3``
    before ``AC1`` has made a choice a renderer should not quietly correct. A
    comparison keyed only by label would accept a body in the old order forever
    and leave the two halves of this module disagreeing about whether order
    means anything. The gate resolves by label and would pass either way, which
    is exactly why this needs a test rather than an argument.
    """
    reordered = copy.deepcopy(contract)
    acceptance = reordered["requirements"][0]["acceptance"]
    acceptance[0], acceptance[1] = acceptance[1], acceptance[0]

    plan = plan_acceptance_criteria_update(_TICKET_ID, reordered, _stored_body())

    assert plan.changed is True
    assert plan.labels[:2] == ("AC2", "AC1")
    assert plan.new_body.index("**AC2**") < plan.new_body.index("**AC1**")


@pytest.mark.unit
def test_a_criterion_hand_edited_inside_the_span_is_overwritten_not_accepted(
    contract: dict[str, Any],
) -> None:
    """The serializer wins. Idempotence must not become tolerated drift.

    This is the failure the fix could plausibly have introduced: a comparison
    loose enough to survive Linear's normaliser could also be loose enough to
    accept a criterion somebody retyped in the Linear UI, which would leave the
    body and the contract saying different things with the gate green on the
    label. The managed span is generated output and the contract is the model,
    and that direction is unchanged by comparing meaning instead of bytes.
    """
    stored = _stored_body()
    original = (
        "The emitted records identify a key by its id and never carry the key value."
    )
    assert stored.count(original) == 1
    hand_edited = stored.replace(
        original, "Someone retyped this criterion in the Linear editor."
    )

    plan = plan_acceptance_criteria_update(_TICKET_ID, contract, hand_edited)

    assert plan.changed is True
    assert "Someone retyped this criterion" not in plan.new_body
    assert original in plan.new_body
