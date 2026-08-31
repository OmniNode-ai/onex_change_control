# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OCC dependency edge and rerun state models (OMN-10487)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ModelOccDepEdge(BaseModel):
    """Records that a product PR depends on an OCC ticket.

    Primary identity is ticket_id; PR numbers are secondary and may change
    (e.g. if a PR is closed and re-opened).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: str
    product_repo: str
    product_pr_number: int
    failure_reason: str  # "OCC_DEPENDENCY_OPEN" or "OCC_DEPENDENCY_MISSING"
    recorded_at: str  # ISO-8601 UTC, set by the recording workflow


class ModelOccDepEdgeStore(BaseModel):
    """Persisted set of OCC dependency edges.

    Written to drift/occ_dependency_edges.yaml by the record-dep-edge workflow
    and consumed by the rerun-downstream workflow.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1.0.0")
    edges: list[ModelOccDepEdge] = Field(default_factory=list)


class ModelOccRerunRecord(BaseModel):
    """Tracks one rerun attempt to prevent loops.

    Keyed by (occ_merge_commit, product_repo, product_pr_number).
    At most one rerun per OCC merge commit per downstream PR.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    occ_merge_commit: str
    product_repo: str
    product_pr_number: int
    rerun_at: str  # ISO-8601 UTC


class ModelOccRerunState(BaseModel):
    """Persisted rerun state to prevent downstream rerun loops.

    Written to drift/occ_rerun_state.yaml by the rerun-downstream workflow.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1.0.0")
    reruns: list[ModelOccRerunRecord] = Field(default_factory=list)


__all__ = [
    "ModelOccDepEdge",
    "ModelOccDepEdgeStore",
    "ModelOccRerunRecord",
    "ModelOccRerunState",
]
