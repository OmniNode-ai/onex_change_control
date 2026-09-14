# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18333 — an ABSENT binding holds; a PARTIAL binding is refused.

The sequencing correction to step 7. The binds-every rule shipped refusing a
contract that binds NOTHING on the same terms as one binding three of four.
That is correct as a statement about companions and wrong as a statement about
*this fleet on this morning*: no producer transcribes a binding yet, so every
companion of every falsifier-declaring ticket binds nothing, and the change
that makes the producer transcribe cannot land because its own companion binds
nothing either. The gate refused the fix to the thing it was refusing.

**The corrected line, and why it is where it is.**

* A contract that binds NOTHING is ABSENT. Absence is already held — by the
  closer, which will not close a ticket whose criteria nothing claims
  (OMN-18330). Refusing it here adds no safety and takes the repository's whole
  evidence path offline until a producer that does not exist yet is deployed.
  The gate still SAYS so, once per unbound criterion, as a hold: silent absence
  is how a companion that binds nothing reads as a pass, and the hold is what
  keeps it visible without making it fatal.
* A contract that binds SOMETHING and omits a declared criterion is PARTIAL,
  and partial is the gate fact. It is the only shape that reads as a pass while
  starving the closer on exactly the criteria it omitted — a reviewer seeing
  `binds_ac: [AC1, AC2]` has no way to know the ticket declared four. Nothing
  downstream distinguishes "bound two of four" from "bound all of them", which
  is precisely the confusion absence does not create.

So the discriminator is whether the contract binds ANY criterion, not whether a
key is spelled. A producer that transcribes nothing emits no key at all
(measured: an empty render is byte-identical to no render), and an explicit
empty list is the same producer saying the same thing in a different spelling.
Reading those two differently would refuse on a distinction no consumer can see.

