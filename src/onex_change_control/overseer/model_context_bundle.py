# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""ModelContextBundle L0-L4 hierarchy for overseer context injection.

Layered context specifications for overseer model invocations.
Lower levels are always included; higher levels are added only when the
model tier requires them.

Inheritance chain: _ContextBundleBase -> L0 -> L1 -> L2 -> L3 -> L4.
Each level declares its own ``level`` Literal discriminator independently
(not overriding a base-class field) to satisfy mypy --strict.
All levels are frozen with ``extra="forbid"``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from onex_change_control.overseer.enum_context_bundle_level import (
    EnumContextBundleLevel,
)


class _ContextBundleBase(BaseModel, frozen=True, extra="forbid"):
    """Shared non-discriminated fields for all bundle levels."""

    run_id: str = Field(..., description="Pipeline or session run identifier.")
    task_id: str = Field(..., description="Task identifier within the run.")
    role: str = Field(..., description="Agent role for this invocation.")
    fsm_state: str = Field(..., description="Current finite-state-machine state.")


class ModelContextBundleL0(_ContextBundleBase, frozen=True, extra="forbid"):
    """Level 0 — minimal context.

    Contains only the fields required for task routing and FSM tracking.
    """

    level: Literal[EnumContextBundleLevel.L0] = Field(
        default=EnumContextBundleLevel.L0,
        description="Context bundle level.",
    )


class ModelContextBundleL1(ModelContextBundleL0, frozen=True, extra="forbid"):
    """Level 1 — basic context.

    Adds ticket metadata and summary on top of L0.
    """

    level: Literal[EnumContextBundleLevel.L1] = Field(  # type: ignore[assignment]
        default=EnumContextBundleLevel.L1,
        description="Context bundle level.",
    )
    ticket_id: str = Field(..., description="Linear ticket identifier.")
    summary: str = Field(..., description="Human-readable task summary.")


class ModelContextBundleL2(ModelContextBundleL1, frozen=True, extra="forbid"):
    """Level 2 — standard context.

    Adds entrypoint and file-scope information on top of L1.
    """

    level: Literal[EnumContextBundleLevel.L2] = Field(  # type: ignore[assignment]
        default=EnumContextBundleLevel.L2,
        description="Context bundle level.",
    )
    entrypoints: list[str] = Field(
        ..., description="Suggested code entrypoints for the task."
    )
    file_scope: list[str] = Field(
        default_factory=list,
        description="Files in scope for the task.",
    )


class ModelContextBundleL3(ModelContextBundleL2, frozen=True, extra="forbid"):
    """Level 3 — rich context.

    Adds architectural decisions and history on top of L2.
    """

    level: Literal[EnumContextBundleLevel.L3] = Field(  # type: ignore[assignment]
        default=EnumContextBundleLevel.L3,
        description="Context bundle level.",
    )
    decisions: list[str] = Field(
        default_factory=list,
        description="Active architectural decisions relevant to this task.",
    )
    history: list[str] = Field(
        default_factory=list,
        description="Relevant historical context entries.",
    )


class ModelContextBundleL4(ModelContextBundleL3, frozen=True, extra="forbid"):
    """Level 4 — maximum context.

    Adds full dependency graph and raw context payload on top of L3.
    """

    level: Literal[EnumContextBundleLevel.L4] = Field(  # type: ignore[assignment]
        default=EnumContextBundleLevel.L4,
        description="Context bundle level.",
    )
    dependency_graph: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Mapping of task IDs to their dependency task IDs.",
    )
    raw_context: dict[str, str] = Field(
        default_factory=dict,
        description="Arbitrary key-value context payload.",
    )


ModelContextBundle = (
    ModelContextBundleL0
    | ModelContextBundleL1
    | ModelContextBundleL2
    | ModelContextBundleL3
    | ModelContextBundleL4
)
"""Union type alias for routing engine dispatch."""
