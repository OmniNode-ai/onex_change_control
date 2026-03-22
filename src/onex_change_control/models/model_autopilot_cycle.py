# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Autopilot Cycle Record Model.

Tracks per-step completion status for each autopilot close-out cycle.
Enables detection of hollow cycles where steps were skipped.
"""

from enum import Enum, unique

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


@unique
class EnumAutopilotStepStatus(str, Enum):
    """Status of a single autopilot pipeline step."""

    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    NOT_RUN = "not_run"

    def __str__(self) -> str:
        return self.value


@unique
class EnumAutopilotCycleStatus(str, Enum):
    """Overall status of an autopilot close-out cycle.

    Closed status set — no free-form string states.
    """

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    HALTED = "halted"
    CIRCUIT_BREAKER = "circuit_breaker"

    def __str__(self) -> str:
        return self.value


class ModelAutopilotStepResult(BaseModel):
    """Result for a single autopilot pipeline step.

    Skip-with-reason doctrine: A step may count as intentionally skipped
    only when a non-empty ``reason`` is recorded and the skip is valid for
    the current mode. Otherwise it should remain ``not_run`` or ``failed``.
    """

    model_config = ConfigDict(frozen=True)

    step: str = Field(
        ..., description="Step name (e.g., merge-sweep, integration-sweep)"
    )
    status: EnumAutopilotStepStatus = Field(..., description="Step completion status")
    reason: str | None = Field(
        default=None, description="Why step was skipped or failed"
    )
    duration_seconds: float = Field(
        default=0.0, description="Wall-clock execution time"
    )

    @field_validator("reason")
    @classmethod
    def skipped_requires_reason(cls, v: str | None, info: ValidationInfo) -> str | None:
        """Skipped steps must have a non-empty reason."""
        if info.data.get("status") == EnumAutopilotStepStatus.SKIPPED and not v:
            msg = "Skipped step must provide a non-empty reason"
            raise ValueError(msg)
        return v


class ModelAutopilotCycleRecord(BaseModel):
    """Record of a single autopilot close-out cycle.

    Written to: ``$ONEX_STATE_DIR/state/autopilot/{cycle_id}.yaml``
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(..., description="Schema version (SemVer)")
    cycle_id: str = Field(..., description="Unique cycle identifier")
    mode: str = Field(..., description="Autopilot mode: close-out or build")
    started_at: str = Field(..., description="ISO datetime when cycle started")
    completed_at: str | None = Field(
        default=None, description="ISO datetime when cycle completed"
    )
    steps: list[ModelAutopilotStepResult] = Field(
        default_factory=list, description="Per-step results"
    )
    overall_status: EnumAutopilotCycleStatus = Field(
        default=EnumAutopilotCycleStatus.INCOMPLETE,
        description="Cycle completion status",
    )
    consecutive_noop_count: int = Field(
        default=0, description="Prior consecutive cycles with zero tickets"
    )
