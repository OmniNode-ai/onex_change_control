# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Compliance Sweep Report Model.

Aggregated sweep report across all handlers in one or more repos.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from onex_change_control.models.model_handler_compliance_result import (
    ModelHandlerComplianceResult,
)


class ModelRepoComplianceBreakdown(BaseModel):
    """Per-repo breakdown of compliance results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str = Field(..., description="Repository name")
    total_handlers: int = Field(..., description="Total handlers scanned", ge=0)
    compliant: int = Field(..., description="Compliant handler count", ge=0)
    imperative: int = Field(..., description="Imperative handler count", ge=0)
    hybrid: int = Field(..., description="Hybrid handler count", ge=0)
    top_violations: list[str] = Field(
        default_factory=list,
        description="Most common violation types in this repo",
    )


class ModelComplianceSweepReport(BaseModel):
    """Aggregated compliance sweep report.

    Contains counts, ratios, per-repo breakdown, and full audit details
    for all handlers scanned across one or more repositories.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: datetime = Field(
        ...,
        description="When the sweep was performed",
    )
    total_handlers: int = Field(..., description="Total handlers scanned", ge=0)
    compliant_count: int = Field(..., description="Compliant handler count", ge=0)
    imperative_count: int = Field(..., description="Imperative handler count", ge=0)
    hybrid_count: int = Field(..., description="Hybrid handler count", ge=0)
    allowlisted_count: int = Field(..., description="Allowlisted handler count", ge=0)
    missing_contract_count: int = Field(
        ..., description="Missing contract handler count", ge=0
    )
    compliant_pct: float = Field(
        ...,
        description="Percentage of compliant handlers (0-100)",
    )
    violation_histogram: dict[str, int] = Field(
        default_factory=dict,
        description="Count per EnumComplianceViolation value",
    )
    per_repo: dict[str, ModelRepoComplianceBreakdown] = Field(
        default_factory=dict,
        description="Breakdown by repository",
    )
    results: list[ModelHandlerComplianceResult] = Field(
        default_factory=list,
        description="Full audit details",
    )
    new_violations_since_last: list[str] = Field(
        default_factory=list,
        description="Handlers that gained violations since last sweep",
    )
