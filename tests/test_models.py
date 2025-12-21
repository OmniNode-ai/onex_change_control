"""Tests for Pydantic schema models."""

import pytest
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
    ModelDayCloseProcessChange,
    ModelDayCloseRisk,
)
from onex_change_control.models.model_ticket_contract import (
    ModelEmergencyBypass,
    ModelEvidenceRequirement,
    ModelTicketContract,
)


class TestModelDayClose:
    """Tests for ModelDayClose."""

    def test_valid_day_close(self) -> None:
        """Test creating a valid day close report."""
        day_close = ModelDayClose(
            schema_version="1.0.0",
            date="2025-12-19",
            invariants_checked=ModelDayCloseInvariantsChecked(
                reducers_pure=EnumInvariantStatus.UNKNOWN,
                orchestrators_no_io=EnumInvariantStatus.UNKNOWN,
                effects_do_io_only=EnumInvariantStatus.PASS,
                real_infra_proof_progressing=EnumInvariantStatus.UNKNOWN,
            ),
        )
        assert day_close.schema_version == "1.0.0"
        assert day_close.date == "2025-12-19"
        assert len(day_close.plan) == 0
        assert len(day_close.actual_by_repo) == 0

    def test_invalid_schema_version(self) -> None:
        """Test invalid schema_version format."""
        with pytest.raises(ValueError, match="Invalid schema_version format"):
            ModelDayClose(
                schema_version="invalid",
                date="2025-12-19",
                invariants_checked=ModelDayCloseInvariantsChecked(
                    reducers_pure=EnumInvariantStatus.UNKNOWN,
                    orchestrators_no_io=EnumInvariantStatus.UNKNOWN,
                    effects_do_io_only=EnumInvariantStatus.PASS,
                    real_infra_proof_progressing=EnumInvariantStatus.UNKNOWN,
                ),
            )

    def test_invalid_date_format(self) -> None:
        """Test invalid date format."""
        with pytest.raises(ValueError, match="Invalid date format"):
            ModelDayClose(
                schema_version="1.0.0",
                date="2025/12/19",
                invariants_checked=ModelDayCloseInvariantsChecked(
                    reducers_pure=EnumInvariantStatus.UNKNOWN,
                    orchestrators_no_io=EnumInvariantStatus.UNKNOWN,
                    effects_do_io_only=EnumInvariantStatus.PASS,
                    real_infra_proof_progressing=EnumInvariantStatus.UNKNOWN,
                ),
            )

    def test_invalid_calendar_date(self) -> None:
        """Test calendar-invalid dates (e.g., February 30th)."""
        with pytest.raises(ValueError, match="Invalid calendar date"):
            ModelDayClose(
                schema_version="1.0.0",
                date="2025-02-30",  # Invalid: February only has 28/29 days
                invariants_checked=ModelDayCloseInvariantsChecked(
                    reducers_pure=EnumInvariantStatus.UNKNOWN,
                    orchestrators_no_io=EnumInvariantStatus.UNKNOWN,
                    effects_do_io_only=EnumInvariantStatus.PASS,
                    real_infra_proof_progressing=EnumInvariantStatus.UNKNOWN,
                ),
            )

        with pytest.raises(ValueError, match="Invalid calendar date"):
            ModelDayClose(
                schema_version="1.0.0",
                date="2025-13-01",  # Invalid: month 13 doesn't exist
                invariants_checked=ModelDayCloseInvariantsChecked(
                    reducers_pure=EnumInvariantStatus.UNKNOWN,
                    orchestrators_no_io=EnumInvariantStatus.UNKNOWN,
                    effects_do_io_only=EnumInvariantStatus.PASS,
                    real_infra_proof_progressing=EnumInvariantStatus.UNKNOWN,
                ),
            )

    def test_complete_day_close(self) -> None:
        """Test creating a complete day close report."""
        day_close = ModelDayClose(
            schema_version="1.0.0",
            date="2025-12-19",
            process_changes_today=[
                ModelDayCloseProcessChange(
                    change="Introduce structured daily close",
                    rationale="Need explicit reconciliation",
                    replaces="Implicit tracking",
                )
            ],
            plan=[
                ModelDayClosePlanItem(
                    requirement_id="MVP-2WAY-REGISTRATION",
                    summary="2-way registration workflow",
                )
            ],
            actual_by_repo=[
                ModelDayCloseActualRepo(
                    repo="OmniNode-ai/omnibase_core",
                    prs=[
                        ModelDayClosePR(
                            pr=218,
                            title="Canonical message envelope model",
                            state=EnumPRState.MERGED,
                            notes="Required for routing",
                        )
                    ],
                )
            ],
            drift_detected=[
                ModelDayCloseDriftDetected(
                    drift_id="DRIFT-001",
                    category=EnumDriftCategory.DEPENDENCIES,
                    evidence="Cross-repo drift",
                    impact="Risk of rework",
                    correction_for_tomorrow="Keep daily close discipline",
                )
            ],
            invariants_checked=ModelDayCloseInvariantsChecked(
                reducers_pure=EnumInvariantStatus.UNKNOWN,
                orchestrators_no_io=EnumInvariantStatus.UNKNOWN,
                effects_do_io_only=EnumInvariantStatus.PASS,
                real_infra_proof_progressing=EnumInvariantStatus.UNKNOWN,
            ),
            corrections_for_tomorrow=["Make workflow explicit"],
            risks=[
                ModelDayCloseRisk(
                    risk="Infra flake",
                    mitigation="Prioritize deterministic harness",
                )
            ],
        )
        assert len(day_close.process_changes_today) == 1
        assert len(day_close.plan) == 1
        assert len(day_close.actual_by_repo) == 1
        assert len(day_close.drift_detected) == 1
        assert len(day_close.corrections_for_tomorrow) == 1
        assert len(day_close.risks) == 1

    def test_pr_number_validation(self) -> None:
        """Test that PR numbers must be >= 1."""
        # Valid PR number
        pr = ModelDayClosePR(
            pr=1,
            title="Test PR",
            state=EnumPRState.OPEN,
            notes="Test",
        )
        assert pr.pr == 1

        # Invalid PR number (0)
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            ModelDayClosePR(
                pr=0,
                title="Test PR",
                state=EnumPRState.OPEN,
                notes="Test",
            )

        # Invalid PR number (negative)
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            ModelDayClosePR(
                pr=-1,
                title="Test PR",
                state=EnumPRState.OPEN,
                notes="Test",
            )


