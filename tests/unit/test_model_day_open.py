# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for ModelDayOpen and sub-models.

Tests cover:
- Minimal and full construction of ModelDayOpen
- SemVer and date validation (matching ModelDayClose patterns)
- Frozen immutability enforcement
- Enum value correctness for EnumFindingSeverity and EnumProbeStatus
- Sub-model field validation
- List constraint enforcement (max_length)
- Serialization roundtrip via model_validate + model_dump
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from onex_change_control.enums.enum_finding_severity import EnumFindingSeverity
from onex_change_control.enums.enum_probe_status import EnumProbeStatus
from onex_change_control.models.model_day_open import (
    ModelDayOpen,
    ModelDayOpenFinding,
    ModelDayOpenProbeResult,
    ModelDayOpenRepoSyncEntry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_day_open_data() -> dict[str, object]:
    """Minimal valid ModelDayOpen data."""
    return {
        "schema_version": "1.0.0",
        "date": "2026-03-18",
        "run_id": "abc123def456",
    }


@pytest.fixture
def full_day_open_data() -> dict[str, object]:
    """Fully populated ModelDayOpen data."""
    return {
        "schema_version": "1.0.0",
        "date": "2026-03-18",
        "run_id": "abc123def456",
        "yesterday_corrections": [
            "Fix env parity for OMNIWEB_DB_URL",
            "Address CI failure in omnibase_core PR #55",
        ],
        "repo_sync_status": [
            {
                "repo": "omniclaude",
                "branch": "main",
                "up_to_date": True,
                "head_sha": "a1b2c3d4e5f6",
            },
            {
                "repo": "omnibase_core",
                "branch": "main",
                "up_to_date": False,
                "head_sha": "f6e5d4c3b2a1",
                "error": None,
            },
        ],
        "infra_health": [
            {
                "service": "postgres",
                "running": True,
                "port_responding": True,
            },
            {
                "service": "redpanda",
                "running": True,
                "port_responding": False,
                "error": "Connection refused on port 19092",
            },
        ],
        "probe_results": [
            {
                "probe_name": "list_prs",
                "status": "completed",
                "artifact_path": "~/.claude/begin-day/abc123/list_prs.json",
                "summary": "Found 3 open PRs with CI failures",
                "finding_count": 3,
                "duration_seconds": 12.5,
            },
            {
                "probe_name": "gap_detect",
                "status": "failed",
                "error": "Skill not available",
                "finding_count": 0,
                "duration_seconds": 0.1,
            },
        ],
        "aggregated_findings": [
            {
                "finding_id": "list_prs:ci_failing:omniclaude/PR-701",
                "severity": "high",
                "source_probe": "list_prs",
                "title": "CI failing on PR #701",
                "detail": "Tests failing in omniclaude PR #701",
                "repo": "omniclaude",
                "suggested_action": "Investigate test failures",
            },
            {
                "finding_id": "env_parity:missing_key:OMNIWEB_DB_URL",
                "severity": "medium",
                "source_probe": "env_parity",
                "title": "Missing env var OMNIWEB_DB_URL",
                "repo": None,
                "suggested_action": "Add to ~/.omnibase/.env",
            },
        ],
        "recommended_focus_areas": [
            "omniclaude: CI failures (8 points)",
            "platform: env parity (4 points)",
        ],
        "total_duration_seconds": 120.5,
    }


# ---------------------------------------------------------------------------
# Tests: Minimal construction
# ---------------------------------------------------------------------------


class TestModelDayOpenMinimal:
    """Test minimal valid construction."""

    def test_minimal_construction(
        self, minimal_day_open_data: dict[str, object]
    ) -> None:
        model = ModelDayOpen.model_validate(minimal_day_open_data)
        assert model.schema_version == "1.0.0"
        assert model.date == "2026-03-18"
        assert model.run_id == "abc123def456"
        assert model.yesterday_corrections == []
        assert model.repo_sync_status == []
        assert model.infra_health == []
        assert model.probe_results == []
        assert model.aggregated_findings == []
        assert model.recommended_focus_areas == []
        assert model.total_duration_seconds == 0.0

    def test_missing_required_fields_raises(self) -> None:
        with pytest.raises(ValidationError):
            ModelDayOpen.model_validate({"schema_version": "1.0.0"})

    def test_missing_schema_version_raises(self) -> None:
        with pytest.raises(ValidationError):
            ModelDayOpen.model_validate({"date": "2026-03-18", "run_id": "abc123"})


# ---------------------------------------------------------------------------
# Tests: Full construction
# ---------------------------------------------------------------------------


class TestModelDayOpenFull:
    """Test fully populated construction."""

    def test_full_construction(self, full_day_open_data: dict[str, object]) -> None:
        model = ModelDayOpen.model_validate(full_day_open_data)
        assert len(model.yesterday_corrections) == 2
        assert len(model.repo_sync_status) == 2
        assert len(model.infra_health) == 2
        assert len(model.probe_results) == 2
        assert len(model.aggregated_findings) == 2
        assert len(model.recommended_focus_areas) == 2
        assert model.total_duration_seconds == 120.5

    def test_repo_sync_entry_fields(
        self, full_day_open_data: dict[str, object]
    ) -> None:
        model = ModelDayOpen.model_validate(full_day_open_data)
        entry = model.repo_sync_status[0]
        assert entry.repo == "omniclaude"
        assert entry.branch == "main"
        assert entry.up_to_date is True
        assert entry.head_sha == "a1b2c3d4e5f6"
        assert entry.error is None

    def test_infra_service_with_error(
        self, full_day_open_data: dict[str, object]
    ) -> None:
        model = ModelDayOpen.model_validate(full_day_open_data)
        redpanda = model.infra_health[1]
        assert redpanda.service == "redpanda"
        assert redpanda.running is True
        assert redpanda.port_responding is False
        assert redpanda.error == "Connection refused on port 19092"

    def test_probe_result_completed(
        self, full_day_open_data: dict[str, object]
    ) -> None:
        model = ModelDayOpen.model_validate(full_day_open_data)
        probe = model.probe_results[0]
        assert probe.probe_name == "list_prs"
        assert probe.status == EnumProbeStatus.COMPLETED
        assert probe.finding_count == 3
        assert probe.duration_seconds == 12.5

    def test_probe_result_failed(self, full_day_open_data: dict[str, object]) -> None:
        model = ModelDayOpen.model_validate(full_day_open_data)
        probe = model.probe_results[1]
        assert probe.status == EnumProbeStatus.FAILED
        assert probe.error == "Skill not available"

    def test_finding_fields(self, full_day_open_data: dict[str, object]) -> None:
        model = ModelDayOpen.model_validate(full_day_open_data)
        finding = model.aggregated_findings[0]
        assert finding.finding_id == "list_prs:ci_failing:omniclaude/PR-701"
        assert finding.severity == EnumFindingSeverity.HIGH
        assert finding.source_probe == "list_prs"
        assert finding.repo == "omniclaude"


# ---------------------------------------------------------------------------
# Tests: SemVer validation
# ---------------------------------------------------------------------------


class TestSemVerValidation:
    """Test schema_version SemVer validation."""

    @pytest.mark.parametrize("version", ["1.0.0", "0.1.0", "2.10.33"])
    def test_valid_semver(self, version: str) -> None:
        model = ModelDayOpen.model_validate(
            {"schema_version": version, "date": "2026-03-18", "run_id": "x"}
        )
        assert model.schema_version == version

    @pytest.mark.parametrize(
        "version",
        ["1.0", "v1.0.0", "1.0.0-alpha", "01.0.0", "1.0.0+build", "abc"],
    )
    def test_invalid_semver(self, version: str) -> None:
        with pytest.raises(ValidationError, match="Invalid schema_version"):
            ModelDayOpen.model_validate(
                {"schema_version": version, "date": "2026-03-18", "run_id": "x"}
            )


# ---------------------------------------------------------------------------
# Tests: Date validation
# ---------------------------------------------------------------------------


class TestDateValidation:
    """Test date field validation."""

    @pytest.mark.parametrize("dt", ["2026-03-18", "2000-01-01", "2099-12-31"])
    def test_valid_dates(self, dt: str) -> None:
        model = ModelDayOpen.model_validate(
            {"schema_version": "1.0.0", "date": dt, "run_id": "x"}
        )
        assert model.date == dt

    @pytest.mark.parametrize(
        "dt",
        ["2026/03/18", "03-18-2026", "not-a-date", "2026-13-01", "2026-02-30"],
    )
    def test_invalid_dates(self, dt: str) -> None:
        with pytest.raises(ValidationError):
            ModelDayOpen.model_validate(
                {"schema_version": "1.0.0", "date": dt, "run_id": "x"}
            )


# ---------------------------------------------------------------------------
# Tests: Frozen immutability
# ---------------------------------------------------------------------------


class TestFrozenImmutability:
    """Test that all models are frozen (immutable)."""

    def test_day_open_frozen(self, minimal_day_open_data: dict[str, object]) -> None:
        model = ModelDayOpen.model_validate(minimal_day_open_data)
        with pytest.raises(ValidationError):
            model.date = "2026-01-01"  # type: ignore[misc]

    def test_repo_sync_entry_frozen(self) -> None:
        entry = ModelDayOpenRepoSyncEntry(
            repo="test", branch="main", up_to_date=True, head_sha="abc"
        )
        with pytest.raises(ValidationError):
            entry.repo = "other"  # type: ignore[misc]

    def test_finding_frozen(self) -> None:
        finding = ModelDayOpenFinding(
            finding_id="test:cat:key",
            severity=EnumFindingSeverity.HIGH,
            source_probe="test",
            title="Test finding",
        )
        with pytest.raises(ValidationError):
            finding.severity = EnumFindingSeverity.LOW  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests: Enum values
# ---------------------------------------------------------------------------


class TestEnumValues:
    """Test enum value correctness."""

    def test_finding_severity_values(self) -> None:
        assert str(EnumFindingSeverity.CRITICAL) == "critical"
        assert str(EnumFindingSeverity.HIGH) == "high"
        assert str(EnumFindingSeverity.MEDIUM) == "medium"
        assert str(EnumFindingSeverity.LOW) == "low"
        assert str(EnumFindingSeverity.INFO) == "info"

    def test_probe_status_values(self) -> None:
        assert str(EnumProbeStatus.COMPLETED) == "completed"
        assert str(EnumProbeStatus.FAILED) == "failed"
        assert str(EnumProbeStatus.SKIPPED) == "skipped"
        assert str(EnumProbeStatus.TIMED_OUT) == "timed_out"

    def test_invalid_severity_in_finding(self) -> None:
        with pytest.raises(ValidationError):
            ModelDayOpenFinding(
                finding_id="test:cat:key",
                severity="unknown_severity",
                source_probe="test",
                title="Test",
            )

    def test_invalid_probe_status(self) -> None:
        with pytest.raises(ValidationError):
            ModelDayOpenProbeResult(
                probe_name="test",
                status="invalid_status",
            )


# ---------------------------------------------------------------------------
# Tests: List constraints
# ---------------------------------------------------------------------------


class TestListConstraints:
    """Test list max_length constraints."""

    def test_recommended_focus_areas_max_10(self) -> None:
        data = {
            "schema_version": "1.0.0",
            "date": "2026-03-18",
            "run_id": "x",
            "recommended_focus_areas": [f"area-{i}" for i in range(11)],
        }
        with pytest.raises(ValidationError):
            ModelDayOpen.model_validate(data)

    def test_recommended_focus_areas_at_limit(self) -> None:
        data = {
            "schema_version": "1.0.0",
            "date": "2026-03-18",
            "run_id": "x",
            "recommended_focus_areas": [f"area-{i}" for i in range(10)],
        }
        model = ModelDayOpen.model_validate(data)
        assert len(model.recommended_focus_areas) == 10


# ---------------------------------------------------------------------------
# Tests: Serialization roundtrip
# ---------------------------------------------------------------------------


class TestSerializationRoundtrip:
    """Test model_dump / model_validate roundtrip."""

    def test_roundtrip(self, full_day_open_data: dict[str, object]) -> None:
        model = ModelDayOpen.model_validate(full_day_open_data)
        dumped = model.model_dump()
        restored = ModelDayOpen.model_validate(dumped)
        assert restored == model

    def test_json_roundtrip(self, full_day_open_data: dict[str, object]) -> None:
        model = ModelDayOpen.model_validate(full_day_open_data)
        json_str = model.model_dump_json()
        restored = ModelDayOpen.model_validate_json(json_str)
        assert restored == model

    def test_enum_serialization(self, full_day_open_data: dict[str, object]) -> None:
        model = ModelDayOpen.model_validate(full_day_open_data)
        dumped = model.model_dump()
        # Enums serialize to their string values
        assert dumped["probe_results"][0]["status"] == "completed"
        assert dumped["aggregated_findings"][0]["severity"] == "high"
