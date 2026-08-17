# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for NodeContractDriftOrchestrator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml


@pytest.fixture
def container() -> MagicMock:
    """Mock ModelONEXContainer for node instantiation."""
    return MagicMock()


class TestNodeContractDriftOrchestrator:
    def test_orchestrator_node_instantiation(self, container: MagicMock) -> None:
        from onex_change_control.nodes.node_contract_drift_orchestrator.node import (
            NodeContractDriftOrchestrator,
        )

        node = NodeContractDriftOrchestrator(container)
        assert node is not None

    def test_orchestrator_contract_declares_downstream_nodes(
        self,
    ) -> None:
        """Verify the contract YAML declares the expected downstream topology."""
        contract_path = (
            Path(__file__).parent.parent
            / "src"
            / "onex_change_control"
            / "nodes"
            / "node_contract_drift_orchestrator"
            / "contract.yaml"
        )
        contract = yaml.safe_load(contract_path.read_text())
        downstream = contract.get("downstream_nodes", [])
        node_names = {n["node_name"] for n in downstream}
        assert "node_contract_drift_compute" in node_names
        assert "node_contract_drift_reducer" in node_names
        assert "node_contract_drift_effect" in node_names
