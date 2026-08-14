# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for NodeContractDriftCompute."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from onex_change_control.enums.enum_drift_severity import EnumDriftSeverity
from onex_change_control.handlers.handler_drift_analysis import (
    compute_canonical_hash,
)
from onex_change_control.models.model_contract_drift_input import (
    ModelContractDriftInput,
)


@pytest.fixture
def container() -> MagicMock:
    """Mock ModelONEXContainer for node instantiation."""
    return MagicMock()


class TestNodeContractDriftCompute:
    def test_node_instantiation(self, container: MagicMock) -> None:
        from onex_change_control.nodes.node_contract_drift_compute.node import (
            NodeContractDriftCompute,
        )

        node = NodeContractDriftCompute(container)
        assert node is not None

    def test_node_registers_computations(self, container: MagicMock) -> None:
        from onex_change_control.nodes.node_contract_drift_compute.node import (
            NodeContractDriftCompute,
        )

        node = NodeContractDriftCompute(container)
        assert "contract_drift" in node._computations
        assert "contract_drift_detailed" in node._computations

    def test_hash_only_no_drift(
        self,
        container: MagicMock,
        base_compute_contract: dict[str, Any],
    ) -> None:
        from onex_change_control.nodes.node_contract_drift_compute.node import (
            NodeContractDriftCompute,
        )

        node = NodeContractDriftCompute(container)
        pinned = compute_canonical_hash(base_compute_contract)
        drift_input = ModelContractDriftInput(
            contract_name="test",
            current_contract=base_compute_contract,
            pinned_hash=pinned,
        )
        result = node._run_hash_only(drift_input)
        assert result.severity == EnumDriftSeverity.NONE
        assert not result.drift_detected

    def test_hash_only_breaking_drift(
        self,
        container: MagicMock,
        base_compute_contract: dict[str, Any],
    ) -> None:
        from onex_change_control.nodes.node_contract_drift_compute.node import (
            NodeContractDriftCompute,
        )

        node = NodeContractDriftCompute(container)
        drift_input = ModelContractDriftInput(
            contract_name="test",
            current_contract=base_compute_contract,
            pinned_hash="0" * 64,
        )
        result = node._run_hash_only(drift_input)
        assert result.severity == EnumDriftSeverity.BREAKING
        assert result.drift_detected
