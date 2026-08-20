# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""NodeContractDriftOrchestrator — ORCHESTRATOR for the drift detection pipeline.

Thin coordination shell. Orchestration flow is contract-driven.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from omnibase_core.models.container.model_onex_container import (
        ModelONEXContainer,
    )


class NodeContractDriftOrchestrator:
    """ORCHESTRATOR coordinating the drift detection pipeline."""

    def __init__(self, container: ModelONEXContainer) -> None:
        self._container = container
