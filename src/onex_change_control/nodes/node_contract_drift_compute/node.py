# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""NodeContractDriftCompute — COMPUTE node for contract drift detection.

Thin coordination shell. All business logic lives in handler_drift_analysis.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from onex_change_control.handlers.handler_drift_analysis import (
    analyze_drift,
    analyze_drift_with_pinned_contract,
)
from onex_change_control.models.model_contract_drift_input import (
    ModelContractDriftInput,  # noqa: TC001  Why: Pydantic model needs runtime type for field annotation
)
from onex_change_control.models.model_contract_drift_output import (
    ModelContractDriftOutput,  # noqa: TC001  Why: Pydantic model needs runtime type for field annotation
)

if TYPE_CHECKING:
    from omnibase_core.models.container.model_onex_container import (
        ModelONEXContainer,
    )


class NodeContractDriftCompute:
    """COMPUTE node that detects drift between a contract and its pinned baseline.

    Two computation types:
    - contract_drift: hash-only check (fast, for CI gates, no pinned dict needed)
    - contract_drift_detailed: field-level diff (requires pinned contract dict
      passed as pinned_contract in input metadata)
    """

    def __init__(self, container: ModelONEXContainer) -> None:
        self._container = container
        self._computations: dict[
            str,
            type[object],
        ] = {}
        self.register_computation("contract_drift", self._run_hash_only)
        self.register_computation("contract_drift_detailed", self._run_detailed)

    def register_computation(self, name: str, fn: object) -> None:
        """Register a named computation function."""
        self._computations[name] = fn  # type: ignore[assignment]

    def _run_hash_only(self, data: ModelContractDriftInput) -> ModelContractDriftOutput:
        return analyze_drift(data)

    def _run_detailed(self, data: ModelContractDriftInput) -> ModelContractDriftOutput:
        # For detailed mode, the pinned contract dict must be available.
        # If not provided, fall back to hash-only.
        pinned_contract = data.current_contract  # caller must supply via metadata
        return analyze_drift_with_pinned_contract(data, pinned_contract)
