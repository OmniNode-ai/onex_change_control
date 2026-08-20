# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EnumTaskStatus(StrEnum):
    """FSM states for overseer task lifecycle.

    10-member finite state machine covering the full task lifecycle
    from creation through terminal states.
    """

    PENDING = "pending"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class ModelTaskStateEnvelope(BaseModel, frozen=True, extra="forbid"):
    """Full task state snapshot for overseer projection.

    Wire type shared between the global overseer, domain runners,
    and routing engine. Frozen and extra-forbid for schema safety.
    """

    task_id: str = Field(..., description="Task identifier from upstream task record.")
    status: EnumTaskStatus
    domain: str
    node_id: str
    runner_id: str | None = None
    attempt: int = Field(default=1, ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # string-version-ok: wire type across overseer/runner boundary; JSON
    schema_version: str = "1.0"
