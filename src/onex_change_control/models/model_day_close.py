"""Day Close Report Model.

Pydantic schema model for daily close reports.
"""

import re

from pydantic import BaseModel, Field, field_validator

from onex_change_control.enums.enum_drift_category import EnumDriftCategory
from onex_change_control.enums.enum_invariant_status import EnumInvariantStatus
from onex_change_control.enums.enum_pr_state import EnumPRState

# SemVer pattern for schema_version validation
# Note: This pattern supports basic SemVer (major.minor.patch) only.
# Pre-release versions (e.g., "1.0.0-alpha") and build metadata (e.g., "1.0.0+build")
# are not supported. If full SemVer support is needed, consider using a SemVer library.
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

# ISO date pattern (YYYY-MM-DD) - compiled at module level for performance
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ModelDayCloseProcessChange(BaseModel):
    """Process change entry in daily close report."""

    change: str = Field(..., description="What changed in the process today")
    rationale: str = Field(..., description="Why we changed it")
    replaces: str = Field(..., description="What it replaces / previous behavior")


class ModelDayClosePlanItem(BaseModel):
    """Plan item in daily close report."""

    requirement_id: str = Field(..., description="Requirement identifier")
    summary: str = Field(..., description="Summary of the requirement")


class ModelDayClosePR(BaseModel):
    """Pull request entry in daily close report."""

    pr: int = Field(..., description="PR number", ge=1)
    title: str = Field(..., description="PR title")
    state: EnumPRState = Field(..., description="PR state")
    notes: str = Field(..., description="Why it matters / what it unblocks")


class ModelDayCloseActualRepo(BaseModel):
    """Actual work by repository in daily close report."""

    repo: str = Field(
        ..., description="Repository name (e.g., 'OmniNode-ai/omnibase_core')"
    )
    prs: list[ModelDayClosePR] = Field(default_factory=list, description="List of PRs")


class ModelDayCloseDriftDetected(BaseModel):
    """Drift detected entry in daily close report."""

    drift_id: str = Field(..., description="Unique drift identifier")
    category: EnumDriftCategory = Field(..., description="Drift category")
    evidence: str = Field(..., description="What changed / where (PRs, commits, files)")
    impact: str = Field(..., description="Why it matters")
    correction_for_tomorrow: str = Field(
        ..., description="Specific fix / decision / ticket"
    )


class ModelDayCloseInvariantsChecked(BaseModel):
    """Invariants checked in daily close report."""

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

    risk: str = Field(..., description="Short risk description")
    mitigation: str = Field(..., description="Short mitigation description")


class ModelDayClose(BaseModel):
    """Daily close report model.

    Represents a daily reconciliation of plan vs actual work across repos.
    """

    schema_version: str = Field(
        ..., description="Schema version (SemVer format, e.g., '1.0.0')"
    )
    date: str = Field(..., description="ISO date (YYYY-MM-DD)")
    process_changes_today: list[ModelDayCloseProcessChange] = Field(
        default_factory=list,
        description="Process changes made today",
    )
    plan: list[ModelDayClosePlanItem] = Field(
        default_factory=list,
        description="Planned requirements",
    )
    actual_by_repo: list[ModelDayCloseActualRepo] = Field(
        default_factory=list,
        description="Actual work by repository",
    )
    drift_detected: list[ModelDayCloseDriftDetected] = Field(
        default_factory=list,
        description="Drift detected entries",
    )
    invariants_checked: ModelDayCloseInvariantsChecked = Field(
        ..., description="Invariants checked status"
    )
    corrections_for_tomorrow: list[str] = Field(
        default_factory=list,
        description="Actionable corrections for tomorrow",
    )
    risks: list[ModelDayCloseRisk] = Field(
        default_factory=list,
        description="Risk entries",
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
        """Validate date is ISO format (YYYY-MM-DD).

        Note: This validates format only, not calendar validity.
        Invalid dates like "2025-02-30" will pass format validation.
        """
        if not _DATE_PATTERN.match(v):
            msg = f"Invalid date format: {v}. Expected ISO format (YYYY-MM-DD)"
            raise ValueError(msg)
        return v
