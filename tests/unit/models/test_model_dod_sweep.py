# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for DoD sweep models.

Tests cover:
1. ModelDodSweepCheckResult -- all 6 check types, all 3 statuses
2. ModelDodSweepTicketResult -- derived overall_status (all pass, any fail, mixed)
3. ModelDodSweepResult -- derived aggregates, SemVer validation, date validation
4. Frozen immutability -- attempting to set attributes raises TypeError
5. Exempted ticket stays UNKNOWN (not PASS)
6. All-exempted aggregate is UNKNOWN
7. Mixed pass/fail/exempt aggregate
8. Empty tickets list -- UNKNOWN overall
9. Single non-exempted pass -- PASS overall
10. All pass + some exempt -- PASS overall
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from onex_change_control.enums.enum_dod_sweep_check import EnumDodSweepCheck
from onex_change_control.enums.enum_invariant_status import EnumInvariantStatus
from onex_change_control.models.model_dod_sweep import (
    ModelDodSweepCheckResult,
    ModelDodSweepResult,
    ModelDodSweepTicketResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SWEEP_BASE = {
    "schema_version": "1.0.0",
    "date": "2026-03-22",
    "run_id": "run-test-001",
    "mode": "batch",
}


def _check(
    check: EnumDodSweepCheck,
    status: EnumInvariantStatus,
) -> ModelDodSweepCheckResult:
    return ModelDodSweepCheckResult(check=check, status=status)


def _all_pass_checks() -> list[ModelDodSweepCheckResult]:
    return [_check(c, EnumInvariantStatus.PASS) for c in EnumDodSweepCheck]


def _ticket(
    ticket_id: str,
    checks: list[ModelDodSweepCheckResult],
    *,
    exempted: bool = False,
    exemption_reason: str | None = None,
) -> ModelDodSweepTicketResult:
    return ModelDodSweepTicketResult(
        ticket_id=ticket_id,
        title=f"Test ticket {ticket_id}",
        checks=checks,
        exempted=exempted,
        exemption_reason=exemption_reason,
    )


@pytest.mark.unit
class TestModelDodSweepCheckResult:
    """Tests for individual check results."""

    def test_all_check_types_accepted(self) -> None:
        """All 6 EnumDodSweepCheck values are accepted."""
        for check_type in EnumDodSweepCheck:
            result = _check(check_type, EnumInvariantStatus.PASS)
            assert result.check == check_type

    def test_all_statuses_accepted(self) -> None:
        """All 3 EnumInvariantStatus values are accepted."""
        for status in EnumInvariantStatus:
            result = _check(EnumDodSweepCheck.CONTRACT_EXISTS, status)
            assert result.status == status

    def test_unknown_subtype_accepted(self) -> None:
        """unknown_subtype field accepts valid literal values."""
        result = ModelDodSweepCheckResult(
            check=EnumDodSweepCheck.PR_MERGED,
            status=EnumInvariantStatus.UNKNOWN,
            unknown_subtype="linkage_inconclusive",
            detail="No merged PR found matching ticket ID",
        )
        assert result.unknown_subtype == "linkage_inconclusive"

    def test_frozen_immutability(self) -> None:
        """Attempting to set attributes on a frozen model raises."""
        result = _check(EnumDodSweepCheck.CONTRACT_EXISTS, EnumInvariantStatus.PASS)
        with pytest.raises(ValidationError):
            result.status = EnumInvariantStatus.FAIL  # type: ignore[misc]


@pytest.mark.unit
class TestModelDodSweepTicketResult:
    """Tests for per-ticket result aggregation."""

    def test_overall_pass_when_all_pass(self) -> None:
        """overall_status is PASS when all 6 checks pass."""
        t = _ticket("OMN-1001", _all_pass_checks())
        assert t.overall_status == EnumInvariantStatus.PASS

    def test_overall_fail_when_any_fail(self) -> None:
        """overall_status is FAIL if any check fails."""
        checks = _all_pass_checks()
        checks[2] = _check(EnumDodSweepCheck.RECEIPT_CLEAN, EnumInvariantStatus.FAIL)
        t = _ticket("OMN-1002", checks)
        assert t.overall_status == EnumInvariantStatus.FAIL

    def test_overall_unknown_when_mixed(self) -> None:
        """overall_status is UNKNOWN with mixed PASS/UNKNOWN and no FAIL."""
        checks = _all_pass_checks()
        checks[4] = _check(EnumDodSweepCheck.CI_GREEN, EnumInvariantStatus.UNKNOWN)
        t = _ticket("OMN-1003", checks)
        assert t.overall_status == EnumInvariantStatus.UNKNOWN

    def test_overall_unknown_when_empty_checks(self) -> None:
        """overall_status is UNKNOWN with empty checks list."""
        t = _ticket("OMN-1004", [])
        assert t.overall_status == EnumInvariantStatus.UNKNOWN

    def test_exempted_ticket_stays_unknown(self) -> None:
        """Exempted ticket overall_status is UNKNOWN, not PASS."""
        t = _ticket(
            "OMN-1005",
            _all_pass_checks(),
            exempted=True,
            exemption_reason="Pre-contract era ticket",
        )
        assert t.overall_status == EnumInvariantStatus.UNKNOWN
        assert t.exempted is True

    def test_frozen_immutability(self) -> None:
        """Attempting to set attributes on a frozen model raises."""
        t = _ticket("OMN-1006", _all_pass_checks())
        with pytest.raises(ValidationError):
            t.ticket_id = "OMN-9999"  # type: ignore[misc]


@pytest.mark.unit
class TestModelDodSweepResult:
    """Tests for aggregate sweep result."""

    def test_all_pass_aggregate(self) -> None:
        """overall_status is PASS when all non-exempted tickets pass."""
        result = ModelDodSweepResult(
            **_SWEEP_BASE,
            tickets=[
                _ticket("OMN-2001", _all_pass_checks()),
                _ticket("OMN-2002", _all_pass_checks()),
            ],
        )
        assert result.overall_status == EnumInvariantStatus.PASS
        assert result.total_tickets == 2
        assert result.passed == 2
        assert result.failed == 0
        assert result.exempted == 0

    def test_any_fail_aggregate(self) -> None:
        """overall_status is FAIL if any ticket has a failure."""
        fail_checks = _all_pass_checks()
        fail_checks[0] = _check(
            EnumDodSweepCheck.CONTRACT_EXISTS, EnumInvariantStatus.FAIL
        )
        result = ModelDodSweepResult(
            **_SWEEP_BASE,
            tickets=[
                _ticket("OMN-2003", _all_pass_checks()),
                _ticket("OMN-2004", fail_checks),
            ],
        )
        assert result.overall_status == EnumInvariantStatus.FAIL
        assert result.passed == 1
        assert result.failed == 1

    def test_all_exempted_is_unknown(self) -> None:
        """All-exempted aggregate produces overall_status=UNKNOWN."""
        result = ModelDodSweepResult(
            **_SWEEP_BASE,
            tickets=[
                _ticket("OMN-2005", [], exempted=True, exemption_reason="Old"),
                _ticket("OMN-2006", [], exempted=True, exemption_reason="Old"),
            ],
        )
        assert result.overall_status == EnumInvariantStatus.UNKNOWN
        assert result.exempted == 2
        assert result.passed == 0

    def test_mixed_pass_fail_exempt(self) -> None:
        """Mixed: 2 passed + 1 failed + 1 exempt -> FAIL, correct counts."""
        fail_checks = _all_pass_checks()
        fail_checks[1] = _check(
            EnumDodSweepCheck.RECEIPT_EXISTS, EnumInvariantStatus.FAIL
        )
        result = ModelDodSweepResult(
            **_SWEEP_BASE,
            tickets=[
                _ticket("OMN-2007", _all_pass_checks()),
                _ticket("OMN-2008", _all_pass_checks()),
                _ticket("OMN-2009", fail_checks),
                _ticket("OMN-2010", [], exempted=True, exemption_reason="Old"),
            ],
        )
        assert result.overall_status == EnumInvariantStatus.FAIL
        assert result.passed == 2
        assert result.failed == 1
        assert result.exempted == 1

    def test_empty_tickets_is_unknown(self) -> None:
        """Empty tickets list produces UNKNOWN overall."""
        result = ModelDodSweepResult(**_SWEEP_BASE, tickets=[])
        assert result.overall_status == EnumInvariantStatus.UNKNOWN
        assert result.total_tickets == 0

    def test_single_pass_is_pass(self) -> None:
        """Single non-exempted pass produces PASS overall."""
        result = ModelDodSweepResult(
            **_SWEEP_BASE,
            tickets=[_ticket("OMN-2011", _all_pass_checks())],
        )
        assert result.overall_status == EnumInvariantStatus.PASS
        assert result.passed == 1

    def test_pass_plus_exempt_is_pass(self) -> None:
        """All pass + some exempt produces PASS overall."""
        result = ModelDodSweepResult(
            **_SWEEP_BASE,
            tickets=[
                _ticket("OMN-2012", _all_pass_checks()),
                _ticket("OMN-2013", [], exempted=True, exemption_reason="Old"),
            ],
        )
        assert result.overall_status == EnumInvariantStatus.PASS
        assert result.passed == 1
        assert result.exempted == 1

    def test_invalid_semver_rejected(self) -> None:
        """Invalid schema_version is rejected."""
        with pytest.raises(ValidationError, match="SemVer"):
            ModelDodSweepResult(
                schema_version="bad",
                date="2026-03-22",
                run_id="run-1",
                mode="batch",
            )

    def test_invalid_date_rejected(self) -> None:
        """Invalid date format is rejected."""
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            ModelDodSweepResult(
                schema_version="1.0.0",
                date="not-a-date",
                run_id="run-1",
                mode="batch",
            )

    def test_targeted_mode_fields(self) -> None:
        """Targeted mode accepts target_id field."""
        result = ModelDodSweepResult(
            schema_version="1.0.0",
            date="2026-03-22",
            run_id="run-targeted",
            mode="targeted",
            target_id="OMN-5000",
            tickets=[],
        )
        assert result.mode == "targeted"
        assert result.target_id == "OMN-5000"

    def test_frozen_immutability(self) -> None:
        """Attempting to set attributes on a frozen model raises."""
        result = ModelDodSweepResult(**_SWEEP_BASE, tickets=[])
        with pytest.raises(ValidationError):
            result.run_id = "changed"  # type: ignore[misc]
