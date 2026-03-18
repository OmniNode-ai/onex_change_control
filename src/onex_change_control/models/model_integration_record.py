# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Integration Record Model.

Pydantic schema models for /integration-sweep probe results.
ModelIntegrationProbeResult represents a single surface check;
ModelIntegrationRecord aggregates all probe results into a sweep report.
"""

import re
from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from onex_change_control.enums.enum_integration_surface import EnumIntegrationSurface
from onex_change_control.enums.enum_invariant_status import EnumInvariantStatus
from onex_change_control.enums.enum_probe_reason import EnumProbeReason
from onex_change_control.validation.patterns import SEMVER_PATTERN

# ISO date pattern (YYYY-MM-DD) - compiled at module level for performance
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Security constraints to prevent DoS attacks
_MAX_STRING_LENGTH = 10000  # Max length for string fields
_MAX_LIST_ITEMS = 1000  # Max items in lists


class ModelIntegrationProbeResult(BaseModel):
    """Result for a single integration surface probe.

    Represents the outcome of probing one EnumIntegrationSurface during
    the /integration-sweep workflow. When status is UNKNOWN, a reason
    code (EnumProbeReason) is expected to explain why a definitive
    PASS/FAIL result could not be obtained.

    Immutability:
        This model is frozen (immutable) after creation.
    """

    model_config = ConfigDict(frozen=True)

    surface: EnumIntegrationSurface = Field(
        ...,
        description="The integration surface being probed",
    )
    status: EnumInvariantStatus = Field(
        ...,
        description="Probe result: PASS, FAIL, or UNKNOWN",
    )
    reason: EnumProbeReason | None = Field(
        default=None,
        description=(
            "Reason code when status is UNKNOWN or the probe was skipped. "
            "Optional for PASS/FAIL results."
        ),
    )
    detail: Annotated[str | None, Field(max_length=_MAX_STRING_LENGTH)] = Field(
        default=None,
        description="Human-readable detail about the probe outcome",
    )
    checked_at: str | None = Field(
        default=None,
        description=(
            "ISO date (YYYY-MM-DD) when the probe was run. "
            "None if probe did not execute."
        ),
        max_length=20,
    )

    @field_validator("checked_at")
    @classmethod
    def validate_checked_at(cls, v: str | None) -> str | None:
        """Validate checked_at is ISO format (YYYY-MM-DD) and calendar-valid."""
        if v is None:
            return v
        if not _DATE_PATTERN.match(v):
            msg = f"Invalid date format: {v}. Expected ISO format (YYYY-MM-DD)"
            raise ValueError(msg)
        try:
            date.fromisoformat(v)
        except ValueError as e:
            msg = f"Invalid calendar date: {v}. {e!s}"
            raise ValueError(msg) from e
        return v


class ModelIntegrationRecord(BaseModel):
    """Aggregated /integration-sweep artifact.

    Collects probe results for every integration surface examined during
    a sweep run. The overall_status field is automatically derived by a
    model_validator:
    - FAIL  if any probe result is FAIL
    - PASS  if all probe results are PASS (and the list is non-empty)
    - UNKNOWN otherwise (empty list, or mixed PASS/UNKNOWN without any FAIL)

    Schema Version:
        The schema_version field uses basic SemVer format (major.minor.patch) only.
        Pre-release versions and build metadata are not supported.
        Leading zeros are rejected per SemVer specification.

    Immutability:
        This model is frozen (immutable) after creation. overall_status is
        set via object.__setattr__ inside the model_validator to work around
        the frozen constraint at construction time.

    Storage:
        Written to: onex_change_control/drift/integration/YYYY-MM-DD.yaml
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(
        ...,
        description="Schema version (SemVer format, e.g., '1.0.0')",
        max_length=20,
    )
    date: str = Field(
        ...,
        description="ISO date (YYYY-MM-DD) of the sweep run",
    )
    run_id: str = Field(
        ...,
        description="Unique identifier for this sweep run",
        max_length=_MAX_STRING_LENGTH,
    )
    tickets: list[ModelIntegrationProbeResult] = Field(
        default_factory=list,
        description="Per-surface probe results collected during this sweep",
        max_length=_MAX_LIST_ITEMS,
    )
    overall_status: EnumInvariantStatus = Field(
        default=EnumInvariantStatus.UNKNOWN,
        description=(
            "Derived sweep status. "
            "FAIL if any ticket is FAIL; PASS if all are PASS; UNKNOWN otherwise. "
            "Computed by model_validator — do not set manually."
        ),
    )

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        """Validate schema_version is SemVer format."""
        if not SEMVER_PATTERN.match(v):
            msg = f"Invalid schema_version format: {v}. Expected SemVer (e.g., '1.0.0')"
            raise ValueError(msg)
        return v

    @field_validator("date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        """Validate date is ISO format (YYYY-MM-DD) and calendar-valid."""
        if not _DATE_PATTERN.match(v):
            msg = f"Invalid date format: {v}. Expected ISO format (YYYY-MM-DD)"
            raise ValueError(msg)
        try:
            date.fromisoformat(v)
        except ValueError as e:
            msg = f"Invalid calendar date: {v}. {e!s}"
            raise ValueError(msg) from e
        return v

    @model_validator(mode="after")
    def derive_overall_status(self) -> "ModelIntegrationRecord":
        """Derive overall_status from ticket probe results.

        Rules:
        - FAIL  if any ticket has status FAIL
        - PASS  if all tickets have status PASS (non-empty list)
        - UNKNOWN otherwise (empty list or mixed PASS/UNKNOWN)
        """
        statuses = [t.status for t in self.tickets]
        if not statuses:
            derived = EnumInvariantStatus.UNKNOWN
        elif any(s == EnumInvariantStatus.FAIL for s in statuses):
            derived = EnumInvariantStatus.FAIL
        elif all(s == EnumInvariantStatus.PASS for s in statuses):
            derived = EnumInvariantStatus.PASS
        else:
            derived = EnumInvariantStatus.UNKNOWN
        object.__setattr__(self, "overall_status", derived)
        return self