class TestModelTicketContract:
    """Tests for ModelTicketContract."""

    def test_valid_ticket_contract(self) -> None:
        """Test creating a valid ticket contract."""
        contract = ModelTicketContract(
            schema_version="1.0.0",
            ticket_id="OMN-962",
            summary="Implement Pydantic schema models",
            is_seam_ticket=False,
            interface_change=False,
            emergency_bypass=ModelEmergencyBypass(enabled=False),
        )
        assert contract.schema_version == "1.0.0"
        assert contract.ticket_id == "OMN-962"
        assert contract.is_seam_ticket is False
        assert contract.interface_change is False
        assert len(contract.interfaces_touched) == 0

    def test_invalid_schema_version(self) -> None:
        """Test invalid schema_version format."""
        with pytest.raises(ValueError, match="Invalid schema_version format"):
            ModelTicketContract(
                schema_version="invalid",
                ticket_id="OMN-962",
                summary="Test",
                is_seam_ticket=False,
                interface_change=False,
                emergency_bypass=ModelEmergencyBypass(enabled=False),
            )

    def test_semver_edge_cases(self) -> None:
        """Test SemVer edge cases and boundary conditions."""
        # Valid: basic SemVer
        contract = ModelTicketContract(
            schema_version="0.0.0",
            ticket_id="OMN-962",
            summary="Test",
            is_seam_ticket=False,
            interface_change=False,
            emergency_bypass=ModelEmergencyBypass(enabled=False),
        )
        assert contract.schema_version == "0.0.0"

        # Valid: large version numbers
        contract = ModelTicketContract(
            schema_version="999.999.999",
            ticket_id="OMN-962",
            summary="Test",
            is_seam_ticket=False,
            interface_change=False,
            emergency_bypass=ModelEmergencyBypass(enabled=False),
        )
        assert contract.schema_version == "999.999.999"

        # Note: Leading zeros are accepted by our basic pattern (e.g., "01.0.0")
        # This is a limitation of the basic SemVer pattern. Standard SemVer doesn't
        # allow leading zeros, but our pattern uses \d+ which matches them.
        # If strict SemVer compliance is needed, use a SemVer library.
        contract = ModelTicketContract(
            schema_version="01.0.0",  # Accepted by basic pattern (limitation)
            ticket_id="OMN-962",
            summary="Test",
            is_seam_ticket=False,
            interface_change=False,
            emergency_bypass=ModelEmergencyBypass(enabled=False),
        )
        assert contract.schema_version == "01.0.0"

        # Invalid: missing components
        with pytest.raises(ValueError, match="Invalid schema_version format"):
            ModelTicketContract(
                schema_version="1.0",
                ticket_id="OMN-962",
                summary="Test",
                is_seam_ticket=False,
                interface_change=False,
                emergency_bypass=ModelEmergencyBypass(enabled=False),
            )

        # Invalid: pre-release (not supported by basic pattern)
        with pytest.raises(ValueError, match="Invalid schema_version format"):
            ModelTicketContract(
                schema_version="1.0.0-alpha",
                ticket_id="OMN-962",
                summary="Test",
                is_seam_ticket=False,
                interface_change=False,
                emergency_bypass=ModelEmergencyBypass(enabled=False),
            )

    def test_interface_change_constraint(self) -> None:
        """Test interface_change constraint validation."""
        with pytest.raises(
            ValueError,
            match="interfaces_touched must be empty when interface_change is false",
        ):
            ModelTicketContract(
                schema_version="1.0.0",
                ticket_id="OMN-962",
                summary="Test",
                is_seam_ticket=False,
                interface_change=False,
                interfaces_touched=[EnumInterfaceSurface.EVENTS],
                emergency_bypass=ModelEmergencyBypass(enabled=False),
            )

    def test_emergency_bypass_validation(self) -> None:
        """Test emergency bypass validation."""
        with pytest.raises(ValueError, match="justification is required"):
            ModelEmergencyBypass(
                enabled=True,
                justification="",
                follow_up_ticket_id="OMN-963",
            )

        with pytest.raises(ValueError, match="follow_up_ticket_id is required"):
            ModelEmergencyBypass(
                enabled=True,
                justification="Emergency fix",
                follow_up_ticket_id="",
            )

    def test_complete_ticket_contract(self) -> None:
        """Test creating a complete ticket contract."""
        contract = ModelTicketContract(
            schema_version="1.0.0",
            ticket_id="OMN-962",
            summary="Implement Pydantic schema models",
            is_seam_ticket=True,
            interface_change=True,
            interfaces_touched=[
                EnumInterfaceSurface.EVENTS,
                EnumInterfaceSurface.TOPICS,
            ],
            evidence_requirements=[
                ModelEvidenceRequirement(
                    kind=EnumEvidenceKind.TESTS,
                    description="Unit tests for core structural validation",
                    command="poetry run pytest tests/test_models.py",
                )
            ],
            emergency_bypass=ModelEmergencyBypass(enabled=False),
        )
        assert contract.is_seam_ticket is True
        assert contract.interface_change is True
        expected_interfaces_count = 2
        assert len(contract.interfaces_touched) == expected_interfaces_count
        assert len(contract.evidence_requirements) == 1

    def test_interface_change_true_with_empty_interfaces(self) -> None:
        """Test that interface_change=True with empty interfaces_touched is allowed.

        This is intentionally allowed to support cases where interfaces are changed
        but categorization is pending.
        """
        contract = ModelTicketContract(
            schema_version="1.0.0",
            ticket_id="OMN-962",
            summary="Test ticket",
            is_seam_ticket=False,
            interface_change=True,
            interfaces_touched=[],  # Empty is allowed when interface_change=True
            emergency_bypass=ModelEmergencyBypass(enabled=False),
        )
        assert contract.interface_change is True
        assert len(contract.interfaces_touched) == 0
