# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-19046 — the one-time backfill, and the four things it must refuse.

OMN-19038 makes every NEWLY minted contract carry its criteria. The 370
contracts that already bind criteria carry none, and their text exists only in
the ticket bodies. This module pins the backfill that closes that, and most of
it is about the cases where writing something would be worse than writing
nothing.

The rule this module exists to enforce is not "populate as many contracts as
possible". It is: a model this writes must be the criteria the binding was
accepted against, or it must not be written. ``TestItRefusesRatherThanGuess``
is therefore the bulk of the file, and ``test_a_stale_pin_is_refused`` is the
sharpest of them — it is the one case where the backfill CAN produce text, the
text is real, and writing it would still be wrong.
"""

from __future__ import annotations

import hashlib

import pytest
import yaml

from onex_change_control.serialization.ac_requirements import (
    RULE_DUPLICATE_LABEL,
    RULE_STALE_PIN,
    RULE_TICKET_UNREADABLE,
    RULE_UNKNOWN_CRITERION,
    AcRequirementsRefusalError,
    ModelRequirementsPlan,
    plan_requirements_backfill,
)
from onex_change_control.validation.ac_criteria import criteria_by_label, criterion_hash

pytestmark = pytest.mark.unit


BODY = """Some preamble about the defect.

## Acceptance criteria

- [ ] AC1: the lookup is index-backed -- falsifier: a test EXPLAINs it, no Seq Scan
- [ ] AC2: the writer materialises rows again -- falsifier: max(window_start) moves
"""


def _contract_text(*, pins: dict[str, str] | None = None) -> str:
    records = ""
    if pins:
        records = "    ac_bindings:\n" + "".join(
            f'      - label: "{label}"\n'
            f'        criterion_hash: "{value}"\n'
            f'        proposed_by: "occ-autobind"\n'
            for label, value in pins.items()
        )
    return (
        "---\n"
        'schema_version: "1.0.0"\n'
        'ticket_id: "OMN-19046"\n'
        'title: "Autobind OCC evidence for OMN-19046"\n'
        "evidence_requirements:\n"
        '  - kind: "ci"\n'
        '    description: "diff scope present"\n'
        '    command: "gh pr view 1 --repo o/r --json files"\n'
        "dod_evidence:\n"
        '  - id: "dod-o-r-pr-1"\n'
        '    description: "PR #1."\n'
        '    source: "generated"\n'
        '    binds_ac: ["AC1", "AC2"]\n' + records + "    checks:\n"
        '      - check_type: "command"\n'
        '        check_value: "gh pr view 1 --repo o/r --json number,state"\n'
    )


def _live_pins() -> dict[str, str]:
    known = criteria_by_label(BODY)
    return {label: criterion_hash(text) for label, text in known.items()}


def _plan(text: str, body: str | None) -> ModelRequirementsPlan:
    return plan_requirements_backfill("OMN-19046", yaml.safe_load(text), text, body)


class TestItFillsAnAbsentModel:
    """The headline: the criteria the binding names reach the contract."""

    def test_the_bound_criteria_are_written_in_binding_order(self) -> None:
        plan = _plan(_contract_text(), BODY)
        assert plan.changed is True
        assert plan.labels == ("AC1", "AC2")
        parsed = yaml.safe_load(plan.new_text)
        criteria = parsed["requirements"][0]["acceptance"]
        assert [c["id"] for c in criteria] == ["AC1", "AC2"]

    def test_the_statement_is_the_bodys_own_text(self) -> None:
        plan = _plan(_contract_text(), BODY)
        parsed = yaml.safe_load(plan.new_text)
        written = parsed["requirements"][0]["acceptance"][0]["statement"]
        assert written == criteria_by_label(BODY)["AC1"]
        assert "Seq Scan" in written

    def test_the_written_model_hashes_to_the_pin_the_gate_recomputes(self) -> None:
        """The property that makes the backfill trustworthy at all.

        The gate resolves a binding by recomputing the criterion's hash from
        the ticket body. If what this writes does not hash to that, the model
        and the binding describe different sentences.
        """
        plan = _plan(_contract_text(), BODY)
        parsed = yaml.safe_load(plan.new_text)
        for criterion in parsed["requirements"][0]["acceptance"]:
            live = criteria_by_label(BODY)[criterion["id"]]
            assert criterion_hash(criterion["statement"]) == criterion_hash(live)

    def test_every_other_byte_of_the_contract_is_untouched(self) -> None:
        """A backfill that reformats 370 files is a diff nobody reviews."""
        original = _contract_text()
        plan = _plan(original, BODY)
        assert plan.new_text.count("requirements:") == 2  # ours + evidence_
        without_block = plan.new_text.replace(
            plan.new_text[
                plan.new_text.index("requirements:\n") : plan.new_text.index(
                    "evidence_requirements:"
                )
            ],
            "",
        )
        assert without_block == original

    def test_a_contract_binding_nothing_is_left_alone(self) -> None:
        text = _contract_text().replace('    binds_ac: ["AC1", "AC2"]\n', "")
        plan = _plan(text, BODY)
        assert plan.changed is False
        assert "requirements:\n" not in plan.new_text


class TestItIsIdempotentThroughTheReader:
    """OMN-19054's rule: compare meaning, never bytes."""

    def test_running_it_twice_writes_nothing_the_second_time(self) -> None:
        first = _plan(_contract_text(), BODY)
        assert first.changed is True
        second = _plan(first.new_text, BODY)
        assert second.changed is False
        assert second.new_text == first.new_text

    def test_a_body_whose_bullets_were_renormalised_still_compares_equal(self) -> None:
        """Linear rewrites ``- `` to ``* `` on save, and that is not an edit.

        A byte comparison would rewrite every contract on every pass. The
        reader consumes the bullet, so the two forms are the same criterion.
        """
        first = _plan(_contract_text(), BODY)
        renormalised = BODY.replace("- [ ] AC", "* [ ] AC")
        second = _plan(first.new_text, renormalised)
        assert second.changed is False


