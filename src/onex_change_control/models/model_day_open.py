# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Day Open Report Model.

Pydantic schema model for daily morning investigation reports.
"""

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from onex_change_control.enums.enum_finding_severity import EnumFindingSeverity
from onex_change_control.enums.enum_probe_status import EnumProbeStatus
from onex_change_control.validation.patterns import SEMVER_PATTERN

# ISO date pattern (YYYY-MM-DD) - compiled at module level for performance
# Used for format validation before calendar validation
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Security constraints to prevent DoS attacks
_MAX_STRING_LENGTH = 10000  # Max length for string fields
_MAX_LIST_ITEMS = 1000  # Max items in lists


class ModelDayOpenRepoSyncEntry(BaseModel):
    """Repository sync status entry from Phase 1 pull-all."""

    model_config = ConfigDict(frozen=True)

    repo: str = Field(
        ...,
        description="Repository name (e.g., 'omniclaude')",
        max_length=_MAX_STRING_LENGTH,
    )
    branch: str = Field(
        ...,
        description="Branch that was synced (typically 'main')",
        max_length=_MAX_STRING_LENGTH,
    )
    up_to_date: bool = Field(
        ...,
        description="Whether the repo was already up to date",
    )
    head_sha: str = Field(
        ...,
        description="HEAD commit SHA after sync",
        max_length=_MAX_STRING_LENGTH,
    )
    error: str | None = Field(
        default=None,
        description="Error message if sync failed",
        max_length=_MAX_STRING_LENGTH,
    )


class ModelDayOpenInfraService(BaseModel):
    """Infrastructure service health entry from Phase 1."""

    model_config = ConfigDict(frozen=True)

    service: str = Field(
        ...,
        description="Service name (e.g., 'postgres', 'redpanda', 'valkey')",
        max_length=_MAX_STRING_LENGTH,
    )
    running: bool = Field(
        ...,
        description="Whether the Docker container is running",
    )
    port_responding: bool = Field(
        ...,
        description="Whether the service port is accepting connections",
    )
    error: str | None = Field(
        default=None,
        description="Error message if health check failed",
        max_length=_MAX_STRING_LENGTH,
    )


class ModelDayOpenProbeResult(BaseModel):
    """Result from a single Phase 2 investigation probe."""

    model_config = ConfigDict(frozen=True)

    probe_name: str = Field(
        ...,
        description="Probe identifier (e.g., 'list_prs', 'gap_detect')",
        max_length=_MAX_STRING_LENGTH,
    )
    status: EnumProbeStatus = Field(
        ...,
        description="Execution status of the probe",
    )
    artifact_path: str | None = Field(
        default=None,
        description="Path to the probe's JSON artifact file",
        max_length=_MAX_STRING_LENGTH,
    )
    summary: str | None = Field(
        default=None,
        description="Brief summary of probe results",
        max_length=_MAX_STRING_LENGTH,
    )
    finding_count: int = Field(
        default=0,
        description="Number of findings from this probe",
        ge=0,
    )
    error: str | None = Field(
        default=None,
        description="Error message if probe failed",
        max_length=_MAX_STRING_LENGTH,
    )
    duration_seconds: float = Field(
        default=0.0,
        description="Wall-clock duration of probe execution",
        ge=0.0,
    )


class ModelDayOpenFinding(BaseModel):
    """Individual finding from morning investigation.

    The finding_id must be a stable, probe-local fingerprint following
    the format: {probe_name}:{category}:{deterministic_key}
    """

    model_config = ConfigDict(frozen=True)

    finding_id: str = Field(
        ...,
        description=(
            "Stable fingerprint: {probe_name}:{category}:{deterministic_key}. "
            "Must identify the underlying resource, not the wording."
        ),
        max_length=_MAX_STRING_LENGTH,
    )
    severity: EnumFindingSeverity = Field(
        ...,
        description="Severity level of the finding",
    )
    source_probe: str = Field(
        ...,
        description="Name of the probe that generated this finding",
        max_length=_MAX_STRING_LENGTH,
    )
    title: str = Field(
        ...,
        description="Short description of the finding",
        max_length=_MAX_STRING_LENGTH,
    )
    detail: str = Field(
        default="",
        description="Detailed explanation of the finding",
        max_length=_MAX_STRING_LENGTH,
    )
    repo: str | None = Field(
        default=None,
        description="Affected repository, or None for platform-wide issues",
        max_length=_MAX_STRING_LENGTH,
    )
    suggested_action: str | None = Field(
        default=None,
        description="Recommended action to address the finding",
        max_length=_MAX_STRING_LENGTH,
    )


class ModelDayOpen(BaseModel):
    """Daily morning investigation report model.

    Represents the aggregated output of the begin-day pipeline:
    Phase 0 (context load), Phase 1 (sync + preconditions),
    Phase 2 (parallel probes), and Phase 3 (aggregation).

    Schema Version:
        The schema_version field uses basic SemVer format (major.minor.patch) only.
        Pre-release versions (e.g., "1.0.0-alpha") and build metadata
        (e.g., "1.0.0+build") are not supported. Leading zeros are rejected
        per SemVer specification.

    Immutability:
        This model is frozen (immutable) after creation to ensure:
        1. Historical morning reports cannot be modified after creation
        2. Thread-safe access from multiple readers
        3. Safe use as dictionary keys or cache entries
    """

    model_config = ConfigDict(frozen=True)

    # string-version-ok: YAML/JSON wire; format checked by field_validator
    schema_version: str = Field(
        ...,
        description="Schema version (SemVer format, e.g., '1.0.0')",
        max_length=20,
    )
    date: str = Field(..., description="ISO date (YYYY-MM-DD)")
    run_id: str = Field(
        ...,
        description="Unique identifier for this begin-day run",
        max_length=_MAX_STRING_LENGTH,
    )
    yesterday_corrections: list[str] = Field(
        default_factory=list,
        description="Carry-forward corrections from yesterday's close-day",
        max_length=_MAX_LIST_ITEMS,
    )
    repo_sync_status: list[ModelDayOpenRepoSyncEntry] = Field(
        default_factory=list,
        description="Repository sync results from Phase 1",
        max_length=_MAX_LIST_ITEMS,
    )
    infra_health: list[ModelDayOpenInfraService] = Field(
        default_factory=list,
        description="Infrastructure health check results",
        max_length=_MAX_LIST_ITEMS,
    )
    probe_results: list[ModelDayOpenProbeResult] = Field(
        default_factory=list,
        description="Results from Phase 2 investigation probes",
        max_length=_MAX_LIST_ITEMS,
    )
    aggregated_findings: list[ModelDayOpenFinding] = Field(
        default_factory=list,
        description="Severity-sorted, deduplicated findings from all probes",
        max_length=_MAX_LIST_ITEMS,
    )
    recommended_focus_areas: list[str] = Field(
        default_factory=list,
        description="Top focus areas by weighted severity score (max 10)",
        max_length=10,
    )
    total_duration_seconds: float = Field(
        default=0.0,
        description="Total wall-clock duration of the begin-day pipeline",
        ge=0.0,
    )

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        """Validate schema_version is SemVer format.

        Note: Only basic SemVer (major.minor.patch) is supported.
        Pre-release versions and build metadata are not supported.
        Leading zeros are rejected per SemVer specification.
        """
        if not SEMVER_PATTERN.match(v):
            msg = f"Invalid schema_version format: {v}. Expected SemVer (e.g., '1.0.0')"
            raise ValueError(msg)
        return v

    @field_validator("date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        """Validate date is ISO format (YYYY-MM-DD) and calendar-valid.

        Uses datetime.date.fromisoformat() to ensure the date is both
        correctly formatted and represents a valid calendar date.
        """
        # First check format for better error messages
        if not _DATE_PATTERN.match(v):
            msg = f"Invalid date format: {v}. Expected ISO format (YYYY-MM-DD)"
            raise ValueError(msg)

        # Then validate calendar validity
        try:
            date.fromisoformat(v)
        except ValueError as e:
            msg = f"Invalid calendar date: {v}. {e!s}"
            raise ValueError(msg) from e

        return v
