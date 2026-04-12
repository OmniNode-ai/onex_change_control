# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Wire type for overnight session contract.

Defines the machine-readable contract for an overnight pipeline session,
including expected phases, cost ceiling, halt conditions, and evidence
requirements. Replaces the markdown-only standing orders pattern.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from onex_change_control.overseer.model_dispatch_item import (
    ModelDispatchItem,
)


class ModelOvernightHaltCondition(BaseModel):
    """Condition that triggers an action when its trigger clause matches.

    On-halt actions:
      - ``dispatch_skill``: fire the named skill (e.g. ``onex:pr_polish``) as
        a foreground recovery and continue the tick loop.
      - ``halt_and_notify``: stop the pipeline and emit a tick event with a
        notify reason so the controlling session sees it.
      - ``hard_halt``: stop the pipeline immediately with halt_reason set.

    Backwards compat: the pre-OMN-8375 shape only used
    ``condition_id/description/check_type/threshold``. All new fields are
    optional, and the legacy ``cost_ceiling`` / ``phase_failure_count`` /
    ``time_elapsed`` check types still evaluate against ``threshold``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    condition_id: str
    description: str
    check_type: Literal[
        "cost_ceiling",
        "phase_failure_count",
        "time_elapsed",
        "pr_blocked_too_long",
        "required_outcome_missing",
        "custom",
    ]
    threshold: float = 0.0  # USD for cost_ceiling, count for failures, seconds for time
    # OMN-8375: action routing. Default halts the pipeline (legacy behavior).
    on_halt: Literal["hard_halt", "dispatch_skill", "halt_and_notify"] = "hard_halt"
    # Required when on_halt == "dispatch_skill" (e.g. "onex:pr_polish").
    skill: str | None = None
    # Context fields used by specific check_types.
    # pr_blocked_too_long → watches this PR number; time measured in minutes.
    pr: int | None = None
    threshold_minutes: float | None = None
    # required_outcome_missing → outcome name that must not be absent.
    outcome: str | None = None

    @model_validator(mode="after")
    def _enforce_conditional_fields(self) -> ModelOvernightHaltCondition:
        if self.on_halt == "dispatch_skill" and not self.skill:
            msg = "skill is required when on_halt='dispatch_skill'"
            raise ValueError(msg)
        if self.check_type == "pr_blocked_too_long":
            if self.pr is None:
                msg = "pr is required when check_type='pr_blocked_too_long'"
                raise ValueError(msg)
            if self.threshold_minutes is None:
                msg = (
                    "threshold_minutes is required"
                    " when check_type='pr_blocked_too_long'"
                )
                raise ValueError(msg)
        if self.check_type == "required_outcome_missing" and not self.outcome:
            msg = "outcome is required when check_type='required_outcome_missing'"
            raise ValueError(msg)
        return self


class ModelOvernightPhaseSpec(BaseModel):
    """Specification for a single overnight phase."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    phase_name: str
    required: bool = True
    timeout_seconds: int = 3600  # 1 hour default
    halt_on_failure: bool = False
    success_criteria: list[str] = Field(default_factory=list)
    dispatch_items: tuple[ModelDispatchItem, ...] = ()
    # Phase-scoped success criteria probed after dispatch; phase does not
    # advance until all required_outcomes resolve satisfied. Names are
    # resolved by HandlerOvernight against its registered outcome probes
    # (see OMN-8375).
    required_outcomes: tuple[str, ...] = ()
    # Phase-scoped halt conditions evaluated on every tick while the phase
    # is active. Scoped here (in addition to contract.halt_conditions) so a
    # phase can declare "watch this PR only while I'm running".
    halt_conditions: tuple[ModelOvernightHaltCondition, ...] = ()


class ModelOvernightContract(BaseModel):
    """Machine-readable contract for an overnight pipeline session.

    This is the session-level analogue of ModelTicketContract (per-ticket).
    It declares the expected phases, success criteria, cost constraints,
    and halt conditions for an autonomous overnight run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "1.0.0"
    session_id: str  # correlation ID for the overnight session
    created_at: datetime
    max_cost_usd: float = 5.0
    max_duration_seconds: int = 28800  # 8 hours
    dry_run: bool = False

    # Expected phases in order.
    # No default — phases must be supplied explicitly (from session contract YAML
    # or template). Operational defaults live in overnight_contract.template.yaml.
    phases: tuple[ModelOvernightPhaseSpec, ...]

    # Halt conditions — populated by model_validator when not provided.
    halt_conditions: tuple[ModelOvernightHaltCondition, ...] = Field(
        default_factory=tuple
    )

    @model_validator(mode="after")
    def _apply_default_halt_conditions(self) -> ModelOvernightContract:
        if self.halt_conditions:
            return self
        object.__setattr__(
            self,
            "halt_conditions",
            (
                ModelOvernightHaltCondition(
                    condition_id="cost_ceiling",
                    description="Stop if accumulated cost exceeds ceiling",
                    check_type="cost_ceiling",
                    threshold=self.max_cost_usd,
                ),
                ModelOvernightHaltCondition(
                    condition_id="phase_failure_limit",
                    description="Stop after 3 consecutive phase failures",
                    check_type="phase_failure_count",
                    threshold=3.0,
                ),
            ),
        )
        return self

    # Standing orders (replaces nightly-loop-decisions.md)
    standing_orders: tuple[str, ...] = Field(default_factory=tuple)

    # Evidence requirements (parallel to ModelTicketContract.dod_evidence)
    required_outcomes: tuple[str, ...] = Field(
        default_factory=lambda: (
            "merge_sweep_completed",
            "platform_readiness_gate_passed",
        )
    )
