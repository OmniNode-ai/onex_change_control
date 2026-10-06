# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-19046 — the first contract in the corpus whose binding has a model.

OMN-18270 landed the writer that renders a contract's
``requirements[].acceptance[]`` into a ticket's acceptance-criteria section, and
it could not repair a single ticket: 0 of 9,275 contracts carried a
``requirements`` key, so the writer had no input anywhere. ``contracts/OMN-18167.yaml``
is the first that does, and it is the ticket the operator named as the first case.

These tests pin the CONTRACT, not the module. The module's own behaviour is
covered by ``tests/test_omn_18270_ac_section_serializer.py``; what is asserted
here is that this particular contract's model and its binding agree, and that
the agreement survives the round trip through the reader that actually blocks
merges.

**Why the fixtures are real bytes and not a synthetic body.** Both fixtures were
fetched from Linear rather than written by hand:
``omn_18167_body_before_serialization.md`` is the body as Linear held it before
this work, and ``omn_18167_body_as_linear_normalised_it.md`` is what came back
after the serializer's output was applied. The difference between the second
fixture and the serializer's own output is the finding that only an actual write
could produce: **Linear's markdown normaliser rewrites the managed section on
save.** ``- `` bullets become ``* `` and ``_emphasis_`` becomes ``*emphasis*``.
The criterion text is untouched, and the last test below is the one that says so
in a way a future change cannot quietly break — because if the reader ever stops
tolerating the bullet Linear chooses, every serialized ticket silently resolves
to nothing, and the gate goes green for the wrong reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from onex_change_control.serialization.ac_section import (
    bound_labels,
    criteria_from_contract,
    plan_acceptance_criteria_update,
    render_acceptance_criteria_section,
)
from onex_change_control.validation.ac_binding_acceptance import (
    check_contract_ac_bindings,
    refusals,
)
from onex_change_control.validation.ac_criteria import criteria_by_label

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONTRACT_PATH = _REPO_ROOT / "contracts" / "OMN-18167.yaml"
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "omn_19046"
_BODY_BEFORE = _FIXTURES / "omn_18167_body_before_serialization.md"
_BODY_AS_LINEAR_STORES_IT = _FIXTURES / "omn_18167_body_as_linear_normalised_it.md"

_TICKET_ID = "OMN-18167"

#: The exact character counts Linear reported for each body, recorded at the
#: moment each was fetched. Pinned so an edit to a fixture is a failing test
#: rather than a quiet change to what "what Linear stores" means.
_CHARS_BEFORE = 2186
_CHARS_AFTER = 5883


def _fixture(path: Path) -> str:
    """A fixture's text as LINEAR holds it, not as this repository stores it.

    The repository's end-of-file hook appends a trailing newline to every
    committed text file and Linear's stored body carries none, so one newline
    is stripped back off here. Committing the file without it is not an option:
    the hook re-adds it on the next commit that touches this directory, and a
    fixture that drifts every time somebody formats the tree is worse than one
    whose single known difference is named in one place.
    """
    text = path.read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


