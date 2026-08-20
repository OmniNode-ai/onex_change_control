# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, Field, field_validator

from onex_change_control.overseer.model_task_state_envelope import (
    EnumTaskStatus,
)


class ModelTaskDeltaEnvelope(BaseModel, frozen=True, extra="forbid"):
    """Incremental task state change for overseer projection.

    Carries only the fields that changed, plus the mandatory task_id.
    Wire type shared between the global overseer, domain runners,
    and routing engine. Frozen and extra-forbid for schema safety.
    """

    task_id: str
    status: EnumTaskStatus | None = None
    runner_id: str | None = None
    attempt: int | None = Field(default=None, ge=1)
    payload: Mapping[str, Any] | None = None
    error: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # string-version-ok: wire type across overseer/runner boundary; JSON
    schema_version: str = "1.0"

    @field_validator("payload", mode="before")
    @classmethod
    def _freeze_payload(
        cls, value: Mapping[str, Any] | None
    ) -> Mapping[str, Any] | None:
        if value is None:
            return None
        return MappingProxyType(dict(value))
