# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18236 — a `binds_ac` claim is now answerable to the ticket it names.

Sixty-one contracts declare `binds_ac`, carrying 374 label entries, read live
from this repository's default branch on 2026-09-12. Every one of them is an
unchecked assertion: the schema can see that `AC1` is a well-formed label, and
nothing anywhere has ever asked whether the ticket HAS an AC1, or noticed when
AC1's text was rewritten under a check that is still green.

AC1 of the ticket, in its own words: *an item declaring a criterion not present
in the ticket, or pinned to a stale criterion hash, fails its own compliance
check; red on a fixture, green once re-accepted.* Both halves are measured here,
and the re-acceptance is a real second call rather than a claim in prose.

The ported-reader tests are the other load-bearing half. The criterion reader in
`onex_change_control.validation.ac_criteria` is a port of the evidence closer's
own reader in another repository that cannot be imported from here, so a shape
one side reads and the other does not is a binding that silently never joins.
The awkward shapes both sides were measured against are pinned below, by name.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import yaml
from pydantic import ValidationError

from onex_change_control.models.model_ac_binding import ModelAcBinding
from onex_change_control.models.model_dod_check import ModelDodEvidenceItem
from onex_change_control.validation.ac_binding_acceptance import (
    AcBindingFinding,
    check_contract_ac_bindings,
    check_local_ac_bindings,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

from onex_change_control.validation.ac_criteria import (
    acceptance_criteria_items,
    canonical_ac_label,
    criteria_by_label,
    criterion_hash,
    is_ac_heading,
    normalise_criterion,
)

_TICKET = "OMN-19999"

TICKET_BODY = """## Summary

Some prose that is not a criterion.

## Acceptance criteria

- AC1: the gate refuses a claim naming a criterion the ticket does not have.
- AC2: the gate refuses a binding pinned to a criterion that has been rewritten.
"""

REWRITTEN_TICKET_BODY = TICKET_BODY.replace(
    "AC2: the gate refuses a binding pinned to a criterion that has been rewritten.",
    "AC2: the gate ACCEPTS a binding pinned to a criterion that has been rewritten.",
)


def _hash_of(body: str, label: str) -> str:
    return criterion_hash(criteria_by_label(body)[label])


def _contract(
    *,
    claims: list[str],
    bindings: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": "dod-001",
        "description": "Proves the criteria it claims.",
        "source": "manual",
        "status": "verified",
        "binds_ac": claims,
        "checks": [{"check_type": "command", "check_value": "true"}],
    }
    if bindings is not None:
        item["ac_bindings"] = bindings
    return {
        "schema_version": "1.0.0",
        "ticket_id": _TICKET,
        "title": "fixture",
        "dod_evidence": [item],
    }


def _rules(findings: Sequence[AcBindingFinding]) -> list[str]:
    return [f.rule for f in findings]


# ------------------------------------------------------- the criterion reader --


class TestCriterionReader:
    """Ported from the closer. A shape one side reads and the other does not
    is a binding that silently never joins, so the shapes are pinned by name."""

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("- **AC1** - the emphasised bullet shape", "AC1"),
            ("- AC-2: the hyphenated shape", "AC2"),
            ("3) AC 3 the spaced shape", "AC3"),
            ("- DoD4 -- the definition-of-done spelling", "DOD4"),
            ("- [ ] AC5: the unchecked-task shape", "AC5"),
            ("- a criterion with no label at all", ""),
        ],
    )
    def test_each_measured_shape_parses_to_the_label_it_carries(
        self, line: str, expected: str
    ) -> None:
        body = "## Acceptance criteria\n\n" + line + "\n"
        items = acceptance_criteria_items(body)

        assert items, body
        assert canonical_ac_label(items[0]) == expected

    @pytest.mark.parametrize(
        "heading",
        [
            "## Acceptance criteria",
            "**Acceptance criteria:**",
            "### 3. Acceptance criteria",
            "## Acceptance criteria (falsifiable)",
            "## Falsifiable acceptance criteria",
            "## Definition of done",
            "## DoD",
        ],
    )
    def test_every_measured_heading_spelling_opens_the_section(
        self, heading: str
    ) -> None:
        assert is_ac_heading(heading)

    def test_a_heading_that_merely_mentions_the_word_does_not_open_a_section(
        self,
    ) -> None:
        """`## Notes on AC` names no section. Opening one there reads unrelated
        bullets as criteria, which is how a count of 2 was reported for a
        ticket declaring 4."""
        assert not is_ac_heading("## Notes on AC")
        assert not is_ac_heading("## Why we need DoD")

    def test_the_section_ends_at_the_next_heading(self) -> None:
        body = (
            "## Acceptance criteria\n\n- AC1: the real one\n\n"
            "## Fence\n\n- not a criterion\n"
        )

        assert acceptance_criteria_items(body) == ["AC1: the real one"]

    def test_a_second_criteria_section_is_read_too(self) -> None:
        """The divergence from the closer, pinned.

        Measured on the live corpus: OMN-18186 carries AC1 to AC4 under
        `## Acceptance criteria` and AC5, AC6 under a later
        `### Added acceptance criteria`. The closer stops at the first
        non-criteria heading and reads four, so its contract's two remaining
        bindings read as claims on criteria the ticket does not have. It has
        six.
        """
        body = (
            "## Acceptance criteria\n\n- AC1: the first\n\n"
            "## Some other section\n\n- not a criterion\n\n"
            "### Added acceptance criteria\n\n- AC2: added later\n"
        )

        assert sorted(criteria_by_label(body)) == ["AC1", "AC2"]

    def test_a_labelled_criterion_under_an_unrecognisable_heading_is_still_found(
        self,
    ) -> None:
        """The whole-body fallback, pinned.

        Measured on the live corpus: OMN-17907 writes AC4 and AC5 under
        `## Second finding, folded in`, and says in the body that they are full
        acceptance criteria. Refusing to see them would report a real,
        author-written criterion as fabricated.
        """
        body = (
            "## Acceptance criteria\n\n- AC1: the first\n\n"
            "## Second finding, folded in\n\n- **AC4.** the folded-in one\n"
        )

        resolved = criteria_by_label(body)

        assert sorted(resolved) == ["AC1", "AC4"]

    def test_the_fallback_cannot_invent_a_criterion(self) -> None:
        """Over-inclusive about WHERE, never about WHETHER.

        A label that appears nowhere in the body resolves to nothing, which is
        what keeps the fallback from turning the gate into a rubber stamp.
        """
        body = "## Acceptance criteria\n\n- AC1: the only one\n"

        assert "AC7" not in criteria_by_label(body)

    def test_both_passes_produce_the_same_text_for_one_criterion(self) -> None:
        """One extractor, so a criterion cannot have two hashes.

        If the section pass and the fallback disagreed about a criterion's
        text, a binding would go stale for no reason but which pass happened to
        find it first.
        """
        line = "- **AC1:** the criterion text"
        in_section = criteria_by_label("## Acceptance criteria\n\n" + line + "\n")
        via_fallback = criteria_by_label("## Unrelated heading\n\n" + line + "\n")

        assert criterion_hash(in_section["AC1"]) == criterion_hash(via_fallback["AC1"])

    def test_a_body_with_no_heading_is_read_whole(self) -> None:
        """The closer's own fallback. A ticket that lists criteria under a
        prose opener otherwise parses as having none at all."""
        body = "Acceptance is the chain being green.\n\n- AC1: the chain is green\n"

        assert canonical_ac_label(acceptance_criteria_items(body)[0]) == "AC1"


