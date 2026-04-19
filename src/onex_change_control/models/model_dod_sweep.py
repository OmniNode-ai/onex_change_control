# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""DoD Sweep Result Models.

Pydantic schemas for /dod-sweep compliance results.
ModelDodSweepCheckResult represents a single check outcome;
ModelDodSweepTicketResult aggregates all 6 checks for one ticket;
ModelDodSweepResult aggregates all ticket results into a sweep report.

Exemption semantics:
    Exempted tickets are non-blocking for aggregate gating, but they are NOT
    equivalent to evidence-backed PASS. Per-ticket overall_status for exempted
    tickets remains UNKNOWN to preserve the distinction. Aggregate overall_status
    treats exempted as non-failing for gate purposes only.
"""

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from onex_change_control.enums.enum_dod_sweep_check import EnumDodSweepCheck
from onex_change_control.enums.enum_invariant_status import EnumInvariantStatus
from onex_change_control.validation.patterns import SEMVER_PATTERN

# ISO date pattern (YYYY-MM-DD) - compiled at module level for performance
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Security constraints to prevent DoS attacks
_MAX_STRING_LENGTH = 10000  # Max length for string fields
_MAX_LIST_ITEMS = 1000  # Max items in lists


class ModelDodSweepCheckResult(BaseModel):
    """Result for a single DoD compliance check.

    Immutability:
        This model is frozen (immutable) after creation.
    """

    model_config = ConfigDict(frozen=True)

    check: EnumDodSweepCheck = Field(
        ..., description="Which of the 6 DoD checks this result is for"
    )
    status: EnumInvariantStatus = Field(
        ..., description="Check result: PASS, FAIL, or UNKNOWN"
    )
    unknown_subtype: Literal[
        "exempt", "linkage_inconclusive", "mixed_evidence", None
    ] = Field(
        default=None,
        description=(
            "Structured subtype when status is UNKNOWN. Provides stable key "
            "for downstream consumers beyond free-text detail."
        ),
    )
    detail: Annotated[str | None, Field(max_length=_MAX_STRING_LENGTH)] = Field(
        default=None, description="Human-readable detail about the check outcome"
    )


class ModelDodSweepTicketResult(BaseModel):
    """DoD compliance result for a single ticket.

    Exempted tickets retain overall_status=UNKNOWN (not PASS) to distinguish
    'not checked' from 'checked and passed'. Gate logic should treat exempted
    as non-blocking without conflating it with evidence-backed success.

    Immutability:
        This model is frozen (immutable) after creation. overall_status is
        set via object.__setattr__ inside the model_validator to work around
        the frozen constraint at construction time.
    """

    model_config = ConfigDict(frozen=True)

    ticket_id: str = Field(..., description="Linear ticket ID (e.g., OMN-1234)")
    title: str = Field(..., description="Ticket title from Linear")
    unknown_subtype: Literal[
        "exempt", "mixed_evidence", "no_evidence_backed_passes", None
    ] = Field(
        default=None,
        description=(
            "Structured subtype when overall_status is UNKNOWN. "
            "Gives downstream consumers a stable key beyond free-text."
        ),
    )
    completed_at: str | None = Field(
        default=None, description="ISO date when ticket was completed"
    )
    checks: list[ModelDodSweepCheckResult] = Field(
        ..., description="Results for all 6 DoD checks", max_length=10
    )
    overall_status: EnumInvariantStatus = Field(
        default=EnumInvariantStatus.UNKNOWN,
        description=(
            "Derived: FAIL if any check FAIL, PASS if all PASS. "
            "Exempted tickets remain UNKNOWN (not PASS)."
        ),
    )
    exempted: bool = Field(default=False, description="Whether this ticket is exempt")
    exemption_reason: str | None = Field(
        default=None, description="Why this ticket is exempt"
    )
    follow_up_ticket_id: str | None = Field(
        default=None, description="Created follow-up ticket ID, if any"
    )

    @model_validator(mode="after")
    def derive_overall_status(self) -> "ModelDodSweepTicketResult":
        """Derive overall_status from check results.

        Exempted tickets stay UNKNOWN -- they are non-blocking but not PASS.
        """
        # Exempted tickets stay UNKNOWN -- they are non-blocking but not PASS
        if self.exempted:
            object.__setattr__(self, "overall_status", EnumInvariantStatus.UNKNOWN)
            return self
        statuses = [c.status for c in self.checks]
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


class ModelDodSweepResult(BaseModel):
    """Aggregate DoD sweep report.

    Gate semantics: overall_status is PASS if all non-exempted tickets PASS
    and there are no FAIL results. Exempted tickets are non-blocking but are
    counted separately from true passes to maintain reporting accuracy.

    Schema Version:
        The schema_version field uses basic SemVer format (major.minor.patch) only.
        Pre-release versions and build metadata are not supported.
        Leading zeros are rejected per SemVer specification.

    Immutability:
        This model is frozen (immutable) after creation. Aggregate fields are
        set via object.__setattr__ inside the model_validator to work around
        the frozen constraint at construction time.

    Storage:
        Written to: onex_change_control/drift/dod_sweep/YYYY-MM-DD.yaml
    """

    model_config = ConfigDict(frozen=True)

    # string-version-ok: YAML/JSON wire; format checked by field_validator
    schema_version: str = Field(
        ..., description="Schema version (SemVer)", max_length=20
    )
    date: str = Field(..., description="ISO date of sweep run")
    run_id: str = Field(
        ..., description="Unique sweep run identifier", max_length=_MAX_STRING_LENGTH
    )
    mode: Literal["batch", "targeted"] = Field(
        ..., description="Sweep mode: batch (retroactive) or targeted (pre-close gate)"
    )
    lookback_days: int | None = Field(
        default=None, description="Look-back window for batch mode"
    )
    target_id: str | None = Field(
        default=None, description="Epic or ticket ID for targeted mode"
    )
    tickets: list[ModelDodSweepTicketResult] = Field(
        default_factory=list,
        description="Per-ticket compliance results",
        max_length=_MAX_LIST_ITEMS,
    )
    overall_status: EnumInvariantStatus = Field(
        default=EnumInvariantStatus.UNKNOWN,
        description="Derived: FAIL if any ticket FAIL, PASS if all non-exempted pass",
    )
    total_tickets: int = Field(default=0, description="Total tickets swept")
    passed: int = Field(
        default=0, description="Tickets that passed all checks (evidence-backed)"
    )
    failed: int = Field(default=0, description="Tickets with at least one failure")
    exempted: int = Field(
        default=0, description="Exempt tickets (non-blocking, not evidence-backed)"
    )

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        """Validate schema_version is SemVer format."""
        if not SEMVER_PATTERN.match(v):
            msg = f"Invalid schema_version: {v}. Expected SemVer (e.g., '1.0.0')"
            raise ValueError(msg)
        return v

    @field_validator("date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        """Validate date is ISO format (YYYY-MM-DD) and calendar-valid."""
        if not _DATE_PATTERN.match(v):
            msg = f"Invalid date format: {v}. Expected YYYY-MM-DD"
            raise ValueError(msg)
        date.fromisoformat(v)
        return v

    @model_validator(mode="after")
    def derive_aggregates(self) -> "ModelDodSweepResult":
        """Derive aggregate counts and overall_status from ticket results.

        Gate logic: FAIL if any failure; PASS if all non-exempted passed.
        All-exempted epics produce UNKNOWN (rollout accommodation).
        """
        tickets = self.tickets
        # Evidence-backed passes only (not exempted)
        passed = sum(
            1
            for t in tickets
            if t.overall_status == EnumInvariantStatus.PASS and not t.exempted
        )
        failed = sum(1 for t in tickets if t.overall_status == EnumInvariantStatus.FAIL)
        exempted_count = sum(1 for t in tickets if t.exempted)

        # Gate logic: FAIL if any failure; PASS if all non-exempted passed
        if failed > 0:
            derived = EnumInvariantStatus.FAIL
        elif passed > 0 and passed + exempted_count == len(tickets):
            derived = EnumInvariantStatus.PASS
        elif len(tickets) > 0 and exempted_count == len(tickets):
            # All tickets exempted -- technically non-blocking but not proven
            derived = EnumInvariantStatus.UNKNOWN
        else:
            derived = EnumInvariantStatus.UNKNOWN

        object.__setattr__(self, "total_tickets", len(tickets))
        object.__setattr__(self, "passed", passed)
        object.__setattr__(self, "failed", failed)
        object.__setattr__(self, "exempted", exempted_count)
        object.__setattr__(self, "overall_status", derived)
        return self
