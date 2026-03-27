# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Eval Report Model.

Full A/B comparison report across all tasks in an eval suite.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from onex_change_control.models.model_eval_run import ModelEvalRunPair


class ModelEvalSummary(BaseModel):
    """Summary statistics for an eval report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_tasks: int = Field(
        ...,
        description="Total number of tasks compared",
        ge=0,
    )
    onex_better_count: int = Field(
        ...,
        description="Tasks where ONEX improved outcomes",
        ge=0,
    )
    onex_worse_count: int = Field(
        ...,
        description="Tasks where ONEX degraded outcomes",
        ge=0,
    )
    neutral_count: int = Field(
        ...,
        description="Tasks with no significant difference",
        ge=0,
    )
    avg_latency_delta_ms: float = Field(
        ...,
        description="Average latency delta (negative = ONEX faster)",
    )
    avg_token_delta: float = Field(
        ...,
        description="Average token count delta (negative = ONEX uses fewer tokens)",
    )
    avg_success_rate_on: float = Field(
        ...,
        description="Average success rate with ONEX ON",
        ge=0.0,
        le=1.0,
    )
    avg_success_rate_off: float = Field(
        ...,
        description="Average success rate with ONEX OFF",
        ge=0.0,
        le=1.0,
    )
    pattern_hit_rate_on: float = Field(
        ...,
        description="How often patterns were used when ONEX is on",
        ge=0.0,
        le=1.0,
    )


class ModelEvalReport(BaseModel):
    """Full A/B comparison report across all tasks.

    Contains individual pair comparisons and aggregated summary statistics.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(
        ...,
        description="Unique report identifier",
        max_length=200,
    )
    suite_id: str = Field(
        ...,
        description="Which suite was evaluated",
        max_length=200,
    )
    suite_version: str = Field(
        default="",
        description="Version of the suite used for this report",
        max_length=50,
    )
    generated_at: datetime = Field(
        ...,
        description="When this report was generated",
    )
    pairs: list[ModelEvalRunPair] = Field(
        ...,
        description="Individual task comparisons",
    )
    summary: ModelEvalSummary = Field(
        ...,
        description="Aggregated summary statistics",
    )