class TestCriterionHash:
    def test_reflowing_a_paragraph_is_not_a_rewrite(self) -> None:
        wrapped = "AC1: the gate refuses a claim\n   naming an absent criterion."
        flowed = "AC1: the gate refuses a claim naming an absent criterion."

        assert criterion_hash(wrapped) == criterion_hash(flowed)

    def test_a_wording_change_is_a_rewrite(self) -> None:
        assert criterion_hash("AC1: the gate refuses it.") != criterion_hash(
            "AC1: the gate accepts it."
        )

    def test_a_negation_is_a_rewrite(self) -> None:
        assert criterion_hash("AC1: the lane is green.") != criterion_hash(
            "AC1: the lane is not green."
        )

    def test_a_case_change_is_a_rewrite(self) -> None:
        """Not casefolded, deliberately: `MUST` and `may` are different
        requirements and a hash that equated them would pin nothing."""
        assert criterion_hash("AC1: the lane MUST be green.") != criterion_hash(
            "AC1: the lane may be green."
        )

    def test_the_hash_is_a_full_lowercase_sha256(self) -> None:
        value = criterion_hash("AC1: anything")

        assert len(value) == 64
        assert value == value.lower()

    def test_normalisation_is_bounded(self) -> None:
        assert len(normalise_criterion("x " * 10_000)) <= 4000


