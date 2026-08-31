# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for NodeContractDriftReducer and ModelDriftHistory."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from onex_change_control.models.model_drift_history import ModelDriftHistory


@pytest.fixture
def container() -> MagicMock:
    """Mock ModelONEXContainer for node instantiation."""
    return MagicMock()


class TestModelDriftHistory:
    def test_construction(self) -> None:
        history = ModelDriftHistory(
            contract_name="test_contract",
            state="clean",
            drift_reports=[],
            transition_count=0,
        )
        assert history.state == "clean"
        assert history.drift_reports == []

    def test_defaults(self) -> None:
        history = ModelDriftHistory(contract_name="test")
        assert history.state == "clean"
        assert history.drift_reports == []
        assert history.transition_count == 0


class TestNodeContractDriftReducer:
    def test_instantiation(self, container: MagicMock) -> None:
        from onex_change_control.nodes.node_contract_drift_reducer.node import (
            NodeContractDriftReducer,
        )

        node = NodeContractDriftReducer(container)
        assert node is not None

    @pytest.mark.skip(
        reason="FSM runtime not yet wired for external nodes (OMN-6689) — "
        "clean->drifted transition requires NodeReducer.process() + FSM contract wiring"
    )
    def test_reducer_clean_to_drifted(self, container: MagicMock) -> None:
        """BREAKING drift into clean reducer -> drifted."""

    @pytest.mark.skip(
        reason="FSM runtime not yet wired for external nodes (OMN-6689) — "
        "drifted->clean transition requires NodeReducer.process() + FSM contract wiring"
    )
    def test_reducer_drifted_to_clean(self, container: MagicMock) -> None:
        """NONE-severity into drifted reducer -> clean."""
