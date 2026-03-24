# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for DoD sweep handler."""

from pathlib import Path
from unittest.mock import patch

import pytest

from onex_change_control.enums.enum_dod_sweep_check import EnumDodSweepCheck
from onex_change_control.enums.enum_invariant_status import EnumInvariantStatus
from onex_change_control.handlers.handler_dod_sweep import run_dod_sweep
from onex_change_control.models.model_dod_sweep import ModelDodSweepResult


@pytest.mark.unit
class TestRunDodSweep:
    """Test run_dod_sweep handler function."""

    def test_returns_model_dod_sweep_result(self, tmp_path: Path) -> None:
        """Handler returns ModelDodSweepResult type."""
        with patch(
            "onex_change_control.handlers.handler_dod_sweep.fetch_completed_tickets",
            return_value=[],
        ):
            result = run_dod_sweep(
                contracts_dir=tmp_path,
                since_days=7,
                exemptions_path=None,
                api_key="test-key",
            )
        assert isinstance(result, ModelDodSweepResult)
        assert result.schema_version == "1.0.0"
        assert result.mode == "batch"
        assert result.total_tickets == 0

    def test_ticket_with_contract_passes(self, tmp_path: Path) -> None:
        """Ticket with existing contract file gets PASS for CONTRACT_EXISTS."""
        ticket_dir = tmp_path / "OMN-9999.yaml"
        ticket_dir.write_text("test: true")

        fake_tickets = [
            {"id": "OMN-9999", "title": "Test", "completedAt": "2026-03-24"}
        ]
        with patch(
            "onex_change_control.handlers.handler_dod_sweep.fetch_completed_tickets",
            return_value=fake_tickets,
        ):
            result = run_dod_sweep(
                contracts_dir=tmp_path,
                since_days=7,
                exemptions_path=None,
                api_key="test-key",
            )
        assert result.total_tickets == 1
        contract_check = next(
            c
            for t in result.tickets
            for c in t.checks
            if c.check == EnumDodSweepCheck.CONTRACT_EXISTS
        )
        assert contract_check.status == EnumInvariantStatus.PASS

    def test_exempt_ticket_is_unknown_not_pass(self, tmp_path: Path) -> None:
        """Exempt tickets have overall_status UNKNOWN, not PASS."""
        exemptions_file = tmp_path / "exemptions.yaml"
        exemptions_file.write_text(
            "cutoff_date: '2026-01-01'\nexemptions:\n  - ticket_id: OMN-8888\n"
        )
        fake_tickets = [
            {"id": "OMN-8888", "title": "Exempt", "completedAt": "2025-12-15"}
        ]
        with patch(
            "onex_change_control.handlers.handler_dod_sweep.fetch_completed_tickets",
            return_value=fake_tickets,
        ):
            result = run_dod_sweep(
                contracts_dir=tmp_path,
                since_days=7,
                exemptions_path=exemptions_file,
                api_key="test-key",
            )
        assert result.tickets[0].exempted is True
        assert result.tickets[0].overall_status == EnumInvariantStatus.UNKNOWN