# ------------------------------------------------------------ the model rules --


class TestBindingModel:
    def test_a_draft_declares_neither_actor_nor_time(self) -> None:
        draft = ModelAcBinding(label="AC1", criterion_hash="a" * 64)

        assert draft.is_accepted is False

    def test_an_accepted_binding_declares_both(self) -> None:
        accepted = ModelAcBinding(
            label="AC1",
            criterion_hash="a" * 64,
            accepted_by="jonahgabriel",
            accepted_at="2026-09-12T18:04:20Z",
        )

        assert accepted.is_accepted is True

    def test_an_actor_with_no_time_is_refused(self) -> None:
        """Half an acceptance record reads as acceptance to a consumer
        checking only one field, so it may not exist."""
        with pytest.raises(ValidationError):
            ModelAcBinding(
                label="AC1", criterion_hash="a" * 64, accepted_by="jonahgabriel"
            )

    def test_a_time_with_no_actor_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ModelAcBinding(
                label="AC1",
                criterion_hash="a" * 64,
                accepted_at="2026-09-12T18:04:20Z",
            )

    def test_a_truncated_hash_is_refused(self) -> None:
        """A short hash collides sooner and reads as if it did not."""
        with pytest.raises(ValidationError):
            ModelAcBinding(label="AC1", criterion_hash="abc123")

    def test_a_malformed_label_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ModelAcBinding(label="the first one", criterion_hash="a" * 64)

    def test_a_binding_for_an_unclaimed_criterion_is_refused_by_the_item(
        self,
    ) -> None:
        with pytest.raises(ValidationError):
            ModelDodEvidenceItem.model_validate(
                {
                    "id": "dod-001",
                    "description": "x",
                    "binds_ac": ["AC1"],
                    "ac_bindings": [{"label": "AC2", "criterion_hash": "a" * 64}],
                }
            )

    def test_two_records_for_one_criterion_are_refused_by_the_item(self) -> None:
        with pytest.raises(ValidationError):
            ModelDodEvidenceItem.model_validate(
                {
                    "id": "dod-001",
                    "description": "x",
                    "binds_ac": ["AC1"],
                    "ac_bindings": [
                        {"label": "AC1", "criterion_hash": "a" * 64},
                        {"label": "ac-1", "criterion_hash": "b" * 64},
                    ],
                }
            )

    def test_an_item_declaring_no_bindings_is_unchanged(self) -> None:
        """Sixty-one live contracts declare `binds_ac` and no record. None of
        them may start failing because this field exists."""
        item = ModelDodEvidenceItem.model_validate(
            {"id": "dod-001", "description": "x", "binds_ac": ["AC1"]}
        )

        assert item.ac_bindings == ()