**What did NOT change.** A partial binding is refused, an unbindable label under
a binding contract is refused, a ticket declaring no falsifier is out of scope
entirely, and the local pre-commit rules are untouched. No allowlist, no
severity knob, no cutover date, no skip input: the hold is a property of the
contract's own content, computed every run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from onex_change_control.scripts.check_ac_binding_acceptance import main
from onex_change_control.validation.ac_binding_acceptance import (
    AcBindingFinding,
    check_contract_ac_bindings,
    check_local_ac_bindings,
    holds,
    refusals,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "omn_18333"

_UNBOUND = "ac_binding_criterion_unbound"
_UNBINDABLE = "ac_binding_criterion_unbindable"
_UNREADABLE = "ac_binding_ticket_unreadable"

_TICKET = "OMN-19998"


def _body(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _rules(findings: Sequence[AcBindingFinding]) -> list[str]:
    return [finding.rule for finding in findings]


def _binds_nothing() -> dict[str, object]:
    """A companion as today's non-transcribing producer actually mints one."""
    return {
        "ticket_id": _TICKET,
        "dod_evidence": [
            {"id": "dod-001", "description": "a check", "source": "ci"},
            {"id": "dod-002", "description": "another check", "source": "ci"},
        ],
    }


# ------------------------------------------------------------ (a) the hold --


class TestAbsentBindingHolds:
    """The bootstrap case. Seven declared criteria, nothing claimed, exit 0."""

    def test_a_contract_binding_nothing_emits_holds_not_refusals(self) -> None:
        findings = check_contract_ac_bindings(
            _TICKET, _binds_nothing(), _body("ticket_declares_seven_falsified.md")
        )

        assert refusals(findings) == []
        assert len(holds(findings)) == 7
        assert set(_rules(holds(findings))) == {_UNBOUND}

    def test_the_hold_still_names_every_unbound_criterion(self) -> None:
        """Visible, or it is the silent pass this gate exists to remove. A count
        would send somebody hunting; the labels are what a reader acts on."""
        findings = check_contract_ac_bindings(
            _TICKET, _binds_nothing(), _body("ticket_declares_seven_falsified.md")
        )

        joined = " ".join(finding.message for finding in holds(findings))
        for label in ("AC1", "AC2", "AC3", "AC4", "AC5", "AC6", "AC7"):
            assert label in joined
        assert _TICKET in joined

    def test_an_unbindable_label_under_a_binding_nothing_contract_also_holds(
        self,
    ) -> None:
        """The suffixed-label class is a HOLD when nothing is bound, for the same
        reason the bare labels are: there is no partial claim to be misread."""
        findings = check_contract_ac_bindings(
            _TICKET, _binds_nothing(), _body("ticket_declares_real_label_shape.md")
        )

        assert refusals(findings) == []
        assert _rules(holds(findings)).count(_UNBINDABLE) == 6
        assert _rules(holds(findings)).count(_UNBOUND) == 6

    def test_an_explicitly_empty_claim_reads_as_absent_not_as_partial(self) -> None:
        """`binds_ac: []` is the same producer saying the same thing. Reading it
        as a partial binding would refuse on a spelling no consumer can see."""
        contract = {
            "ticket_id": _TICKET,
            "dod_evidence": [{"id": "dod-001", "binds_ac": [], "ac_bindings": []}],
        }

        findings = check_contract_ac_bindings(
            _TICKET, contract, _body("ticket_declares_seven_falsified.md")
        )

        assert refusals(findings) == []
        assert len(holds(findings)) == 7


# --------------------------------------------------------- (b) the refusal --


class TestPartialBindingIsRefused:
    def test_binding_six_of_seven_is_refused_naming_the_seventh(self) -> None:
        contract = {
            "ticket_id": _TICKET,
            "dod_evidence": [
                {"id": "dod-001", "binds_ac": ["AC1", "AC2", "AC3"]},
                {"id": "dod-002", "binds_ac": ["AC4", "AC5", "AC6"]},
            ],
        }

        findings = check_contract_ac_bindings(
            _TICKET, contract, _body("ticket_declares_seven_falsified.md")
        )

        refused = refusals(findings)
        assert _rules(refused) == [_UNBOUND]
        assert "AC7" in refused[0].message
        assert holds(findings) == []

    def test_one_binding_anywhere_makes_the_whole_contract_partial(self) -> None:
        """The union is across items, so a single claim on any item is what
        turns absence into partiality — not a per-item test that would call a
        correctly split companion partial."""
        contract = {
            "ticket_id": _TICKET,
            "dod_evidence": [
                {"id": "dod-001"},
                {"id": "dod-002", "binds_ac": ["AC1"]},
            ],
        }

        findings = check_contract_ac_bindings(
            _TICKET, contract, _body("ticket_declares_seven_falsified.md")
        )

        assert len(refusals(findings)) == 6
        assert holds(findings) == []

    def test_a_record_with_no_claim_still_counts_as_binding_something(self) -> None:
        """An `ac_bindings` record is a binding even when `binds_ac` forgot to
        claim it. It is already refused as unclaimed; reading the contract as
        binding nothing would additionally downgrade the coverage verdict on a
        contract that is demonstrably mid-transcription."""
        contract = {
            "ticket_id": _TICKET,
            "dod_evidence": [
                {
                    "id": "dod-001",
                    "ac_bindings": [{"label": "AC1", "criterion_hash": "deadbeef"}],
                }
            ],
        }

        findings = check_contract_ac_bindings(
            _TICKET, contract, _body("ticket_declares_seven_falsified.md")
        )

        assert holds(findings) == []
        assert _rules(refusals(findings)).count(_UNBOUND) == 7


# ----------------------------------------------------- (c) the unbindable --


class TestUnbindableUnderAPartialBinding:
    def test_a_suffixed_label_is_refused_when_the_contract_binds_something(
        self,
    ) -> None:
        """Unchanged from the merged rule. The remedy is OMN-18356 in the reader,
        not a downgrade here: a criterion nothing can point at is unbound, never
        absent, and a contract that binds the bare labels around it reads as a
        complete pass."""
        contract = {
            "ticket_id": _TICKET,
            "dod_evidence": [
                {
                    "id": "dod-001",
                    "binds_ac": ["AC1", "AC2", "AC3", "AC4", "AC5", "AC6"],
                }
            ],
        }

        findings = check_contract_ac_bindings(
            _TICKET, contract, _body("ticket_declares_real_label_shape.md")
        )

        refused = refusals(findings)
        assert _rules(refused) == [_UNBINDABLE] * 6
        assert holds(findings) == []


# ------------------------------------------------- (d) out of scope, intact --


class TestOutOfScopeIsUnchanged:
    def test_a_ticket_declaring_no_falsifier_emits_nothing_at_all(self) -> None:
        """Not a hold either. Out of scope is out of scope: a pre-cutover ticket
        transcribes nothing and the closer holds it exactly as it does today, so
        a per-criterion hold here would be noise on the entire legacy corpus."""
        findings = check_contract_ac_bindings(
            _TICKET, _binds_nothing(), _body("ticket_declares_no_falsifiers.md")
        )

        assert findings == []

    def test_a_fully_bound_contract_still_emits_nothing(self) -> None:
        contract = {
            "ticket_id": _TICKET,
            "dod_evidence": [
                {
                    "id": "dod-001",
                    "binds_ac": ["AC1", "AC2", "AC3", "AC4", "AC5", "AC6", "AC7"],
                }
            ],
        }

        findings = check_contract_ac_bindings(
            _TICKET, contract, _body("ticket_declares_seven_falsified.md")
        )

        assert findings == []

    def test_the_local_pre_commit_rules_are_untouched(self) -> None:
        """Every local finding still refuses; the hold class exists only where a
        ticket body was read, because absence is only meaningful against a
        declared set."""
        contract = {
            "ticket_id": _TICKET,
            "dod_evidence": [{"id": "dod-001", "binds_ac": ["not-a-label"]}],
        }

        findings = check_local_ac_bindings(_TICKET, contract)

        assert _rules(findings) == ["ac_binding_label_malformed"]
        assert holds(findings) == []


# ------------------------------------------------------ unreachable tickets --


class TestUnreachableMirrorsTheReadableVerdict:
    """Fail-closed means "unreachable is at least as strict as the worst
    readable outcome" — never stricter than any outcome the rule permits.

    The merged rule widened the unreadable verdict from claiming contracts to
    every contract, justified by the coverage rule applying to a contract that
    claims nothing. With that case now a hold, the justification is gone: a
    binds-nothing contract whose ticket cannot be read would be REFUSED where
    the same contract against a readable ticket HOLDS, which re-creates this
    deadlock on every Linear outage and protects nothing.
    """

    def test_a_claiming_contract_with_an_unreadable_ticket_is_still_red(self) -> None:
        contract = {
            "ticket_id": _TICKET,
            "dod_evidence": [{"id": "dod-001", "binds_ac": ["AC1"]}],
        }

        findings = check_contract_ac_bindings(_TICKET, contract, None)

        assert _rules(refusals(findings)) == [_UNREADABLE]

    def test_a_contract_binding_nothing_holds_when_the_ticket_is_unreachable(
        self,
    ) -> None:
        findings = check_contract_ac_bindings(_TICKET, _binds_nothing(), None)

        assert refusals(findings) == []
        assert _rules(holds(findings)) == [_UNREADABLE]


# --------------------------------------------------------- the CLI verdict --


class TestCliVerdict:
    """The exit code is the gate. A hold that exited 1 would be a refusal with a
    softer word for it."""

    def _run(
        self,
        tmp_path: Path,
        contract: Mapping[str, object],
        body_name: str,
        capsys: pytest.CaptureFixture[str],
    ) -> tuple[int, str]:
        path = tmp_path / f"{_TICKET}.yaml"
        path.write_text(yaml.safe_dump(contract), encoding="utf-8")
        bodies = tmp_path / "bodies.json"
        bodies.write_text(json.dumps({_TICKET: _body(body_name)}), encoding="utf-8")
        code = main([str(path), "--ticket-bodies", str(bodies)])
        return code, capsys.readouterr().err

    def test_holds_exit_zero_and_are_printed_as_holds(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, err = self._run(
            tmp_path,
            _binds_nothing(),
            "ticket_declares_seven_falsified.md",
            capsys,
        )

        assert code == 0
        assert f"[HOLD {_UNBOUND}]" in err
        assert "AC7" in err

    def test_a_partial_binding_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        contract = {
            "ticket_id": _TICKET,
            "dod_evidence": [{"id": "dod-001", "binds_ac": ["AC1"]}],
        }

        code, err = self._run(
            tmp_path, contract, "ticket_declares_seven_falsified.md", capsys
        )

        assert code == 1
        assert f"[{_UNBOUND}]" in err
        assert "HOLD" not in err

    def test_a_refusal_beside_a_hold_still_exits_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A malformed label refuses locally while the coverage question holds.
        One refusing finding anywhere is a refusal for the whole run."""
        contract = {
            "ticket_id": _TICKET,
            "dod_evidence": [{"id": "dod-001", "binds_ac": ["not-a-label"]}],
        }

        code, err = self._run(
            tmp_path, contract, "ticket_declares_seven_falsified.md", capsys
        )

        assert code == 1
        assert "[ac_binding_label_malformed]" in err
        assert f"[HOLD {_UNBOUND}]" in err


class TestRenderIsStableForRefusals:
    def test_a_refusal_renders_exactly_as_it_did_before(self) -> None:
        """Downstream reads this line. The hold prefix must not move a refusal's
        bytes, or every consumer that greps the gate's output changes meaning."""
        finding = AcBindingFinding(rule="r", subject="s", message="m")

        assert finding.render() == "  [r] s: m"

    def test_a_hold_is_distinguishable_in_one_glance(self) -> None:
        finding = AcBindingFinding(rule="r", subject="s", message="m", severity="hold")

        assert finding.render() == "  [HOLD r] s: m"
