"""Day Close Report Model.

Pydantic schema model for daily close reports.
"""

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from onex_change_control.enums.enum_drift_category import EnumDriftCategory
from onex_change_control.enums.enum_invariant_status import EnumInvariantStatus
from onex_change_control.enums.enum_pr_state import EnumPRState

# SemVer pattern for schema_version validation
# Note: This pattern supports basic SemVer (major.minor.patch) only.
# Pre-release versions (e.g., "1.0.0-alpha") and build metadata (e.g., "1.0.0+build")
# are not supported. If full SemVer support is needed, consider using a SemVer library.
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

# ISO date pattern (YYYY-MM-DD) - compiled at module level for performance
# Used for format validation before calendar validation
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Security constraints to prevent DoS attacks
_MAX_STRING_LENGTH = 10000  # Max length for string fields
_MAX_LIST_ITEMS = 1000  # Max items in lists


class ModelDayCloseProcessChange(BaseModel):
    """Process change entry in daily close report."""

    model_config = ConfigDict(frozen=True)

    change: str = Field(
        ...,
        description="What changed in the process today",
        max_length=_MAX_STRING_LENGTH,
    )
    rationale: str = Field(
        ..., description="Why we changed it", max_length=_MAX_STRING_LENGTH
    )
    replaces: str = Field(
        ...,
        description="What it replaces / previous behavior",
        max_length=_MAX_STRING_LENGTH,
    )


class ModelDayClosePlanItem(BaseModel):
    """Plan item in daily close report."""

    model_config = ConfigDict(frozen=True)

    requirement_id: str = Field(
        ..., description="Requirement identifier", max_length=_MAX_STRING_LENGTH
    )
    summary: str = Field(
        ..., description="Summary of the requirement", max_length=_MAX_STRING_LENGTH
    )


class ModelDayClosePR(BaseModel):
    """Pull request entry in daily close report."""

    model_config = ConfigDict(frozen=True)

    pr: int = Field(..., description="PR number", ge=1)
    title: str = Field(..., description="PR title", max_length=_MAX_STRING_LENGTH)
    state: EnumPRState = Field(..., description="PR state")
    notes: str = Field(
        ...,
        description="Why it matters / what it unblocks",
        max_length=_MAX_STRING_LENGTH,
    )


class ModelDayCloseActualRepo(BaseModel):
    """Actual work by repository in daily close report."""

    model_config = ConfigDict(frozen=True)

    repo: str = Field(
        ...,
        description="Repository name (e.g., 'OmniNode-ai/omnibase_core')",
        max_length=_MAX_STRING_LENGTH,
    )
    prs: list[ModelDayClosePR] = Field(
        default_factory=list, description="List of PRs", max_length=_MAX_LIST_ITEMS
    )


class ModelDayCloseDriftDetected(BaseModel):
    """Drift detected entry in daily close report."""

    model_config = ConfigDict(frozen=True)

    drift_id: str = Field(
        ..., description="Unique drift identifier", max_length=_MAX_STRING_LENGTH
    )
    category: EnumDriftCategory = Field(..., description="Drift category")
    evidence: str = Field(
        ...,
        description="What changed / where (PRs, commits, files)",
        max_length=_MAX_STRING_LENGTH,
    )
    impact: str = Field(
        ..., description="Why it matters", max_length=_MAX_STRING_LENGTH
    )
    correction_for_tomorrow: str = Field(
        ...,
        description="Specific fix / decision / ticket",
        max_length=_MAX_STRING_LENGTH,
    )


class ModelDayCloseInvariantsChecked(BaseModel):
    """Invariants checked in daily close report."""

    model_config = ConfigDict(frozen=True)

    reducers_pure: EnumInvariantStatus = Field(
        ..., description="Reducers are pure (no I/O)"
    )
    orchestrators_no_io: EnumInvariantStatus = Field(
        ..., description="Orchestrators perform no I/O"
    )
    effects_do_io_only: EnumInvariantStatus = Field(
        ..., description="Effects perform I/O only"
    )
    real_infra_proof_progressing: EnumInvariantStatus = Field(
        ..., description="Real infrastructure proof is progressing"
    )


class ModelDayCloseRisk(BaseModel):
    """Risk entry in daily close report."""

    model_config = ConfigDict(frozen=True)

    risk: str = Field(
        ..., description="Short risk description", max_length=_MAX_STRING_LENGTH
    )
    mitigation: str = Field(
        ..., description="Short mitigation description", max_length=_MAX_STRING_LENGTH
    )


class ModelDayClose(BaseModel):
    """Daily close report model.

    Represents a daily reconciliation of plan vs actual work across repos.

    Immutability:
        This model is frozen (immutable) after creation to ensure:
        1. Historical drift reports cannot be modified after creation
        2. Thread-safe access from multiple readers
        3. Safe use as dictionary keys or cache entries
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(
        ..., description="Schema version (SemVer format, e.g., '1.0.0')", max_length=20
    )
    date: str = Field(..., description="ISO date (YYYY-MM-DD)")
    process_changes_today: list[ModelDayCloseProcessChange] = Field(
        default_factory=list,
        description="Process changes made today",
        max_length=_MAX_LIST_ITEMS,
    )
    plan: list[ModelDayClosePlanItem] = Field(
        default_factory=list,
        description="Planned requirements",
        max_length=_MAX_LIST_ITEMS,
    )
    actual_by_repo: list[ModelDayCloseActualRepo] = Field(
        default_factory=list,
        description="Actual work by repository",
        max_length=_MAX_LIST_ITEMS,
    )
    drift_detected: list[ModelDayCloseDriftDetected] = Field(
        default_factory=list,
        description="Drift detected entries",
        max_length=_MAX_LIST_ITEMS,
    )
    invariants_checked: ModelDayCloseInvariantsChecked = Field(
        ..., description="Invariants checked status"
    )
    corrections_for_tomorrow: list[str] = Field(
        default_factory=list,
        description="Actionable corrections for tomorrow",
        max_length=_MAX_LIST_ITEMS,
    )
    risks: list[ModelDayCloseRisk] = Field(
        default_factory=list,
        description="Risk entries",
        max_length=_MAX_LIST_ITEMS,
    )

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        """Validate schema_version is SemVer format."""
        if not _SEMVER_PATTERN.match(v):
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
