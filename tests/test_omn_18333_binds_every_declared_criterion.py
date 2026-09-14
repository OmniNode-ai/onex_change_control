# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18333 — a companion must bind EVERY criterion its ticket declares.

Step 7 of the mechanical ticket closeout plan. Steps 5 and 6 make the binding
exist: the admission guard requires a named falsifier per acceptance criterion
at create time, and the autobinder transcribes that declaration into the
companion contract. This is the refusal that makes it stay. Without it the
transcription is advisory and a companion that quietly binds nothing lands
exactly as it does today.

**The rule is EVERY, and the partial case is why.** A companion binding some but
not all of the declared criteria is refused on the same terms as one binding
none: it leaves the closer starved on exactly the criteria it omitted while the
pull request reads as a pass. Binding none is the degenerate case of the rule,
not the rule. The plan's step 7 row originally said "none" and was corrected
against its own section 6 (correction D); the tests below pin the corrected
statement.

**Why a vendored companion rather than a hand-built dict.** A fixture proves the
fixture. `vendored_companion_OMN-18185.yaml` is the bytes of a real, merged
companion from this repository's own corpus — sixty evidence items, a
supersession chain, self-bind entries, and `binds_ac` claims spread across items
rather than concentrated on one. Its union of claims is AC1 through AC7, which is
what makes it usable as both halves of the control: red against a ticket
declaring nine criteria, green against one declaring seven.

**What is NOT asserted here.** Whether a bound check actually proves the
criterion its label names. That is the proof-class rule's job and it is enforced
elsewhere; this gate asks only whether anything claims the criterion at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import pytest
import yaml

