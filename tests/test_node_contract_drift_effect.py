# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for NodeContractDriftEffect."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def container() -> MagicMock:
    """Mock ModelONEXContainer for node instantiation."""
    return MagicMock()


class TestNodeContractDriftEffect:
    def test_effect_node_instantiation(self, container: MagicMock) -> None:
        from onex_change_control.nodes.node_contract_drift_effect.node import (
            NodeContractDriftEffect,
        )

        node = NodeContractDriftEffect(container)
        assert node is not None

    @pytest.mark.skip(
        reason="Effect subcontract not loaded — "
        "contract-driven routing not yet wired for external repos"
    )
    def test_effect_contract_loads_topic(self, container: MagicMock) -> None:
        """Verify the EFFECT node's contract resolves the Kafka topic."""