# ------------------------------------------- OMN-18356 — the suffixed grammar --


class TestSuffixedLabelGrammar:
    """The consumer half of OMN-18356.

    The producer (`omniclaude#2159`, `_CRITERION_LABEL`) already emits labels
    like `AC2b` and `AC10a`: a round split into `AC2b`/`AC2c`/... sits a letter
    directly after the ordinal digits, with no boundary between them (both are
    word characters), so a bare `(\\d+)\\b` never matched past the digits and
    the whole label was lost. The fix there adds an optional single-letter
    suffix group, captured verbatim (case preserved) because `AC2b` and `AC2B`
    are different labels, not the same criterion written twice — pinned by the
    producer's own `test_an_uppercase_suffix_is_preserved_as_written`.

    This class widens BOTH consumer regexes (`ModelAcBinding._AC_LABEL_RE` and
    `ac_criteria._AC_LABEL_RE`) to the identical grammar, so a label legal on
    one side is legal on the other and a criterion labelled `AC2b` in a ticket
    body can be bound by an `ac_bindings` record naming it.
    """

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("AC2b", "AC2b"),
            ("AC10a", "AC10a"),
            # Case of the AC/DOD token folds, same as a bare `ac2`; the suffix
            # letter's OWN case is preserved verbatim either way, so an
            # all-lowercase spelling produces the identical canonical label as
            # its mixed-case sibling.
            ("ac2b", "AC2b"),
            ("dod3c", "DOD3c"),
            # Uppercase suffix preserved distinctly -- NOT folded to "AC2b".
            ("AC2B", "AC2B"),
            # A plain label is unaffected: the suffix group matches zero
            # characters and the boundary check falls back to its original
            # position.
            ("AC2", "AC2"),
        ],
    )
    def test_canonical_ac_label_accepts_the_producer_grammar(
        self, label: str, expected: str
    ) -> None:
        assert canonical_ac_label(label) == expected

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            # Two letters is not "an optional single letter" -- no boundary
            # exists after either the first or the second, so the whole
            # match fails and the label is unparseable, same as before the
            # fix. Widening to the producer's grammar must not widen PAST it.
            ("AC2bb", ""),
            # A separator between the digits and the letter is not the
            # producer's shape either (the letter must sit directly adjacent
            # to the digits). The loose, non-end-anchored match this function
            # has always made falls back to the base ordinal, unaffected by
            # this change.
            ("AC2-b", "AC2"),
            # Leading-zero digit normalisation is pre-existing behaviour this
            # fix does not touch: `int()` on the digit group already strips
            # it, with or without a suffix.
            ("AC02", "AC2"),
        ],
    )
    def test_canonical_ac_label_does_not_widen_past_the_producer_grammar(
        self, label: str, expected: str
    ) -> None:
        assert canonical_ac_label(label) == expected

    @pytest.mark.parametrize("label", ["AC2b", "AC10a", "AC2B", "ac2b", "AC2"])
    def test_the_model_accepts_every_producer_legal_label(self, label: str) -> None:
        binding = ModelAcBinding(label=label, criterion_hash="a" * 64)

        # The model validates SHAPE and stores the value AS WRITTEN -- it does
        # not canonicalise, the same as it already does for a bare "ac1".
        assert binding.label == label

    @pytest.mark.parametrize("label", ["AC2bb", "AC2-b", "the first one"])
    def test_the_model_refuses_a_label_the_producer_grammar_does_not_produce(
        self, label: str
    ) -> None:
        with pytest.raises(ValidationError):
            ModelAcBinding(label=label, criterion_hash="a" * 64)

    def test_a_suffixed_criterion_is_bound_by_a_matching_ac_bindings_record(
        self,
    ) -> None:
        """End to end: a ticket declaring `AC2b` as its own criterion, and a
        contract claiming exactly `AC2b`, joins -- the whole point of the fix."""
        body = (
            "## Acceptance criteria\n\n"
            "- AC2: the base criterion.\n"
            "- AC2b: a distinct sibling criterion.\n"
        )
        contract = _contract(claims=["AC2", "AC2b"])

        findings = check_contract_ac_bindings(_TICKET, contract, body)

        assert findings == []

    def test_a_suffixed_criterion_is_not_satisfied_by_its_base_ordinals_claim(
        self,
    ) -> None:
        """The dangerous failure mode: a contract claiming `AC2` must not be
        read as having also claimed `AC2b` -- they are different criteria.
        Before this fix `AC2b` parsed as no label at all and was refused as
        `ac_binding_criterion_unbindable`; after it, it is a real declared
        criterion that this contract has left unbound."""
        body = (
            "## Acceptance criteria\n\n"
            "- AC2: the base criterion. — falsifier: uv run pytest a.py\n"
            "- AC2b: a distinct sibling criterion. — falsifier: uv run pytest b.py\n"
        )
        contract = _contract(claims=["AC2"])

        findings = check_contract_ac_bindings(_TICKET, contract, body)

        assert _rules(findings) == ["ac_binding_criterion_unbound"]
        assert "AC2b" in findings[0].message


