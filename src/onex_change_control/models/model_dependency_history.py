# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Dependency history — accumulated state from dependency reducer."""

from __future__ import annotations

from datetime import (
    datetime,  # noqa: TC003  Why: Pydantic model uses datetime at runtime
)

from pydantic import BaseModel, ConfigDict

from onex_change_control.models.model_contract_dependency_output import (  # noqa: TC001  Why: Pydantic model needs runtime type for field annotation
    ModelContractDependencyOutput,
    ModelHotspotTopic,
)


class ModelDependencySnapshot(BaseModel):
    """A single dependency graph observation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_at: datetime
    edge_count: int
    wave_count: int
    hotspot_count: int


class ModelDependencyHistory(BaseModel):
    """Accumulated dependency state over time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: str  # "stable" or "hotspot_detected"
    snapshots: list[ModelDependencySnapshot]
    persistent_hotspots: list[
        ModelHotspotTopic
    ]  # topics that are hotspots in 3+ consecutive snapshots
    latest: ModelContractDependencyOutput | None = None