class TestItRefusesRatherThanGuess:
    """Four refusals. Each one is a case where writing would look like a fix."""

    def test_an_absent_body_is_refused_not_skipped(self) -> None:
        with pytest.raises(AcRequirementsRefusalError) as caught:
            _plan(_contract_text(), None)
        assert caught.value.rule == RULE_TICKET_UNREADABLE
        assert "OMN-19046" in str(caught.value)

    def test_a_bound_label_the_body_lacks_is_refused(self) -> None:
        body = BODY.replace("AC2:", "AC7:")
        with pytest.raises(AcRequirementsRefusalError) as caught:
            _plan(_contract_text(), body)
        assert caught.value.rule == RULE_UNKNOWN_CRITERION
        assert "AC2" in str(caught.value)
        assert "OMN-19046" in str(caught.value)

    def test_two_criteria_canonicalising_to_one_label_are_refused(self) -> None:
        """OMN-19046 AC3, verbatim: the refusal names the ticket and the rule."""
        body = (
            BODY
            + "- [ ] AC-1: a different criterion under one label"
            + " -- falsifier: something else\n"
        )
        with pytest.raises(AcRequirementsRefusalError) as caught:
            _plan(_contract_text(), body)
        assert caught.value.rule == RULE_DUPLICATE_LABEL
        assert "OMN-19046" in str(caught.value)
        assert "AC1" in str(caught.value)

    def test_a_pin_matching_the_first_text_arbitrates_the_duplicate(self) -> None:
        """A recorded acceptance answers the question, so this is not a guess.

        `criterion_hash` on a binding record is the digest of the criterion at
        the moment a person accepted it. When it matches the text the reader
        resolves, the duplicate is settled by evidence. Measured on the live
        corpus, every contract that reaches this branch has a lane's verdict
        annotation written into the criteria section as the second variant.
        """
        body = (
            BODY + "- [ ] AC1 MET 2026-09-15T17:48Z on the worker -- falsifier: n/a\n"
        )
        with pytest.raises(AcRequirementsRefusalError):
            _plan(_contract_text(), body)
        plan = _plan(_contract_text(pins=_live_pins()), body)
        assert plan.changed is True
        parsed = yaml.safe_load(plan.new_text)
        written = parsed["requirements"][0]["acceptance"][0]["statement"]
        assert written == criteria_by_label(body)["AC1"]
        assert "MET 2026-09-15" not in written

    def test_an_annotation_outside_the_section_is_not_a_duplicate_at_all(self) -> None:
        """The corpus correction, pinned.

        A writer that treated a whole-body fallback line as a competing
        variant for a label the SECTION already resolved refused 48 of 374
        contracts on 2026-09-21, almost none of them ambiguous. The reader
        skips the fallback for a resolved label, and so must this.
        """
        body = BODY + "\n## Notes\n\nAC1 MET 2026-09-15T17:48Z on the worker.\n"
        plan = _plan(_contract_text(), body)
        assert plan.changed is True
        parsed = yaml.safe_load(plan.new_text)
        written = parsed["requirements"][0]["acceptance"][0]["statement"]
        assert "MET 2026-09-15" not in written

    def test_the_duplicate_rule_reads_past_the_readers_first_wins_collapse(
        self,
    ) -> None:
        """The control that makes the rule above mean something.

        ``criteria_by_label`` drops the second criterion before any writer
        sees it. A duplicate check built on that map cannot fire at all, and
        would pass its own test by never being reached. This asserts the
        single-criterion body is still accepted, so the rule above is
        discriminating rather than refusing everything.
        """
        plan = _plan(_contract_text(), BODY)
        assert plan.changed is True

    def test_a_stale_pin_is_refused(self) -> None:
        """The sharpest case: real text, and writing it would still be wrong.

        The binding was accepted against text the body no longer carries.
        Writing the live text produces a model that disagrees with the pin
        beside it, and it reads as a repair of a binding nobody re-accepted.
        """
        stale = {"AC1": hashlib.sha256(b"what AC1 used to say").hexdigest()}
        with pytest.raises(AcRequirementsRefusalError) as caught:
            _plan(_contract_text(pins=stale), BODY)
        assert caught.value.rule == RULE_STALE_PIN
        assert "AC1" in str(caught.value)

    def test_a_current_pin_is_not_mistaken_for_a_stale_one(self) -> None:
        """The positive control under the rule above.

        Without this, a stale-pin rule that refused unconditionally would pass
        its own test and block the entire backfill.
        """
        plan = _plan(_contract_text(pins=_live_pins()), BODY)
        assert plan.changed is True
        assert plan.labels == ("AC1", "AC2")


class TestItDoesNotOverwriteAnAuthoredModel:
    """A model somebody wrote is an edit to replace, not a gap to fill."""

    def test_a_disagreeing_existing_model_is_left_alone(self) -> None:
        filled = _plan(_contract_text(), BODY).new_text
        tampered = filled.replace("the dev plane", "SOMETHING A PERSON WROTE")
        plan = _plan(tampered, BODY)
        assert plan.changed is False
        assert plan.new_text == tampered
