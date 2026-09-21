# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18270 — the contract's criteria become the ticket's criteria, mechanically.

`binds_ac` has had a reader since OMN-18236 and no writer. The gate resolves a
binding against the labelled criteria in the ticket BODY, and nothing has ever
put them there: every one of the nine tickets on OMN-18270 was repaired by a
human retyping criteria into Linear, which is the process this replaces.

The direction of authority these tests pin is the whole point. The contract's
``requirements[].acceptance[]`` is the model. The ticket body is a RENDERING of
it. A label the contract binds and the model does not enumerate is refused
rather than invented — which is exactly the wall OMN-18167 hit on 2026-09-21
(comment 873fa01e: *"Any eight-way split I wrote would be invented, which this
ticket's own 'do not invent' section forbids"*).

The round-trip test is the load-bearing one. What this module RENDERS must be
what `validation.ac_criteria` READS — the same reader the OMN-18236 validator
and the evidence closer resolve bindings with. A renderer that emitted a shape
its own reader could not parse would serialize criteria into a body and leave
the gate seeing none, which is the defect it is meant to fix, spelled with
extra steps.
"""

from __future__ import annotations

from typing import Any

import pytest

from onex_change_control.serialization.ac_section import (
    BEGIN_MARKER,
    END_MARKER,
    RULE_UNENUMERATED_BINDING,
    AcSectionRefusalError,
    bound_labels,
    criteria_from_contract,
    plan_acceptance_criteria_update,
    render_acceptance_criteria_section,
    upsert_acceptance_criteria_section,
)
from onex_change_control.validation.ac_criteria import criteria_by_label

pytestmark = pytest.mark.unit


def _contract(
    *, criteria: list[tuple[str, str]], binds: list[str] | None = None
) -> dict[str, Any]:
    """A minimal contract carrying ``criteria`` and binding ``binds``."""
    contract: dict[str, Any] = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-00001",
        "title": "fixture",
        "requirements": [
            {
                "id": "req-1",
                "statement": "fixture requirement",
                "acceptance": [
                    {"id": label, "statement": statement}
                    for label, statement in criteria
                ],
            }
        ],
    }
    if binds is not None:
        contract["dod_evidence"] = [
            {
                "id": "dod-1",
                "description": "fixture evidence",
                "source": "manual",
                "binds_ac": binds,
                "checks": [{"check_type": "command", "check_value": "true"}],
            }
        ]
    return contract


def _eight() -> list[tuple[str, str]]:
    return [
        (f"AC{n}", f"criterion number {n} is settled by probe {n}") for n in range(1, 9)
    ]


# -- RED 1: eight labelled criteria in the model render as eight -------------


def test_eight_criteria_render_as_eight_and_read_back_as_eight() -> None:
    """A contract with eight labelled criteria renders eight, and the OMN-18236
    reader resolves all eight out of the rendered section.

    Rendering and reading are asserted together deliberately. Counting eight
    bullets would pass on a shape the gate cannot parse; the only count that
    means anything is the one the resolver itself returns.
    """
    contract = _contract(criteria=_eight(), binds=[f"AC{n}" for n in range(1, 9)])

    section = render_acceptance_criteria_section(contract)

    resolved = criteria_by_label(section)
    assert sorted(resolved) == [f"AC{n}" for n in range(1, 9)]
    for n in range(1, 9):
        assert f"criterion number {n} is settled by probe {n}" in resolved[f"AC{n}"]


def test_criteria_from_contract_preserves_declaration_order() -> None:
    contract = _contract(criteria=_eight())
    assert [label for label, _ in criteria_from_contract(contract)] == [
        f"AC{n}" for n in range(1, 9)
    ]


def test_bound_labels_canonicalises_contract_spellings() -> None:
    """``ac-3`` in a contract and ``AC3`` in a body are the same label.

    Canonicalisation goes through the reader's own ``canonical_ac_label``, so
    the writer cannot normalise differently from the gate that reads it back.
    """
    contract = _contract(
        criteria=[("AC1", "first"), ("AC3", "third")],
        binds=["ac-3", "AC 1", "ac_1"],
    )
    assert bound_labels(contract) == ["AC1", "AC3"]


# -- RED 2: a body already carrying the section is updated in place ----------


def test_body_carrying_the_section_is_updated_in_place() -> None:
    """A changed model rewrites only the managed section; the rest survives byte
    for byte, above and below."""
    before = _contract(criteria=[("AC1", "the old wording")], binds=["AC1"])
    after = _contract(criteria=[("AC1", "the new wording")], binds=["AC1"])

    preamble = "## Source\n\nSome prose the lane must not touch.\n\n"
    trailer = "\n\n## Do not invent\n\nOnly the framing from the source row.\n"

    # A body that already carries the managed section BETWEEN two prose blocks,
    # which is where a second run finds it: the append path puts it last, and
    # every run after that must rewrite it where it sits.
    first = preamble + render_acceptance_criteria_section(before) + trailer
    assert "the old wording" in first

    second = upsert_acceptance_criteria_section(
        first, render_acceptance_criteria_section(after)
    )
    assert second.startswith(preamble)
    assert second.endswith(trailer)
    assert "the new wording" in second
    assert "the old wording" not in second
    assert second.count(BEGIN_MARKER) == 1
    assert second.count(END_MARKER) == 1
    # Everything outside the managed span is untouched.
    assert "Some prose the lane must not touch." in second
    assert "Only the framing from the source row." in second


def test_rerunning_on_an_unchanged_model_changes_nothing() -> None:
    """Idempotence, asserted as byte equality and as a ``changed`` flag.

    A planner that rewrote an identical section would mint a Linear revision
    per sweep and make every ticket look edited on a day nobody edited one.
    """
    contract = _contract(criteria=_eight(), binds=[f"AC{n}" for n in range(1, 9)])
    body = "## Source\n\nprose\n"

    once = plan_acceptance_criteria_update("OMN-00001", contract, body)
    assert once.changed is True

    twice = plan_acceptance_criteria_update("OMN-00001", contract, once.new_body)
    assert twice.changed is False
    assert twice.new_body == once.new_body


def test_section_is_appended_when_the_body_has_none() -> None:
    contract = _contract(criteria=[("AC1", "the only criterion")], binds=["AC1"])
    plan = plan_acceptance_criteria_update(
        "OMN-00001", contract, "## Source\n\nprose\n"
    )

    assert plan.changed is True
    assert plan.new_body.startswith("## Source")
    assert BEGIN_MARKER in plan.new_body
    assert criteria_by_label(plan.new_body)["AC1"].endswith("the only criterion")


# -- RED 3: a label bound in the contract but absent from the model is refused


def test_label_bound_but_absent_from_the_model_is_refused() -> None:
    """The OMN-18167 shape: the contract binds AC1..AC8 and the model enumerates
    none of them. Refused, and the message names every missing label.

    This is the refusal that makes the process safe to run unattended. Without
    it the serializer would render an empty section over a ticket whose contract
    claims eight criteria, and the gate would read zero — a silent downgrade
    dressed as a repair.
    """
    contract = _contract(criteria=[], binds=[f"AC{n}" for n in range(1, 9)])

    with pytest.raises(AcSectionRefusalError) as excinfo:
        render_acceptance_criteria_section(contract)

    message = str(excinfo.value)
    for n in range(1, 9):
        assert f"AC{n}" in message


def test_partially_absent_bound_label_is_refused() -> None:
    """Binding four labels against a model that enumerates two is refused too.

    Partial is the dangerous shape, not total absence: a section carrying AC1
    and AC2 reads as a complete rendering, and nothing downstream distinguishes
    "the model has two" from "the model has four and two went missing".
    """
    contract = _contract(
        criteria=[("AC1", "first"), ("AC2", "second")],
        binds=["AC1", "AC2", "AC3", "AC4"],
    )
    with pytest.raises(AcSectionRefusalError) as excinfo:
        render_acceptance_criteria_section(contract)
    assert "AC3" in str(excinfo.value)
    assert "AC4" in str(excinfo.value)


def test_criterion_whose_id_is_not_a_label_is_refused() -> None:
    """A criterion the grammar cannot label can never be bound, so rendering it
    would put an unbindable line in the body and call the ticket serialized."""
    contract = _contract(criteria=[("first-thing", "some criterion")])
    with pytest.raises(AcSectionRefusalError) as excinfo:
        render_acceptance_criteria_section(contract)
    assert "first-thing" in str(excinfo.value)


def test_duplicate_labels_in_the_model_are_refused() -> None:
    contract = _contract(criteria=[("AC1", "first"), ("ac_1", "first again")])
    with pytest.raises(AcSectionRefusalError) as excinfo:
        render_acceptance_criteria_section(contract)
    assert "AC1" in str(excinfo.value)


def test_unmanaged_acceptance_section_in_the_body_is_refused() -> None:
    """A hand-written criteria section outside the markers stops the write.

    Eight of OMN-18270's nine tickets were repaired by hand on 2026-09-21.
    Appending a managed section to one of those would leave the body with two
    criteria sections, and the reader reads BOTH — two texts under one label,
    with the first occurrence silently winning. A human decides which survives.
    """
    contract = _contract(criteria=[("AC1", "from the contract")], binds=["AC1"])
    body = "## Acceptance criteria\n\n* AC1: typed in by hand\n"

    with pytest.raises(AcSectionRefusalError) as excinfo:
        plan_acceptance_criteria_update("OMN-00001", contract, body)
    assert "unmanaged" in str(excinfo.value).lower()


def test_managed_section_is_not_mistaken_for_an_unmanaged_one() -> None:
    """The guard above must not fire on the section this module itself wrote."""
    contract = _contract(criteria=[("AC1", "from the contract")], binds=["AC1"])
    first = plan_acceptance_criteria_update("OMN-00001", contract, "prose\n")
    again = plan_acceptance_criteria_update("OMN-00001", contract, first.new_body)
    assert again.changed is False


# -- the writer and the gate's reader agree ----------------------------------


def test_rendered_labels_survive_the_readers_awkward_shapes() -> None:
    """Round-trip through the reader for label spellings the corpus contains.

    ``canonical_ac_label`` accepts a single-letter suffix (OMN-18356), so a
    split criterion ``AC2b`` must render and read back as ``AC2b`` and not
    collapse into ``AC2``.
    """
    contract = _contract(
        criteria=[
            ("AC1", "plain"),
            ("AC2b", "a split criterion"),
            ("DoD3", "a dod one"),
        ],
    )
    section = render_acceptance_criteria_section(contract)
    resolved = criteria_by_label(section)
    assert sorted(resolved) == ["AC1", "AC2b", "DOD3"]


def test_a_statement_that_repeats_its_own_label_is_not_double_labelled() -> None:
    contract = _contract(criteria=[("AC1", "AC1: the criterion text")])
    section = render_acceptance_criteria_section(contract)
    assert "AC1: the criterion text" not in section
    assert criteria_by_label(section)["AC1"].endswith("the criterion text")


# -- the CLI ----------------------------------------------------------------


def _write_contract(tmp_path: Any, ticket: str, contract: dict[str, Any]) -> Any:
    import yaml

    path = tmp_path / f"{ticket}.yaml"
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    return path


def test_cli_emits_a_diff_and_the_body_to_apply(tmp_path: Any) -> None:
    """The dry run prints the diff AND writes the exact bytes an apply uses.

    One call produces both, so the diff a reviewer reads and the body a lane
    writes to Linear cannot be computed from different inputs.
    """
    import json

    from onex_change_control.scripts.serialize_ac_section import main

    contract = _contract(criteria=[("AC1", "the criterion")], binds=["AC1"])
    path = _write_contract(tmp_path, "OMN-00001", contract)
    bodies = tmp_path / "bodies.json"
    bodies.write_text(
        json.dumps({"OMN-00001": "## Source\n\nprose\n"}), encoding="utf-8"
    )
    out = tmp_path / "out"

    code = main([str(path), "--ticket-bodies", str(bodies), "--emit-body", str(out)])

    assert code == 0
    written = (out / "OMN-00001.md").read_text(encoding="utf-8")
    assert "the criterion" in written
    assert criteria_by_label(written)["AC1"].endswith("the criterion")


def test_cli_exits_one_on_the_omn_18167_shape(tmp_path: Any, capsys: Any) -> None:
    """A block binding against an unenumerated model is exit 1, not a warning."""
    import json

    from onex_change_control.scripts.serialize_ac_section import main

    contract = _contract(criteria=[], binds=[f"AC{n}" for n in range(1, 9)])
    path = _write_contract(tmp_path, "OMN-18167", contract)
    bodies = tmp_path / "bodies.json"
    bodies.write_text(
        json.dumps({"OMN-18167": "## Source\n\nprose\n"}), encoding="utf-8"
    )

    code = main([str(path), "--ticket-bodies", str(bodies)])

    assert code == 1
    captured = capsys.readouterr().out
    assert "ac_section_unenumerated_binding" in captured
    assert "AC8" in captured


def test_cli_refuses_a_ticket_with_no_body_rather_than_skipping_it(
    tmp_path: Any, capsys: Any
) -> None:
    """An absent body is RED. "I could not read the ticket" must not resolve to
    "so it needs no change" — the same direction the OMN-18236 gate fails."""
    import json

    from onex_change_control.scripts.serialize_ac_section import main

    contract = _contract(criteria=[("AC1", "the criterion")], binds=["AC1"])
    path = _write_contract(tmp_path, "OMN-00002", contract)
    bodies = tmp_path / "bodies.json"
    bodies.write_text(json.dumps({}), encoding="utf-8")

    code = main([str(path), "--ticket-bodies", str(bodies)])

    assert code == 1
    assert "ac_section_ticket_unreadable" in capsys.readouterr().out


def test_cli_reports_unchanged_without_rewriting(tmp_path: Any, capsys: Any) -> None:
    import json

    from onex_change_control.scripts.serialize_ac_section import main

    contract = _contract(criteria=[("AC1", "the criterion")], binds=["AC1"])
    path = _write_contract(tmp_path, "OMN-00003", contract)
    settled = plan_acceptance_criteria_update("OMN-00003", contract, "prose\n").new_body
    bodies = tmp_path / "bodies.json"
    bodies.write_text(json.dumps({"OMN-00003": settled}), encoding="utf-8")

    code = main([str(path), "--ticket-bodies", str(bodies)])

    assert code == 0
    assert "UNCHANGED OMN-00003" in capsys.readouterr().out


# -- The gate itself, not a stand-in for it ---------------------------------
#
# Every test above resolves the rendered section with `criteria_by_label`, the
# OMN-18236 READER. That is the right unit boundary, but it is not the thing
# that blocks a merge: `check-ac-binding-acceptance` is. The two below drive
# that CLI end to end, so the claim "serializing this body satisfies the gate"
# is enforced here instead of asserted in a pull-request body, and a future
# change to either side that breaks the pairing fails in CI rather than
# silently rendering a section the gate declines to read.
#
# They are a matched pair on purpose. The positive control proves the gate
# flips RED -> GREEN; the negative control proves a REFUSED contract leaves it
# RED, so a refusal can never be mistaken for a repair.


def _gate(contract_path: Any, bodies_path: Any) -> int:
    from onex_change_control.scripts.check_ac_binding_acceptance import main as gate

    return gate([str(contract_path), "--ticket-bodies", str(bodies_path)])


def test_serialization_flips_the_real_omn_18236_gate_from_refusal_to_pass(
    tmp_path: Any,
) -> None:
    """POSITIVE CONTROL, through the merge-blocking CLI rather than its reader.

    Before serialization the gate refuses with ``ac_binding_ticket_unlabelled``
    -- the contract binds criteria the body does not label. After, it exits 0
    on bytes this module produced and nobody edited.
    """
    import json

    contract = _contract(criteria=_eight(), binds=[label for label, _ in _eight()])
    path = _write_contract(tmp_path, "OMN-00042", contract)

    before = tmp_path / "before.json"
    before.write_text(json.dumps({"OMN-00042": "prose only\n"}), encoding="utf-8")
    assert _gate(path, before) == 1

    plan = plan_acceptance_criteria_update("OMN-00042", contract, "prose only\n")
    after = tmp_path / "after.json"
    after.write_text(json.dumps({"OMN-00042": plan.new_body}), encoding="utf-8")

    assert _gate(path, after) == 0


def test_a_refused_contract_leaves_the_gate_refusing(tmp_path: Any) -> None:
    """NEGATIVE CONTROL: the OMN-18167 shape, end to end.

    The contract binds ``AC1``..``AC8`` and enumerates none. The serializer
    refuses, nothing is written, and the gate is still RED afterwards. Without
    this, a refusal that quietly emitted an empty section would read as a pass
    on the positive control alone.
    """
    import json

    contract = _contract(criteria=[], binds=[label for label, _ in _eight()])
    path = _write_contract(tmp_path, "OMN-00043", contract)
    bodies = tmp_path / "bodies.json"
    bodies.write_text(json.dumps({"OMN-00043": "prose only\n"}), encoding="utf-8")

    assert _gate(path, bodies) == 1

    with pytest.raises(AcSectionRefusalError) as refusal:
        plan_acceptance_criteria_update("OMN-00043", contract, "prose only\n")
    assert refusal.value.rule == RULE_UNENUMERATED_BINDING

    # The body was never rewritten, so the gate's verdict is unchanged.
    assert _gate(path, bodies) == 1
