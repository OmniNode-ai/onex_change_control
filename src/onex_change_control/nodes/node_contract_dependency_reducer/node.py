# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""NodeContractDependencyReducer — REDUCER node for dependency history accumulation.

Thin coordination shell. FSM behavior is contract-driven via state_machine.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omnibase_core.models.container.model_onex_container import (
        ModelONEXContainer,
    )


class NodeContractDependencyReducer:
    """REDUCER that accumulates contract dependency state over time."""

    def __init__(self, container: ModelONEXContainer) -> None:
        self._container = container
