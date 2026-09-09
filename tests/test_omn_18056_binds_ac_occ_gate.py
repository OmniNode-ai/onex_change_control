# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""A dod_evidence item may declare which acceptance criteria it covers. OMN-18056

The closer's flip predicate (omnibase_infra
``handler_evidence_autoclose_sweep``, #3347) asks which acceptance criterion a
verified check covers, and reads the answer from ``binds_ac`` on the contract's
``dod_evidence`` item, carried through dod_verify onto each check result. On the
first armed scheduled run (34317729264, head 64203bd6) SEVEN of the adjudicated
tickets held on ``gap_ac_unbound`` -- no contract could declare a binding,
because every gate that parses ``dod_evidence`` is ``extra="forbid"``.

Two OCC gates refused it, and both are exercised here:

1. ``contract_compliance_check._validate_dod_item`` -- the wired compliance
   gate (``ci.yml`` -> ``scripts/ci/run_contract_compliance_check.py``) --
   validates every active item against the OCC-LOCAL
   ``ModelDodEvidenceItem``. RED before this change:
   ``INVALID_DOD_EVIDENCE_ITEM -- strict schema rejected field(s): binds_ac``.

2. ``validate_yaml`` -- the ``Validate Contract YAML (OMN-8808)`` job, a member
   of ``STRICT_GATE_JOBS`` under the required ``CI Summary`` umbrella --
   validates the whole corpus against core's ``ModelTicketContract``, whose
   ``ModelContractDodItem`` does not carry the field in any RELEASED core
   (dca2ee2c is on core's ``dev``, in no tag; newest release 0.47.5; this repo
   pins ``<0.47.0``). RED before this change: ``extra_forbidden`` on every
   subsequent OCC pull request, not only the declaring one.

Every acceptance case below is paired with a positive control, because a gate
that accepts a new field by no longer refusing anything has not been fixed.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from onex_change_control.models.model_dod_check import ModelDodEvidenceItem
from onex_change_control.scripts.contract_compliance_check import _validate_dod_item
from onex_change_control.scripts.validate_yaml import (
    CORE_KNOWS_BINDS_AC,
    validate_file,
    withhold_unreleased_binds_ac,
)


def _item(**overrides: Any) -> dict[str, Any]:
    """A minimally valid, executable dod_evidence item."""
    base: dict[str, Any] = {
        "id": "dod-001",
        "description": "The gate refuses a malformed binding.",
        "checks": [{"check_type": "command", "check_value": "echo ok"}],
    }
    base.update(overrides)
    return base


def _contract(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-18056",
        "title": "AC binding",
        "summary": "AC binding",
        "is_seam_ticket": False,
        "interface_change": False,
        "emergency_bypass": {"enabled": False},
    }
    base.update(overrides)
    return base


class TestComplianceGateAcceptsBinding:
    """The wired compliance gate no longer rejects a declared binding."""

    def test_item_declaring_binds_ac_is_accepted_and_carries_the_labels(
        self,
    ) -> None:
        validated, error = _validate_dod_item(_item(binds_ac=["AC1", "DoD2"]))
        assert error is None
        assert validated is not None
        assert validated["binds_ac"] == ["AC1", "DoD2"]

    def test_item_declaring_nothing_still_parses_and_claims_nothing(self) -> None:
        """The corpus default. Empty is a coverage gap, never a silent pass."""
        validated, error = _validate_dod_item(_item())
        assert error is None
        assert validated is not None
        assert validated["binds_ac"] == []

    @pytest.mark.parametrize("label", ["AC1", "ac-1", "AC_2", "AC 3", "DoD2", "dod4"])
    def test_accepted_label_spellings(self, label: str) -> None:
        """Every form here canonicalises identically on the closer's side."""
        _, error = _validate_dod_item(_item(binds_ac=[label]))
        assert error is None


