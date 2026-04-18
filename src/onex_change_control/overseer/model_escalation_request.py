# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from onex_change_control.overseer.enum_capability_tier import EnumCapabilityTier
from onex_change_control.overseer.enum_context_bundle_level import (
    EnumContextBundleLevel,
)
from onex_change_control.overseer.enum_failure_class import (
    EnumFailureClass,
)


class ModelEscalationRequest(BaseModel, frozen=True, extra="forbid"):
    """Escalation request emitted by domain runners when local retries are exhausted.

    Carries the failure classification, capability tier required for resolution,
    and context bundle level for the next dispatch. Wire type shared between
    domain runners and the global overseer. Frozen and extra-forbid for schema safety.
    """

    task_id: str
    domain: str
    node_id: str
    failure_class: EnumFailureClass
    capability_tier: EnumCapabilityTier = EnumCapabilityTier.C2
    context_bundle_level: EnumContextBundleLevel = EnumContextBundleLevel.L2
    attempt: int = Field(default=1, ge=1)
    max_attempts_exhausted: int = Field(default=3, ge=1)
    error_message: str | None = None
    error_detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # string-version-ok: wire type across domain runners and overseer; JSON
    schema_version: str = "1.0"
