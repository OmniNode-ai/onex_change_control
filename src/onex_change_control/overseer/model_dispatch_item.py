# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""ModelDispatchItem — per-theme dispatch detail for ModelOvernightPhaseSpec."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from datetime import datetime


class ModelDispatchItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    theme_id: str
    title: str
    target_repo: str
    dispatch_mode: Literal[
        "skill",
        "agent_team",
        "foreground_required",
        "blocked_on_human",
        "cron",
    ]
    skill_or_command: str | None = None
    dependencies: tuple[str, ...] = ()
    isolation_worktree_required: bool = False
    open_prs: tuple[int, ...] = ()
    success_criteria: tuple[str, ...] = ()
    deadline: datetime | None = None
    priority: Literal["P0", "P1", "P2"] = "P1"
    notes: str | None = None


__all__ = ["ModelDispatchItem"]
