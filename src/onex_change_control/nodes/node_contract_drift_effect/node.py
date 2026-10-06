# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""NodeContractDriftEffect — EFFECT node for drift event emission.

Thin coordination shell. Event emission is contract-driven via handler_routing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omnibase_core.models.container.model_onex_container import (
        ModelONEXContainer,
    )


class NodeContractDriftEffect:
    """EFFECT that emits Kafka events on contract drift detection."""

    def __init__(self, container: ModelONEXContainer) -> None:
        self._container = container
