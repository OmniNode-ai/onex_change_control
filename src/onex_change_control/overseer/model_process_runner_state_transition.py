# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from onex_change_control.overseer.enum_process_runner_state import (
        EnumProcessRunnerState,
    )


class ModelProcessRunnerStateTransition(BaseModel, frozen=True, extra="forbid"):
    """Immutable record of a single FSM state transition for a process runner.

    Captures the from/to states and the timestamp at which the transition
    occurred. Frozen and extra-forbid for schema safety.
    """

    from_state: EnumProcessRunnerState = Field(
        description="The state the runner was in before this transition."
    )
    to_state: EnumProcessRunnerState = Field(
        description="The state the runner moved into."
    )
    transitioned_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the transition occurred.",
    )
