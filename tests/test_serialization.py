"""Tests for model serialization (round-trip)."""

import json

import pytest
import yaml
from pydantic import ValidationError

from onex_change_control.enums.enum_drift_category import EnumDriftCategory
from onex_change_control.enums.enum_evidence_kind import EnumEvidenceKind
from onex_change_control.enums.enum_interface_surface import EnumInterfaceSurface
from onex_change_control.enums.enum_invariant_status import EnumInvariantStatus
from onex_change_control.enums.enum_pr_state import EnumPRState
from onex_change_control.models.model_day_close import (
    ModelDayClose,
    ModelDayCloseActualRepo,
    ModelDayCloseDriftDetected,
    ModelDayCloseInvariantsChecked,
    ModelDayClosePlanItem,
    ModelDayClosePR,
)
from onex_change_control.models.model_ticket_contract import (
    ModelEmergencyBypass,
    ModelEvidenceRequirement,
    ModelTicketContract,
)


class TestModelDayCloseSerialization:
    """Tests for ModelDayClose serialization."""

    def test_json_round_trip(self) -> None:
        """Test JSON serialization round-trip."""
        original = ModelDayClose(
            schema_version="1.0.0",
            date="2025-12-20",
            plan=[
                ModelDayClosePlanItem(
                    requirement_id="MVP-2WAY-REGISTRATION",
                    summary="2-way registration workflow",
                ),
            ],
            invariants_checked=ModelDayCloseInvariantsChecked(
                reducers_pure=EnumInvariantStatus.PASS,
                orchestrators_no_io=EnumInvariantStatus.PASS,
                effects_do_io_only=EnumInvariantStatus.PASS,
                real_infra_proof_progressing=EnumInvariantStatus.UNKNOWN,
            ),
        )

        # Serialize to JSON
        json_str = original.model_dump_json()
        assert isinstance(json_str, str)

        # Deserialize from JSON
        data = json.loads(json_str)
        reconstructed = ModelDayClose.model_validate(data)

        # Verify round-trip
        assert reconstructed.schema_version == original.schema_version
        assert reconstructed.date == original.date
        assert len(reconstructed.plan) == len(original.plan)
        assert reconstructed.plan[0].requirement_id == original.plan[0].requirement_id

    def test_yaml_round_trip(self) -> None:
        """Test YAML serialization round-trip."""
        original = ModelDayClose(
            schema_version="1.0.0",
            date="2025-12-20",
            drift_detected=[
                ModelDayCloseDriftDetected(
                    drift_id="DRIFT-001",
                    category=EnumDriftCategory.INTERFACES,
                    evidence="Test evidence",
                    impact="Test impact",
                    correction_for_tomorrow="Test correction",
                ),
            ],
            invariants_checked=ModelDayCloseInvariantsChecked(
                reducers_pure=EnumInvariantStatus.PASS,
                orchestrators_no_io=EnumInvariantStatus.PASS,
                effects_do_io_only=EnumInvariantStatus.PASS,
                real_infra_proof_progressing=EnumInvariantStatus.UNKNOWN,
            ),
        )

        # Serialize to dict (YAML-compatible)
        data = original.model_dump(mode="json")
        yaml_str = yaml.dump(data, default_flow_style=False)

        # Deserialize from YAML
        loaded_data = yaml.safe_load(yaml_str)
        reconstructed = ModelDayClose.model_validate(loaded_data)

        # Verify round-trip
        assert reconstructed.schema_version == original.schema_version
        assert reconstructed.date == original.date
        assert len(reconstructed.drift_detected) == len(original.drift_detected)
        assert (
            reconstructed.drift_detected[0].drift_id
            == original.drift_detected[0].drift_id
        )
        assert (
            reconstructed.drift_detected[0].category
            == original.drift_detected[0].category
        )

    def test_enum_serialization(self) -> None:
        """Test that enums serialize correctly to JSON/YAML."""
        original = ModelDayClose(
            schema_version="1.0.0",
            date="2025-12-20",
            actual_by_repo=[
                ModelDayCloseActualRepo(
                    repo="OmniNode-ai/omnibase_core",
                    prs=[
                        ModelDayClosePR(
                            pr=1,
                            title="Test PR",
                            state=EnumPRState.MERGED,
                            notes="Test notes",
                        ),
                    ],
                ),
            ],
            invariants_checked=ModelDayCloseInvariantsChecked(
                reducers_pure=EnumInvariantStatus.PASS,
                orchestrators_no_io=EnumInvariantStatus.FAIL,
                effects_do_io_only=EnumInvariantStatus.UNKNOWN,
                real_infra_proof_progressing=EnumInvariantStatus.PASS,
            ),
        )

        # Serialize to JSON
        json_str = original.model_dump_json()
        data = json.loads(json_str)

        # Verify enum values are strings in JSON
        assert data["actual_by_repo"][0]["prs"][0]["state"] == "merged"
        assert data["invariants_checked"]["reducers_pure"] == "pass"
        assert data["invariants_checked"]["orchestrators_no_io"] == "fail"

        # Deserialize and verify enums are restored
        reconstructed = ModelDayClose.model_validate(data)
        assert reconstructed.actual_by_repo[0].prs[0].state == EnumPRState.MERGED
        assert (
            reconstructed.invariants_checked.reducers_pure == EnumInvariantStatus.PASS
        )
        assert (
            reconstructed.invariants_checked.orchestrators_no_io
            == EnumInvariantStatus.FAIL
        )


