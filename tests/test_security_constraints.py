"""Tests for security constraints (DoS prevention)."""

import pytest
from pydantic import ValidationError

from onex_change_control.enums.enum_invariant_status import EnumInvariantStatus
from onex_change_control.models.model_day_close import (
    _MAX_LIST_ITEMS,
    _MAX_STRING_LENGTH,
    ModelDayClose,
    ModelDayCloseInvariantsChecked,
    ModelDayClosePlanItem,
)
from onex_change_control.models.model_ticket_contract import (
    ModelEmergencyBypass,
    ModelTicketContract,
)


class TestStringLengthConstraints:
    """Tests for max string length constraints."""

    def test_valid_string_length(self) -> None:
        """Test that strings within limit are accepted."""
        # Create a string at the limit
        long_string = "x" * _MAX_STRING_LENGTH

        plan_item = ModelDayClosePlanItem(
            requirement_id="TEST",
            summary=long_string,
        )
        assert len(plan_item.summary) == _MAX_STRING_LENGTH

    def test_excessive_string_length(self) -> None:
        """Test that strings exceeding limit are rejected.

        Pydantic v2 enforces max_length constraints on string fields.
        This test verifies that enforcement is working correctly.
        """
        # Create a string exceeding the limit
        too_long_string = "x" * (_MAX_STRING_LENGTH + 1)

        # Pydantic v2 should reject strings exceeding max_length
        with pytest.raises(ValidationError) as exc_info:
            ModelDayClosePlanItem(
                requirement_id="TEST",
                summary=too_long_string,
            )

        # Verify the error message indicates the length constraint
        error_str = str(exc_info.value).lower()
        assert (
            "max length" in error_str
            or "too long" in error_str
            or "at most" in error_str
        )


class TestListLengthConstraints:
    """Tests for max list items constraints."""

    def test_valid_list_length(self) -> None:
        """Test that lists within limit are accepted."""
        # Create a list at the limit
        plan_items = [
            ModelDayClosePlanItem(requirement_id=f"REQ-{i}", summary=f"Summary {i}")
            for i in range(_MAX_LIST_ITEMS)
        ]

        day_close = ModelDayClose(
            schema_version="1.0.0",
            date="2025-12-20",
            plan=plan_items,
            invariants_checked=ModelDayCloseInvariantsChecked(
                reducers_pure=EnumInvariantStatus.PASS,
                orchestrators_no_io=EnumInvariantStatus.PASS,
                effects_do_io_only=EnumInvariantStatus.PASS,
                real_infra_proof_progressing=EnumInvariantStatus.UNKNOWN,
            ),
        )
        assert len(day_close.plan) == _MAX_LIST_ITEMS

    def test_excessive_list_length(self) -> None:
        """Test that lists exceeding limit are rejected."""
        # Create a list exceeding the limit
        too_many_items = [
            ModelDayClosePlanItem(requirement_id=f"REQ-{i}", summary=f"Summary {i}")
            for i in range(_MAX_LIST_ITEMS + 1)
        ]

        with pytest.raises(ValidationError):
            ModelDayClose(
                schema_version="1.0.0",
                date="2025-12-20",
                plan=too_many_items,
                invariants_checked=ModelDayCloseInvariantsChecked(
                    reducers_pure=EnumInvariantStatus.PASS,
                    orchestrators_no_io=EnumInvariantStatus.PASS,
                    effects_do_io_only=EnumInvariantStatus.PASS,
                    real_infra_proof_progressing=EnumInvariantStatus.UNKNOWN,
                ),
            )


class TestTicketContractConstraints:
    """Tests for ticket contract security constraints."""

    def test_ticket_id_length_constraint(self) -> None:
        """Test that ticket_id has appropriate length limit."""
        # Valid: short ticket ID
        contract = ModelTicketContract(
            schema_version="1.0.0",
            ticket_id="OMN-962",
            summary="Test",
            is_seam_ticket=False,
            interface_change=False,
            emergency_bypass=ModelEmergencyBypass(enabled=False),
        )
        assert contract.ticket_id == "OMN-962"

        # Invalid: ticket ID too long
        too_long_id = "OMN-" + "x" * 50
        with pytest.raises(ValidationError):
            ModelTicketContract(
                schema_version="1.0.0",
                ticket_id=too_long_id,
                summary="Test",
                is_seam_ticket=False,
                interface_change=False,
                emergency_bypass=ModelEmergencyBypass(enabled=False),
            )