@pytest.fixture(scope="module")
def contract() -> dict[str, object]:
    """The live contract, parsed as the gate CLIs parse it — plain YAML.

    Read off disk rather than inlined, so the test fails when the contract
    changes and nobody re-derived the body from it. A copy of the criteria in
    this file would be a second source of truth for the exact thing this ticket
    exists to give one source of truth to.
    """
    loaded = yaml.safe_load(_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.unit
def test_every_bound_label_is_enumerated_by_the_models_own_criteria(
    contract: dict[str, object],
) -> None:
    """The defect this ticket closes, asserted directly.

    From PR #9240 on 2026-09-12 until now this contract bound ``AC1``..``AC8``
    against a model that enumerated nothing, which the 2026-09-12 closeout
    adjudication called "eight names with no referents". Equality is asserted in
    both directions on purpose: a criterion nothing binds is itself a refusal
    under ``ac_binding_criterion_unbound``, so a ninth criterion added to cover
    one of the check's provenance pins would break this contract rather than
    improve it.
    """
    declared = {label for label, _ in criteria_from_contract(contract)}
    bound = set(bound_labels(contract))

    assert bound == {f"AC{n}" for n in range(1, 9)}
    assert declared == bound


@pytest.mark.unit
def test_each_criterion_names_the_check_that_would_settle_it(
    contract: dict[str, object],
) -> None:
    """Every criterion carries a falsifier, which is what makes it answerable.

    The admission guard's own predicate. A criterion with no named falsifier is
    prose that reads like a requirement, and this contract's whole problem was
    labels that read like criteria. Asserted on the rendered statements rather
    than the raw YAML so it measures what a reader of the ticket actually gets.
    """
    for label, statement in criteria_from_contract(contract):
        assert "falsifier:" in statement.casefold(), label


@pytest.mark.unit
def test_the_rendered_section_reads_back_as_the_eight_labels_it_binds(
    contract: dict[str, object],
) -> None:
    """The round trip, through the reader the gate and the closer both use.

    Asserted on ``criteria_by_label``'s output and never on a bullet count: a
    section rendered in a shape its own reader could not parse would look
    serialized and resolve nothing.
    """
    section = render_acceptance_criteria_section(contract)
    resolved = criteria_by_label(section)

    assert set(resolved) == set(bound_labels(contract))
    for label, text in resolved.items():
        assert text.strip(), label


@pytest.mark.unit
def test_the_gate_refuses_the_body_as_it_was_and_passes_the_serialized_one(
    contract: dict[str, object],
) -> None:
    """The end-to-end claim, driven through the merge-blocking validator.

    The "after" body is taken from ``plan_acceptance_criteria_update`` rather
    than typed here, so no hand edit can sit between the refusal and the pass.
    This is the four-step control the ledger records, collapsed into one test
    that fails if any step stops being true.
    """
    before = _fixture(_BODY_BEFORE)
    assert len(before) == _CHARS_BEFORE

    refused_before = refusals(check_contract_ac_bindings(_TICKET_ID, contract, before))
    assert refused_before, "the pre-serialization body must still refuse"
    assert any(
        finding.rule == "ac_binding_ticket_unlabelled" for finding in refused_before
    )

    plan = plan_acceptance_criteria_update(_TICKET_ID, contract, before)
    assert plan.changed
    assert plan.labels == tuple(f"AC{n}" for n in range(1, 9))

    refused_after = refusals(
        check_contract_ac_bindings(_TICKET_ID, contract, plan.new_body)
    )
    assert refused_after == []


@pytest.mark.unit
def test_serialization_only_appends_and_leaves_the_prose_byte_for_byte(
    contract: dict[str, object],
) -> None:
    """Additive, which is what made it safe to apply to a ticket already Done.

    A serializer that reflowed the body would rewrite a closed ticket's history
    for a cosmetic reason. The original text must survive as a literal prefix,
    not merely be "still present somewhere".
    """
    before = _fixture(_BODY_BEFORE)
    plan = plan_acceptance_criteria_update(_TICKET_ID, contract, before)

    assert plan.new_body.startswith(before.rstrip())
    assert len(plan.new_body) > len(before)


@pytest.mark.unit
def test_the_gate_still_passes_the_body_after_linear_normalised_it(
    contract: dict[str, object],
) -> None:
    """The finding only a real write could produce, pinned against regression.

    Linear rewrites the managed section on save: ``- `` bullets become ``* ``
    and ``_emphasis_`` becomes ``*emphasis*``. The criterion text is untouched,
    and the reader's list-item grammar accepts either bullet, so the binding
    still resolves. That is measured here rather than assumed, because the
    failure mode is silent: if the reader ever narrowed to one bullet character,
    every serialized ticket in the corpus would resolve to zero criteria while
    looking perfectly serialized in Linear.
    """
    stored = _fixture(_BODY_AS_LINEAR_STORES_IT)

    assert len(stored) == _CHARS_AFTER
    assert "\n* **AC1** " in stored, "fixture must carry Linear's own bullet"
    assert set(criteria_by_label(stored)) >= set(bound_labels(contract))
    assert refusals(check_contract_ac_bindings(_TICKET_ID, contract, stored)) == []