class TestModelTicketContractSerialization:
    """Tests for ModelTicketContract serialization."""

    def test_json_round_trip(self) -> None:
        """Test JSON serialization round-trip."""
        original = ModelTicketContract(
            schema_version="1.0.0",
            ticket_id="OMN-962",
            summary="Test ticket",
            is_seam_ticket=True,
            interface_change=True,
            interfaces_touched=[
                EnumInterfaceSurface.EVENTS,
                EnumInterfaceSurface.TOPICS,
            ],
            evidence_requirements=[
                ModelEvidenceRequirement(
                    kind=EnumEvidenceKind.TESTS,
                    description="Unit tests required",
                    command="pytest tests/",
                ),
            ],
            emergency_bypass=ModelEmergencyBypass(enabled=False),
        )

        # Serialize to JSON
        json_str = original.model_dump_json()
        assert isinstance(json_str, str)

        # Deserialize from JSON
        data = json.loads(json_str)
        reconstructed = ModelTicketContract.model_validate(data)

        # Verify round-trip
        assert reconstructed.schema_version == original.schema_version
        assert reconstructed.ticket_id == original.ticket_id
        assert reconstructed.is_seam_ticket == original.is_seam_ticket
        assert reconstructed.interface_change == original.interface_change
        assert len(reconstructed.interfaces_touched) == len(original.interfaces_touched)
        assert reconstructed.interfaces_touched[0] == original.interfaces_touched[0]

    def test_yaml_round_trip(self) -> None:
        """Test YAML serialization round-trip."""
        original = ModelTicketContract(
            schema_version="1.0.0",
            ticket_id="OMN-962",
            summary="Test ticket",
            is_seam_ticket=False,
            interface_change=False,
            emergency_bypass=ModelEmergencyBypass(
                enabled=True,
                justification="Emergency fix required",
                follow_up_ticket_id="OMN-963",
            ),
        )

        # Serialize to dict (YAML-compatible)
        data = original.model_dump(mode="json")
        yaml_str = yaml.dump(data, default_flow_style=False)

        # Deserialize from YAML
        loaded_data = yaml.safe_load(yaml_str)
        reconstructed = ModelTicketContract.model_validate(loaded_data)

        # Verify round-trip
        assert reconstructed.schema_version == original.schema_version
        assert reconstructed.ticket_id == original.ticket_id
        assert (
            reconstructed.emergency_bypass.enabled == original.emergency_bypass.enabled
        )
        assert (
            reconstructed.emergency_bypass.justification
            == original.emergency_bypass.justification
        )

    def test_enum_serialization(self) -> None:
        """Test that enums serialize correctly to JSON/YAML."""
        original = ModelTicketContract(
            schema_version="1.0.0",
            ticket_id="OMN-962",
            summary="Test",
            is_seam_ticket=False,
            interface_change=True,
            interfaces_touched=[
                EnumInterfaceSurface.EVENTS,
                EnumInterfaceSurface.PROTOCOLS,
            ],
            evidence_requirements=[
                ModelEvidenceRequirement(
                    kind=EnumEvidenceKind.DOCS,
                    description="Documentation required",
                    command=None,
                ),
            ],
            emergency_bypass=ModelEmergencyBypass(enabled=False),
        )

        # Serialize to JSON
        json_str = original.model_dump_json()
        data = json.loads(json_str)

        # Verify enum values are strings in JSON
        assert data["interfaces_touched"] == ["events", "protocols"]
        assert data["evidence_requirements"][0]["kind"] == "docs"

        # Deserialize and verify enums are restored
        reconstructed = ModelTicketContract.model_validate(data)
        assert reconstructed.interfaces_touched[0] == EnumInterfaceSurface.EVENTS
        assert reconstructed.evidence_requirements[0].kind == EnumEvidenceKind.DOCS


class TestFrozenModels:
    """Tests for frozen model immutability."""

    def test_day_close_is_frozen(self) -> None:
        """Test that ModelDayClose is immutable after creation."""
        day_close = ModelDayClose(
            schema_version="1.0.0",
            date="2025-12-20",
            invariants_checked=ModelDayCloseInvariantsChecked(
                reducers_pure=EnumInvariantStatus.PASS,
                orchestrators_no_io=EnumInvariantStatus.PASS,
                effects_do_io_only=EnumInvariantStatus.PASS,
                real_infra_proof_progressing=EnumInvariantStatus.UNKNOWN,
            ),
        )

        # Attempting to modify should raise ValidationError
        with pytest.raises(ValidationError):
            day_close.date = "2025-12-21"  # type: ignore[assignment, misc]

    def test_ticket_contract_is_frozen(self) -> None:
        """Test that ModelTicketContract is immutable after creation."""
        contract = ModelTicketContract(
            schema_version="1.0.0",
            ticket_id="OMN-962",
            summary="Test",
            is_seam_ticket=False,
            interface_change=False,
            emergency_bypass=ModelEmergencyBypass(enabled=False),
        )

        # Attempting to modify should raise ValidationError
        with pytest.raises(ValidationError):
            contract.ticket_id = "OMN-963"  # type: ignore[assignment, misc]
