# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

from __future__ import annotations

from pydantic import BaseModel, Field

from onex_change_control.overseer.enum_capability_tier import EnumCapabilityTier
from onex_change_control.overseer.enum_provider import EnumProvider
from onex_change_control.overseer.enum_retry_type import EnumRetryType
from onex_change_control.overseer.enum_risk_level import EnumRiskLevel


class ModelTaskShapeFeatures(BaseModel, frozen=True, extra="forbid"):
    """Feature vector describing a task for routing policy decisions.

    Used by the routing policy engine to select an appropriate model
    given a task's structural and contextual characteristics.
    Frozen and extra-forbid for schema safety.
    """

    domain: str
    """Domain bucket the task belongs to (e.g. 'code', 'reasoning')."""

    capability_tier: EnumCapabilityTier = EnumCapabilityTier.C2
    """Minimum capability tier required to handle the task."""

    risk_level: EnumRiskLevel = EnumRiskLevel.LOW
    """Risk classification for the task."""

    retry_type: EnumRetryType = EnumRetryType.NONE
    """Retry strategy to apply on failure."""

    preferred_provider: EnumProvider = EnumProvider.UNKNOWN
    """Preferred model provider, if any constraint exists."""

    novelty_score: float = Field(default=0.0, ge=0.0, le=1.0)
    """Novelty score in [0.0, 1.0]. Higher values indicate more unusual tasks."""

    estimated_tokens: int = Field(default=0, ge=0)
    """Estimated token count for the task prompt + context."""

    requires_tool_use: bool = False
    """Whether the task requires tool/function-call capability."""

    # string-version-ok: wire type for routing policy feature vector, serialized to JSON
    schema_version: str = "1.0"