from onex_change_control.scripts.check_ac_binding_acceptance import main
from onex_change_control.validation.ac_binding_acceptance import (
    check_contract_ac_bindings,
    check_local_ac_bindings,
)
from onex_change_control.validation.ac_criteria import (
    declared_criteria,
    falsifier_of,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from onex_change_control.validation.ac_binding_acceptance import AcBindingFinding

FIXTURES = Path(__file__).parent / "fixtures" / "omn_18333"

#: The real, merged companion whose bytes every coverage leg below runs against.
#:
#: Stored with a ``.yaml.txt`` suffix, which is this repository's established
#: shape for vendored contract bytes (see
#: ``tests/fixtures/contract_shape_v1/OMN-15413.at-*.yaml.txt``). The
#: string-version gate reads YAML by type, and rewriting ``schema_version`` to
#: satisfy it would destroy the only property these bytes have — being the real
#: artifact. Widening that gate's exclude list to admit a fixture would be an
#: allowlist added to pass a gate, which is never the fix.
VENDORED_COMPANION = FIXTURES / "vendored_companion_OMN-18185.yaml.txt"

_TICKET = "OMN-18185"

_UNBOUND = "ac_binding_criterion_unbound"
_UNBINDABLE = "ac_binding_criterion_unbindable"


def _body(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _companion() -> object:
    return yaml.safe_load(VENDORED_COMPANION.read_text(encoding="utf-8"))


def _rules(findings: Sequence[AcBindingFinding]) -> list[str]:
    return [finding.rule for finding in findings]


def _coverage(findings: Sequence[AcBindingFinding]) -> list[AcBindingFinding]:
    return [f for f in findings if f.rule in {_UNBOUND, _UNBINDABLE}]


# ------------------------------------------------------ the falsifier reader --


class TestFalsifierReader:
    """The scope predicate. A criterion naming no check is out of scope, and
    getting that wrong in either direction is the whole blast radius."""

    @pytest.mark.parametrize(
        "item",
        [
            "AC1 the thing — falsifier: `pytest tests/x.py` asserts it",
            "AC1 the thing, falsified by `pytest tests/x.py`",
            "AC1 the thing. Falsifier: a live readback of the cluster",
        ],
    )
    def test_a_declared_falsifier_is_read(self, item: str) -> None:
        assert falsifier_of(item) is not None

    @pytest.mark.parametrize(
        "item",
        [
            "AC1 the thing, with no named check at all",
            "AC1 mentions the word falsifier: ",
        ],
    )
    def test_a_criterion_naming_no_check_declares_nothing(self, item: str) -> None:
        assert falsifier_of(item) is None

    def test_the_last_marker_wins(self) -> None:
        """A criterion whose prose uses the word before naming the real check is
        read the way its author meant it."""
        item = (
            "AC1 the falsifier discipline matters here — falsifier: "
            "`pytest tests/real.py -q`"
        )

        assert falsifier_of(item) == "`pytest tests/real.py -q`"

    def test_a_ticket_with_no_falsifier_declares_nothing(self) -> None:
        assert declared_criteria(_body("ticket_declares_no_falsifiers.md")) == []


# --------------------------------------------- AC1 / AC4 — the refusal legs --


class TestEveryDeclaredCriterionIsBound:
    def test_a_companion_short_of_every_criterion_is_refused_naming_each(
        self,
    ) -> None:
        """AC1. The vendored companion claims AC1..AC7. The ticket declares nine.

        Both missing labels are named individually — a count would send somebody
        hunting through a sixty-item contract.
        """
        findings = check_contract_ac_bindings(
            _TICKET, _companion(), _body("ticket_declares_nine_falsified.md")
        )

        coverage = _coverage(findings)
        assert [f.rule for f in coverage] == [_UNBOUND, _UNBOUND]
        messages = " ".join(f.message for f in coverage)
        assert "AC8" in messages
        assert "AC9" in messages
        assert _TICKET in messages

    def test_partial_binding_names_the_third_not_a_count(self) -> None:
        """AC4. Two of three bound is a failure, and the finding names the one
        that is missing rather than reporting that one is."""
        contract = {
            "ticket_id": "OMN-19998",
            "dod_evidence": [
                {"id": "dod-001", "binds_ac": ["AC1"]},
                {"id": "dod-002", "binds_ac": ["AC2"]},
            ],
        }

        findings = check_contract_ac_bindings(
            "OMN-19998", contract, _body("ticket_declares_nine_falsified.md")
        )

        coverage = _coverage(findings)
        assert {f.rule for f in coverage} == {_UNBOUND}
        joined = " ".join(f.message for f in coverage)
        for label in ("AC3", "AC4", "AC5", "AC6", "AC7", "AC8", "AC9"):
            assert label in joined
        assert len(coverage) == 7

    def test_binding_none_is_the_degenerate_case_not_an_exemption(self) -> None:
        """A contract claiming nothing at all is refused on the same terms."""
        contract = {"ticket_id": "OMN-19998", "dod_evidence": [{"id": "dod-001"}]}

        findings = check_contract_ac_bindings(
            "OMN-19998", contract, _body("ticket_declares_seven_falsified.md")
        )

        assert len(_coverage(findings)) == 7

    def test_the_union_is_across_items_not_per_item(self) -> None:
        """A contract proving AC1 with one item and AC2 with another has bound
        both. Asking each item to claim every criterion would refuse a correct
        companion, which is the failure mode opposite to the one being closed."""
        contract = {
            "ticket_id": "OMN-19998",
            "dod_evidence": [
                {"id": "dod-001", "binds_ac": ["AC1", "AC2", "AC3"]},
                {"id": "dod-002", "binds_ac": ["AC4", "AC5"]},
                {"id": "dod-003", "binds_ac": ["ac-6", "AC 7"]},
            ],
        }

        findings = check_contract_ac_bindings(
            "OMN-19998", contract, _body("ticket_declares_seven_falsified.md")
        )

        assert _coverage(findings) == []

    def test_a_label_the_grammar_cannot_parse_is_unbound_never_absent(self) -> None:
        """The brief's specific hazard: a suffixed label such as `AC2b` parses as
        NO label upstream. Reading that as absent is how a ticket with an
        unbindable criterion passes as fully bound."""
        contract = {
            "ticket_id": "OMN-19998",
            "dod_evidence": [{"id": "dod-001", "binds_ac": ["AC1"]}],
        }

        findings = check_contract_ac_bindings(
            "OMN-19998", contract, _body("ticket_declares_suffixed_label.md")
        )

        coverage = _coverage(findings)
        assert [f.rule for f in coverage] == [_UNBINDABLE]
        assert "AC2b" in coverage[0].message


class TestParityWithTheAutobinder:
    """The two readers must segment a ticket the same way, or the gate refuses
    companions the autobinder minted correctly.

    The declared set here is read by this repository's own criterion reader
    (`ac_criteria`, ported from the evidence closer). The autobinder reads the
    same ticket with its own vendored copy of the admission guard's parser
    (`omnimarket.occ_criterion_units` at 3069cfd8, itself vendored from
    omniclaude at 3b1c66e7). Neither can import the other: omnimarket depends on
    this package, and omniclaude is a plugin repository installed in neither CI.

    **Measured live 2026-09-14.** Both readers were run over the raw
    descriptions of two real tickets and produced the IDENTICAL declared
    sequence, suffixed labels included — OMN-18333 as `AC1 AC2 AC3 AC4 AC5`, and
    OMN-18332 as the twelve-item sequence this fixture reproduces, with six
    unlabelled entries where the suffixed labels sit.

    **The honest bound.** The fixture below pins THIS reader's output against
    that shape. It cannot execute the other reader, so it proves the two have
    not diverged at the shape that was measured, not that they still agree on
    every input. A parity leg that imported omnimarket would be stronger and is
    not available at this layer.
    """

    #: The declared sequence both readers produced on the real OMN-18332 body,
    #: with the six suffixed labels (AC2b, AC2c, AC2d, AC2e, AC2f, AC2g) landing
    #: as unlabelled on BOTH sides. That agreement is the load-bearing half: a
    #: criterion one reader labels and the other does not is a binding the
    #: autobinder mints and this gate then refuses.
    MEASURED: ClassVar[list[str]] = [
        "AC1",
        "AC2",
        "",
        "",
        "",
        "",
        "",
        "",
        "AC3",
        "AC4",
        "AC5",
        "AC6",
    ]

    def test_the_reader_reproduces_the_measured_shape(self) -> None:
        declared = declared_criteria(_body("ticket_declares_real_label_shape.md"))

        assert [label for label, _ in declared] == self.MEASURED

    def test_the_suffixed_labels_are_refused_rather_than_dropped(self) -> None:
        """Six unbindable criteria, not six absent ones. Dropping them is how a
        ticket whose criteria cannot be bound reads as fully bound."""
        contract = {
            "ticket_id": "OMN-19998",
            "dod_evidence": [
                {
                    "id": "dod-001",
                    "binds_ac": ["AC1", "AC2", "AC3", "AC4", "AC5", "AC6"],
                }
            ],
        }

        findings = check_contract_ac_bindings(
            "OMN-19998", contract, _body("ticket_declares_real_label_shape.md")
        )

        coverage = _coverage(findings)
        assert [f.rule for f in coverage] == [_UNBINDABLE] * 6


# ------------------------------------------------- AC2 / AC3 — the controls --


class TestPositiveControls:
    def test_a_fully_bound_companion_passes_unchanged(self) -> None:
        """AC2. The same vendored bytes, against a ticket declaring exactly the
        seven criteria it claims, emit no coverage finding — so the gate is not
        a blanket refusal of every companion that reaches it."""
        findings = check_contract_ac_bindings(
            _TICKET, _companion(), _body("ticket_declares_seven_falsified.md")
        )

        assert _coverage(findings) == []

    def test_a_ticket_declaring_no_falsifiers_is_untouched(self) -> None:
        """AC3. A pre-cutover ticket declares no falsifier, so the coverage rule
        has no scope over it and the legacy corpus cannot be retroactively
        blocked by a requirement that did not exist when it was written.

        This is the plan's own rule, not leniency: section 6 states that a ticket
        which does not declare the map has nothing transcribed and holds at the
        closer exactly as it does today, and the ticket's AC3 asserts exit 0 with
        no new finding emitted.
        """
        contract = {"ticket_id": "OMN-19998", "dod_evidence": [{"id": "dod-001"}]}

        findings = check_contract_ac_bindings(
            "OMN-19998", contract, _body("ticket_declares_no_falsifiers.md")
        )

        assert _coverage(findings) == []
        assert _rules(findings) == []

    def test_the_local_rules_never_reach_the_coverage_question(self) -> None:
        """The pre-commit half has no ticket body, so it is a strict subset by
        construction rather than a divergent second opinion."""
        assert _coverage(check_local_ac_bindings(_TICKET, _companion())) == []


# ------------------------------------------------------------ the CLI verdict --


class TestCli:
    """AC1's falsifier names the job's exit code and its stderr, so both are
    asserted through the real entry point rather than the engine."""

    def _run(
        self, tmp_path: Path, body_name: str, capsys: pytest.CaptureFixture[str]
    ) -> tuple[int, str]:
        contract = tmp_path / f"{_TICKET}.yaml"
        contract.write_text(
            VENDORED_COMPANION.read_text(encoding="utf-8"), encoding="utf-8"
        )
        bodies = tmp_path / "bodies.json"
        bodies.write_text(json.dumps({_TICKET: _body(body_name)}), encoding="utf-8")
        code = main([str(contract), "--ticket-bodies", str(bodies)])
        return code, capsys.readouterr().err

    def test_a_short_companion_exits_non_zero_and_names_every_unbound_label(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, err = self._run(tmp_path, "ticket_declares_nine_falsified.md", capsys)

        assert code == 1
        assert _UNBOUND in err
        assert "AC8" in err
        assert "AC9" in err

    def test_a_fully_bound_companion_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, err = self._run(tmp_path, "ticket_declares_seven_falsified.md", capsys)

        assert code == 0
        assert err == ""


class TestFailClosed:
    def test_an_unreadable_ticket_refuses_a_contract_that_claims_nothing(
        self,
    ) -> None:
        """OMN-18333 widened the unreachable verdict to every contract. The
        coverage question is about a contract claiming NOTHING just as much as
        one claiming three of four, so "I could not read the ticket" can no
        longer resolve to "so it passes" on the binds-nothing path."""
        contract = {"ticket_id": "OMN-19998", "dod_evidence": [{"id": "dod-001"}]}

        findings = check_contract_ac_bindings("OMN-19998", contract, None)

        assert _rules(findings) == ["ac_binding_ticket_unreadable"]


class TestWiring:
    """Rule 5 — a check that is not a gate is advisory and gets ignored.

    This rule adds no new workflow and no new required status check: it lands
    inside the existing `ac-binding-acceptance` job, whose failure `CI Summary`
    already turns into a failure of that required context. That is exactly the
    ticket's AC5, so what has to be asserted is the OPPOSITE of a new context —
    that the existing job still runs this rule's units and still invokes the
    same entrypoint the local hook does.
    """

    ROOT: ClassVar[Path] = Path(__file__).resolve().parents[1]

    def test_the_existing_job_runs_this_rule_units(self) -> None:
        workflow = (self.ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

        assert "ac-binding-acceptance:" in workflow
        assert Path(__file__).name in workflow

    def test_the_rule_adds_no_new_workflow(self) -> None:
        """A new workflow file would surface a new check context, which AC5
        refuses. There is no `*binds-every*` or `*ac-coverage*` workflow."""
        workflows = {
            path.name for path in (self.ROOT / ".github/workflows").glob("*.yml")
        }

        assert not [
            name for name in workflows if "binds-every" in name or "ac-coverage" in name
        ]

    def test_the_local_hook_and_the_job_share_one_entrypoint(self) -> None:
        config = (self.ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        workflow = (self.ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

        assert "uv run check-ac-binding-acceptance --local" in config
        assert "uv run check-ac-binding-acceptance" in workflow