# ---------------------------------------------------------------- AC1, the gate --


class TestUnknownCriterion:
    """AC1, first half: a claim naming a criterion the ticket does not have."""

    def test_a_claim_for_an_absent_criterion_is_red(self) -> None:
        findings = check_contract_ac_bindings(
            _TICKET, _contract(claims=["AC7"]), TICKET_BODY
        )

        assert _rules(findings) == ["ac_binding_unknown_criterion"]
        assert "AC7" in findings[0].message
        # The finding names what the ticket DOES declare, so the reader can fix
        # it without opening the ticket.
        assert "AC1" in findings[0].message
        assert "AC2" in findings[0].message

    def test_a_claim_for_a_present_criterion_is_clean(self) -> None:
        assert (
            check_contract_ac_bindings(
                _TICKET, _contract(claims=["AC1", "AC2"]), TICKET_BODY
            )
            == []
        )

    def test_the_join_survives_a_different_spelling_on_each_side(self) -> None:
        """`ac-1` in a contract and `**AC1**` in a ticket are one criterion."""
        body = "## Acceptance criteria\n\n- **AC1** the emphasised shape\n"

        assert (
            check_contract_ac_bindings(_TICKET, _contract(claims=["ac-1"]), body) == []
        )


class TestStaleHash:
    """AC1, second half: a binding pinned to a criterion that was rewritten."""

    def test_a_pinned_binding_matching_the_current_text_is_clean(self) -> None:
        contract = _contract(
            claims=["AC2"],
            bindings=[
                {
                    "label": "AC2",
                    "criterion_hash": _hash_of(TICKET_BODY, "AC2"),
                    "accepted_by": "jonahgabriel",
                    "accepted_at": "2026-09-12T18:04:20Z",
                }
            ],
        )

        assert check_contract_ac_bindings(_TICKET, contract, TICKET_BODY) == []

    def test_the_same_contract_goes_red_when_the_criterion_is_rewritten(self) -> None:
        """The whole point: the criterion changed under a passing check.

        The contract is byte-identical to the clean case above. Only the
        TICKET moved, and the binding must stop standing.
        """
        contract = _contract(
            claims=["AC2"],
            bindings=[
                {
                    "label": "AC2",
                    "criterion_hash": _hash_of(TICKET_BODY, "AC2"),
                    "accepted_by": "jonahgabriel",
                    "accepted_at": "2026-09-12T18:04:20Z",
                }
            ],
        )

        findings = check_contract_ac_bindings(_TICKET, contract, REWRITTEN_TICKET_BODY)

        assert _rules(findings) == ["ac_binding_stale_hash"]
        assert "re-accepted" in findings[0].message

    def test_it_is_green_again_once_re_accepted(self) -> None:
        """AC1's `green once re-accepted`, as a second call rather than a claim."""
        re_accepted = _contract(
            claims=["AC2"],
            bindings=[
                {
                    "label": "AC2",
                    "criterion_hash": _hash_of(REWRITTEN_TICKET_BODY, "AC2"),
                    "accepted_by": "jonahgabriel",
                    "accepted_at": "2026-09-12T19:00:00Z",
                }
            ],
        )

        assert (
            check_contract_ac_bindings(_TICKET, re_accepted, REWRITTEN_TICKET_BODY)
            == []
        )

    def test_a_binding_with_no_hash_at_all_is_stale(self) -> None:
        contract = _contract(
            claims=["AC1"], bindings=[{"label": "AC1", "criterion_hash": ""}]
        )

        assert _rules(check_contract_ac_bindings(_TICKET, contract, TICKET_BODY)) == [
            "ac_binding_stale_hash"
        ]

    def test_a_draft_binding_passes_this_gate(self) -> None:
        """Acceptance is NOT required here, deliberately.

        Requiring it before a proposer exists (OMN-18238) would hold every
        contract in the corpus at once, for a record nothing can yet produce.
        """
        contract = _contract(
            claims=["AC1"],
            bindings=[{"label": "AC1", "criterion_hash": _hash_of(TICKET_BODY, "AC1")}],
        )

        assert check_contract_ac_bindings(_TICKET, contract, TICKET_BODY) == []


