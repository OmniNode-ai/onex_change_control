# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Eval Run and Run Pair Models.

Models for individual eval runs and paired A/B comparisons.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from onex_change_control.enums.enum_eval_metric_type import EnumEvalMetricType
from onex_change_control.enums.enum_eval_mode import EnumEvalMode
from onex_change_control.enums.enum_eval_verdict import EnumEvalVerdict

_MAX_STRING_LENGTH = 10000


class ModelEvalMetric(BaseModel):
    """A single metric observation from an eval run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_type: EnumEvalMetricType = Field(
        ...,
        description="Type of metric",
    )
    value: float = Field(
        ...,
        description="Measured value",
    )
    unit: str = Field(
        ...,
        description="Unit of measurement (e.g., ms, count, ratio)",
        max_length=50,
    )


class ModelEvalRun(BaseModel):
    """A single eval run: one task executed in one mode.

    Captures all metrics and metadata for a single execution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(
        ...,
        description="Unique run identifier (UUID)",
        max_length=200,
    )
    task_id: str = Field(
        ...,
        description="References ModelEvalTask.task_id",
        max_length=200,
    )
    mode: EnumEvalMode = Field(
        ...,
        description="ONEX_ON or ONEX_OFF",
    )
    started_at: datetime = Field(
        ...,
        description="When the run started",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="When the run completed (None if still running or crashed)",
    )
    success: bool = Field(
        ...,
        description="Whether all success criteria were met",
    )
    metrics: list[ModelEvalMetric] = Field(
        default_factory=list,
        description="Collected metrics",
    )
    error_message: str | None = Field(
        default=None,
        description="Error message if the run failed",
        max_length=_MAX_STRING_LENGTH,
    )
    git_sha: str = Field(
        ...,
        description="Commit hash at time of run",
        max_length=64,
    )
    env_snapshot: dict[str, str] = Field(
        default_factory=dict,
        description="Relevant ENABLE_* flags at time of run",
    )

    def get_metric(self, metric_type: EnumEvalMetricType) -> float | None:
        """Get the value of a specific metric type, or None if not collected."""
        for m in self.metrics:
            if m.metric_type == metric_type:
                return m.value
        return None


class ModelEvalRunPair(BaseModel):
    """A paired A/B comparison: same task, ONEX ON vs OFF.

    Delta metrics are computed as (on_value - off_value).
    For cost metrics (latency, tokens, errors), negative = ONEX better.
    For benefit metrics (success_rate, pattern_hit_rate), positive = ONEX better.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(
        ...,
        description="The task that was compared",
        max_length=200,
    )
    onex_on_run: ModelEvalRun = Field(
        ...,
        description="Run with ONEX features enabled",
    )
    onex_off_run: ModelEvalRun = Field(
        ...,
        description="Run with ONEX features disabled",
    )
    delta_metrics: dict[str, float] = Field(
        default_factory=dict,
        description="Per-metric delta (on - off). Negative = ONEX saves.",
    )
    verdict: EnumEvalVerdict = Field(
        ...,
        description="Overall verdict for this pair",
    )
