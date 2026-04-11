# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EnumCompletionOutcome(StrEnum):
    """Terminal outcome for a completed task in the performance ledger."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ModelCompletionReport(BaseModel, frozen=True, extra="forbid"):
    """Final outcome report written to the overseer performance ledger.

    Emitted once per task lifecycle when the task reaches a terminal state.
    Carries cost, timing, and outcome data for observability and routing
    feedback. Frozen and extra-forbid for schema safety.
    """

    task_id: str
    domain: str
    node_id: str
    outcome: EnumCompletionOutcome
    total_cost: float = Field(default=0.0, ge=0.0)
    total_duration_seconds: float = Field(default=0.0, ge=0.0)
    attempts_used: int = Field(default=1, ge=1)
    runner_id: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    schema_version: str = "1.0"