class TestFailClosed:
    def test_an_unreadable_ticket_is_red_not_a_skip(self) -> None:
        findings = check_contract_ac_bindings(_TICKET, _contract(claims=["AC1"]), None)

        assert _rules(findings) == ["ac_binding_ticket_unreadable"]

    def test_a_contract_claiming_nothing_is_red_on_an_unreadable_ticket(
        self,
    ) -> None:
        """DELIBERATELY REVERSED by OMN-18333, and the reversal is the point.

        This test previously asserted that a contract claiming nothing was
        unaffected by an unreadable ticket, on the reasoning that it had
        nothing for this gate to check. OMN-18333 gave it something: the
        coverage rule asks whether the TICKET declares a criterion the contract
        fails to claim, and a contract claiming nothing is the degenerate case
        of that question rather than an exemption from it. Leaving the old
        scoping would have let a companion that binds nothing, for a ticket
        nobody can read, pass silently — the exact shape step 7 exists to
        refuse."""
        contract = _contract(claims=[])

        findings = check_contract_ac_bindings(_TICKET, contract, None)

        assert _rules(findings) == ["ac_binding_ticket_unreadable"]

    def test_an_unlabelled_ticket_is_named_as_such(self) -> None:
        """The fix is a ticket-authoring change, so the finding says so rather
        than sending somebody to edit the contract."""
        body = "## Acceptance criteria\n\n- the first thing\n- the second thing\n"

        findings = check_contract_ac_bindings(_TICKET, _contract(claims=["AC1"]), body)

        assert _rules(findings) == ["ac_binding_ticket_unlabelled"]
        assert "Label the ticket's criteria" in findings[0].message


