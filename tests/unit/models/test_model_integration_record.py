# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for ModelIntegrationRecord and ModelIntegrationProbeResult.

Tests cover:
1. overall_status derived PASS when all tickets PASS
2. overall_status derived FAIL if any ticket FAIL
3. overall_status derived UNKNOWN if mixed PASS/UNKNOWN
4. overall_status UNKNOWN if no tickets
5. Round-trips YAML (dump → safe_load → model_validate)
6. Malformed date rejected
7. UNKNOWN probe with no_contract reason accepted
"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from onex_change_control.enums.enum_integration_surface import EnumIntegrationSurface
from onex_change_control.enums.enum_invariant_status import EnumInvariantStatus
from onex_change_control.enums.enum_probe_reason import EnumProbeReason
from onex_change_control.models.model_integration_record import (
    ModelIntegrationProbeResult,
    ModelIntegrationRecord,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = {
    "schema_version": "1.0.0",
    "date": "2026-03-18",
    "run_id": "run-abc123",
}


def _probe(
    surface: EnumIntegrationSurface,
    status: EnumInvariantStatus,
    reason: EnumProbeReason | None = None,
) -> ModelIntegrationProbeResult:
    return ModelIntegrationProbeResult(surface=surface, status=status, reason=reason)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModelIntegrationRecord:
    """Tests for ModelIntegrationRecord and ModelIntegrationProbeResult."""

    def test_overall_status_pass_when_all_pass(self) -> None:
        """overall_status is PASS when every ticket probe result is PASS."""
        record = ModelIntegrationRecord(
            **_BASE,
            tickets=[
                _probe(EnumIntegrationSurface.KAFKA, EnumInvariantStatus.PASS),
                _probe(EnumIntegrationSurface.DB, EnumInvariantStatus.PASS),
                _probe(EnumIntegrationSurface.CI, EnumInvariantStatus.PASS),
            ],
        )
        assert record.overall_status == EnumInvariantStatus.PASS

    def test_overall_status_fail_if_any_fail(self) -> None:
        """overall_status is FAIL if any ticket probe result is FAIL."""
        record = ModelIntegrationRecord(
            **_BASE,
            tickets=[
                _probe(EnumIntegrationSurface.KAFKA, EnumInvariantStatus.PASS),
                _probe(EnumIntegrationSurface.DB, EnumInvariantStatus.FAIL),
                _probe(EnumIntegrationSurface.CI, EnumInvariantStatus.PASS),
            ],
        )
        assert record.overall_status == EnumInvariantStatus.FAIL

    def test_overall_status_unknown_if_mixed_pass_unknown(self) -> None:
        """overall_status is UNKNOWN if mix of PASS and UNKNOWN (no FAIL)."""
        record = ModelIntegrationRecord(
            **_BASE,
            tickets=[
                _probe(EnumIntegrationSurface.KAFKA, EnumInvariantStatus.PASS),
                _probe(
                    EnumIntegrationSurface.SCRIPT,
                    EnumInvariantStatus.UNKNOWN,
                    reason=EnumProbeReason.PROBE_UNAVAILABLE,
                ),
            ],
        )
        assert record.overall_status == EnumInvariantStatus.UNKNOWN

    def test_overall_status_unknown_if_no_tickets(self) -> None:
        """overall_status is UNKNOWN when the tickets list is empty."""
        record = ModelIntegrationRecord(**_BASE, tickets=[])
        assert record.overall_status == EnumInvariantStatus.UNKNOWN

    def test_yaml_roundtrip(self) -> None:
        """Model round-trips through YAML dump → safe_load → model_validate."""
        original = ModelIntegrationRecord(
            **_BASE,
            tickets=[
                _probe(EnumIntegrationSurface.KAFKA, EnumInvariantStatus.PASS),
                _probe(
                    EnumIntegrationSurface.DB,
                    EnumInvariantStatus.UNKNOWN,
                    reason=EnumProbeReason.NO_CONTRACT,
                ),
            ],
        )
        # Dump to YAML via model_dump then safe_load back
        dumped = yaml.safe_dump(original.model_dump(mode="json"), sort_keys=False)
        loaded_dict = yaml.safe_load(dumped)
        rehydrated = ModelIntegrationRecord.model_validate(loaded_dict)

        assert rehydrated.schema_version == original.schema_version
        assert rehydrated.date == original.date
        assert rehydrated.run_id == original.run_id
        assert len(rehydrated.tickets) == len(original.tickets)
        assert rehydrated.overall_status == original.overall_status

    def test_malformed_date_rejected(self) -> None:
        """ValidationError is raised when date is not ISO YYYY-MM-DD format."""
        with pytest.raises(ValidationError):
            ModelIntegrationRecord(**{**_BASE, "date": "18-03-2026"})

    def test_invalid_calendar_date_rejected(self) -> None:
        """ValidationError is raised for a formatted-but-invalid calendar date."""
        with pytest.raises(ValidationError):
            ModelIntegrationRecord(**{**_BASE, "date": "2026-02-30"})

    def test_unknown_probe_with_no_contract_reason_accepted(self) -> None:
        """UNKNOWN probe with no_contract reason is valid and accepted."""
        probe = ModelIntegrationProbeResult(
            surface=EnumIntegrationSurface.PLUGIN,
            status=EnumInvariantStatus.UNKNOWN,
            reason=EnumProbeReason.NO_CONTRACT,
            detail="No contract file found for plugin hook",
            checked_at="2026-03-18",
        )
        record = ModelIntegrationRecord(**_BASE, tickets=[probe])

        assert record.tickets[0].reason == EnumProbeReason.NO_CONTRACT
        assert record.tickets[0].status == EnumInvariantStatus.UNKNOWN
        assert record.overall_status == EnumInvariantStatus.UNKNOWN

    def test_container_health_probe_result_roundtrip(self) -> None:
        """New CONTAINER_HEALTH surface accepted in probe result."""
        result = ModelIntegrationProbeResult(
            surface=EnumIntegrationSurface.CONTAINER_HEALTH,
            status=EnumInvariantStatus.FAIL,
            detail="omninode-runtime stuck in Created state",
            checked_at="2026-03-22",
        )
        assert result.surface == EnumIntegrationSurface.CONTAINER_HEALTH
        assert result.status == EnumInvariantStatus.FAIL

    def test_overall_fail_when_container_health_fails(self) -> None:
        """Record with CONTAINER_HEALTH FAIL drives overall_status to FAIL."""
        record = ModelIntegrationRecord(
            **_BASE,
            tickets=[
                _probe(EnumIntegrationSurface.CI, EnumInvariantStatus.PASS),
                ModelIntegrationProbeResult(
                    surface=EnumIntegrationSurface.CONTAINER_HEALTH,
                    status=EnumInvariantStatus.FAIL,
                    detail="2 containers not running",
                ),
            ],
        )
        assert record.overall_status == EnumInvariantStatus.FAIL
