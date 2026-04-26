# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for DoD sweep handler."""

import warnings
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from onex_change_control.enums.enum_dod_sweep_check import EnumDodSweepCheck
from onex_change_control.enums.enum_invariant_status import EnumInvariantStatus
from onex_change_control.handlers import handler_dod_sweep
from onex_change_control.handlers.handler_dod_sweep import (
    check_receipt_exists,
    run_dod_sweep,
)
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


@pytest.mark.unit
class TestHandlerReceiptReconciliation:
    """OMN-9791: handler honours the same legacy-receipt hard cutoff."""

    BEFORE_CUTOFF = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
    ON_CUTOFF = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    AFTER_CUTOFF = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)

    def test_handler_cutoff_constant_matches_script(self) -> None:
        """Handler's cutoff is exactly 2026-06-01 (script-handler parity)."""
        assert date(2026, 6, 1) == handler_dod_sweep._LEGACY_RECEIPT_CUTOFF

    def test_check_receipt_exists_canonical_passes(self, tmp_path: Path) -> None:
        contracts = tmp_path / "contracts"
        contracts.mkdir()
        canonical = tmp_path / "drift" / "dod_receipts" / "OMN-2000" / "dod-001"
        canonical.mkdir(parents=True)
        (canonical / "20260501T120000Z.yaml").write_text("status: PASS\n")
        status, detail = check_receipt_exists(
            "OMN-2000", contracts, now=self.BEFORE_CUTOFF
        )
        assert status == "PASS"
        assert "canonical" in detail.lower()

    def test_check_receipt_exists_legacy_pass_with_warning_pre_cutoff(
        self, tmp_path: Path
    ) -> None:
        contracts = tmp_path / "contracts"
        contracts.mkdir()
        legacy_dir = tmp_path / ".evidence" / "OMN-2000"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "dod_report.json").write_text('{"result": {"failed": 0}}')
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            status, detail = check_receipt_exists(
                "OMN-2000", contracts, now=self.BEFORE_CUTOFF
            )
        assert status == "PASS"
        assert "DEPRECATED" in detail
        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) >= 1

    def test_check_receipt_exists_legacy_fails_on_cutoff(self, tmp_path: Path) -> None:
        contracts = tmp_path / "contracts"
        contracts.mkdir()
        legacy_dir = tmp_path / ".evidence" / "OMN-2000"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "dod_report.json").write_text('{"result": {"failed": 0}}')
        status, detail = check_receipt_exists("OMN-2000", contracts, now=self.ON_CUTOFF)
        assert status == "FAIL"
        assert "OMN-9791" in detail

    def test_run_dod_sweep_legacy_only_post_cutoff_marks_receipt_fail(
        self, tmp_path: Path
    ) -> None:
        """Through ``run_dod_sweep``, post-cutoff legacy-only flips RECEIPT_EXISTS."""
        # Contract present so contract check passes.
        (tmp_path / "OMN-3000.yaml").write_text("test: true")
        # Legacy receipt present; canonical absent.
        legacy_dir = tmp_path.parent / ".evidence" / "OMN-3000"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "dod_report.json").write_text('{"result": {"failed": 0}}')

        fake_tickets = [
            {"id": "OMN-3000", "title": "Test", "completedAt": "2026-05-30"}
        ]
        with patch(
            "onex_change_control.handlers.handler_dod_sweep.fetch_completed_tickets",
            return_value=fake_tickets,
        ):
            result = run_dod_sweep(
                contracts_dir=tmp_path,
                since_days=30,
                exemptions_path=None,
                api_key="test-key",
                now=self.AFTER_CUTOFF,
            )

        receipt_check = next(
            c
            for t in result.tickets
            for c in t.checks
            if c.check == EnumDodSweepCheck.RECEIPT_EXISTS
        )
        assert receipt_check.status == EnumInvariantStatus.FAIL
        assert receipt_check.detail is not None
        assert "OMN-9791" in receipt_check.detail

    def test_run_dod_sweep_legacy_only_pre_cutoff_passes_receipt_with_deprecated(
        self, tmp_path: Path
    ) -> None:
        """Pre-cutoff legacy-only still PASSes RECEIPT_EXISTS with DEPRECATED."""
        (tmp_path / "OMN-3001.yaml").write_text("test: true")
        legacy_dir = tmp_path.parent / ".evidence" / "OMN-3001"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "dod_report.json").write_text('{"result": {"failed": 0}}')

        fake_tickets = [
            {"id": "OMN-3001", "title": "Test", "completedAt": "2026-05-30"}
        ]
        with (
            patch(
                "onex_change_control.handlers.handler_dod_sweep.fetch_completed_tickets",
                return_value=fake_tickets,
            ),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            result = run_dod_sweep(
                contracts_dir=tmp_path,
                since_days=30,
                exemptions_path=None,
                api_key="test-key",
                now=self.BEFORE_CUTOFF,
            )

        receipt_check = next(
            c
            for t in result.tickets
            for c in t.checks
            if c.check == EnumDodSweepCheck.RECEIPT_EXISTS
        )
        assert receipt_check.status == EnumInvariantStatus.PASS
        assert receipt_check.detail is not None
        assert "DEPRECATED" in receipt_check.detail
        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) >= 1