class TestComplianceGateStillRefuses:
    """Positive controls: the gate did not stop refusing things."""

    def test_unknown_field_is_still_rejected(self) -> None:
        """`extra="forbid"` survives -- only `binds_ac` was declared."""
        validated, error = _validate_dod_item(_item(binds_ax=["AC1"]))
        assert validated is None
        assert error is not None
        assert "INVALID_DOD_EVIDENCE_ITEM" in error
        assert "binds_ax" in error

    @pytest.mark.parametrize(
        "malformed",
        [["AC"], ["criterion one"], [""], ["AC1 -- the gate is wired"], ["ACC1"]],
    )
    def test_malformed_binding_entry_is_rejected(self, malformed: list[str]) -> None:
        """An entry that cannot join is a failure, not an empty binding."""
        validated, error = _validate_dod_item(_item(binds_ac=malformed))
        assert validated is None
        assert error is not None
        assert "INVALID_DOD_EVIDENCE_ITEM" in error
        assert "binds_ac" in error

    def test_a_bare_string_is_not_a_binding_list(self) -> None:
        validated, error = _validate_dod_item(_item(binds_ac="AC1"))
        assert validated is None
        assert error is not None
        assert "binds_ac" in error

    def test_model_raises_with_an_actionable_message(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            ModelDodEvidenceItem.model_validate(_item(binds_ac=["nope"]))
        assert "acceptance-criterion labels" in str(excinfo.value)


class TestContractYamlGate:
    """The corpus-wide `Validate Contract YAML (OMN-8808)` job."""

    def _write(self, tmp_path: Path, data: dict[str, Any]) -> Path:
        path = tmp_path / "contracts" / "OMN-18056.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return path

    def test_contract_declaring_a_binding_validates(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, _contract(dod_evidence=[_item(binds_ac=["AC1"])]))
        assert validate_file(path) is True

    def test_contract_with_a_malformed_binding_fails(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path, _contract(dod_evidence=[_item(binds_ac=["not a label"])])
        )
        assert validate_file(path) is False

    def test_contract_with_an_unknown_field_still_fails(self, tmp_path: Path) -> None:
        """The withholding is scoped to one named field, not to unknown keys."""
        path = self._write(tmp_path, _contract(dod_evidence=[_item(binds_ax=["AC1"])]))
        assert validate_file(path) is False

    def test_contract_declaring_nothing_is_untouched(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, _contract(dod_evidence=[_item()]))
        assert validate_file(path) is True


class TestWithholdingIsSelfDeleting:
    """The shim is conditioned on core's own model, not on a flag."""

    def test_no_op_when_the_contract_declares_no_binding(self) -> None:
        data = _contract(dod_evidence=[_item()])
        assert withhold_unreleased_binds_ac(data) is data

    def test_no_op_on_a_contract_with_no_dod_evidence(self) -> None:
        data = _contract()
        assert withhold_unreleased_binds_ac(data) is data

    def test_field_is_withheld_only_while_core_does_not_know_it(self) -> None:
        """When core carries the field, core validates it and this is a no-op.

        Asserted as a biconditional against core's live model rather than as a
        hardcoded expectation, so the day a release carrying dca2ee2c is pinned
        here this test starts asserting the OTHER branch without being edited.
        """
        data = _contract(dod_evidence=[_item(binds_ac=["AC1"])])
        result = withhold_unreleased_binds_ac(data)
        evidence: list[Any] = result["dod_evidence"]  # type: ignore[assignment]
        assert ("binds_ac" in evidence[0]) is CORE_KNOWS_BINDS_AC

    def test_a_malformed_binding_raises_rather_than_being_discarded(self) -> None:
        if CORE_KNOWS_BINDS_AC:
            pytest.skip("core validates the field itself; the shim is a no-op")
        with pytest.raises(ValidationError):
            withhold_unreleased_binds_ac(
                _contract(dod_evidence=[_item(binds_ac=["nope"])])
            )
