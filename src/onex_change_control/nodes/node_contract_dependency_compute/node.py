# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""NodeContractDependencyCompute — COMPUTE node for contract dependency analysis.

Thin coordination shell. All business logic lives in handler_dependency_analysis.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from onex_change_control.handlers.handler_dependency_analysis import (
    compute_dependency_graph,
)
from onex_change_control.models.model_contract_dependency_input import (
    ModelContractDependencyInput,  # noqa: TC001  Why: Pydantic model needs runtime type for field annotation
)
from onex_change_control.models.model_contract_dependency_output import (
    ModelContractDependencyOutput,  # noqa: TC001  Why: Pydantic model needs runtime type for field annotation
)

if TYPE_CHECKING:
    from omnibase_core.models.container.model_onex_container import (
        ModelONEXContainer,
    )


class NodeContractDependencyCompute:
    """COMPUTE node that builds protocol dependency graph from contract declarations."""

    def __init__(self, container: ModelONEXContainer) -> None:
        self._container = container
        self._computations: dict[str, type[object]] = {}
        self.register_computation("contract_dependency", self._run)

    def register_computation(self, name: str, fn: object) -> None:
        self._computations[name] = fn  # type: ignore[assignment]

    def _run(self, data: ModelContractDependencyInput) -> ModelContractDependencyOutput:
        return compute_dependency_graph(data)