class TestLocalRules:
    """The pre-commit half: everything provable without a ticket body."""

    def test_a_malformed_claim_is_red_locally(self) -> None:
        contract = _contract(claims=["the first one"])

        assert _rules(check_local_ac_bindings(_TICKET, contract)) == [
            "ac_binding_label_malformed"
        ]

    def test_a_record_for_an_unclaimed_criterion_is_red_locally(self) -> None:
        contract = _contract(
            claims=["AC1"], bindings=[{"label": "AC2", "criterion_hash": "a" * 64}]
        )

        assert "ac_binding_unclaimed" in _rules(
            check_local_ac_bindings(_TICKET, contract)
        )

    def test_a_duplicate_record_is_red_locally(self) -> None:
        contract = _contract(
            claims=["AC1"],
            bindings=[
                {"label": "AC1", "criterion_hash": "a" * 64},
                {"label": "AC1", "criterion_hash": "b" * 64},
            ],
        )

        assert "ac_binding_duplicate" in _rules(
            check_local_ac_bindings(_TICKET, contract)
        )

    def test_the_local_rules_never_reach_a_ticket(self) -> None:
        """A stale hash is invisible locally, and that is correct: a commit has
        no Linear read, and a hook that guessed would be worse than one that
        declines to answer."""
        contract = _contract(
            claims=["AC2"], bindings=[{"label": "AC2", "criterion_hash": "a" * 64}]
        )

        assert check_local_ac_bindings(_TICKET, contract) == []

    def test_a_clean_contract_is_clean_locally(self) -> None:
        contract = _contract(
            claims=["AC1"], bindings=[{"label": "AC1", "criterion_hash": "a" * 64}]
        )

        assert check_local_ac_bindings(_TICKET, contract) == []


# ---------------------------------------------------------------------- the CLI --


class TestCli:
    def _write(self, tmp_path: Path, contract: dict[str, object]) -> Path:
        path = tmp_path / f"{_TICKET}.yaml"
        path.write_text(yaml.safe_dump(contract), encoding="utf-8")
        return path

    def test_hosted_mode_without_ticket_bodies_is_a_usage_error(
        self, tmp_path: Path
    ) -> None:
        from onex_change_control.scripts.check_ac_binding_acceptance import main

        path = self._write(tmp_path, _contract(claims=["AC1"]))

        assert main([str(path)]) == 2

    def test_hosted_mode_is_red_on_an_absent_criterion(self, tmp_path: Path) -> None:
        from onex_change_control.scripts.check_ac_binding_acceptance import main

        path = self._write(tmp_path, _contract(claims=["AC7"]))
        bodies = tmp_path / "bodies.json"
        bodies.write_text(json.dumps({_TICKET: TICKET_BODY}), encoding="utf-8")

        assert main([str(path), "--ticket-bodies", str(bodies)]) == 1

    def test_hosted_mode_is_red_when_the_bodies_file_is_missing(
        self, tmp_path: Path
    ) -> None:
        """A fetch step that failed must not read as a clean run."""
        from onex_change_control.scripts.check_ac_binding_acceptance import main

        path = self._write(tmp_path, _contract(claims=["AC1"]))

        assert main([str(path), "--ticket-bodies", str(tmp_path / "absent.json")]) == 1

    def test_hosted_mode_is_green_on_a_present_criterion(self, tmp_path: Path) -> None:
        from onex_change_control.scripts.check_ac_binding_acceptance import main

        path = self._write(tmp_path, _contract(claims=["AC1"]))
        bodies = tmp_path / "bodies.json"
        bodies.write_text(json.dumps({_TICKET: TICKET_BODY}), encoding="utf-8")

        assert main([str(path), "--ticket-bodies", str(bodies)]) == 0

    def test_local_mode_needs_no_ticket_bodies(self, tmp_path: Path) -> None:
        from onex_change_control.scripts.check_ac_binding_acceptance import main

        path = self._write(tmp_path, _contract(claims=["AC1"]))

        assert main([str(path), "--local"]) == 0

    def test_local_mode_is_red_on_a_malformed_claim(self, tmp_path: Path) -> None:
        from onex_change_control.scripts.check_ac_binding_acceptance import main

        path = self._write(tmp_path, _contract(claims=["the first one"]))

        assert main([str(path), "--local"]) == 1

    def test_a_non_contract_path_is_ignored(self, tmp_path: Path) -> None:
        from onex_change_control.scripts.check_ac_binding_acceptance import main

        stray = tmp_path / "notes.yaml"
        stray.write_text("nothing: here\n", encoding="utf-8")

        assert main([str(stray), "--local"]) == 0
