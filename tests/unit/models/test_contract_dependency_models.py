# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for contract dependency models."""

import pytest
from onex_change_control.models.model_contract_dependency_input import (
    ModelContractEntry,
    ModelDbTableRef,
)
from pydantic import ValidationError

from onex_change_control.models.model_contract_dependency_output import (
    ModelContractDependencyOutput,
    ModelDependencyEdge,
    ModelDependencyWave,
)


class TestModelContractEntry:
    def test_entry_creation(self) -> None:
        entry = ModelContractEntry(
            repo="omnimarket",
            node_name="node_projection_delegation",
            subscribe_topics=["onex.evt.omniclaude.task-delegated.v1"],
            publish_topics=["onex.evt.omnimarket.projection-delegation-applied.v1"],
            protocols=["TOPICS"],
        )
        assert entry.repo == "omnimarket"
        assert len(entry.subscribe_topics) == 1

    def test_entry_with_db_table_ref(self) -> None:
        entry = ModelContractEntry(
            repo="omnimarket",
            node_name="node_projection_delegation",
            subscribe_topics=[],
            publish_topics=[],
            protocols=[],
            db_tables=[ModelDbTableRef(name="delegation_events", access="write")],
        )
        assert entry.db_tables[0].name == "delegation_events"
        assert entry.db_tables[0].access == "write"

    def test_entry_is_frozen(self) -> None:
        entry = ModelContractEntry(
            repo="omnimarket",
            node_name="node_projection_delegation",
            subscribe_topics=[],
            publish_topics=[],
            protocols=[],
        )
        with pytest.raises(ValidationError, match="frozen"):
            entry.repo = "omnidash"  # type: ignore[misc]


class TestModelDependencyEdge:
    def test_edge_has_deterministic_id(self) -> None:
        edge1 = ModelDependencyEdge(
            node_a_repo="omnimarket",
            node_a_name="node_projection_delegation",
            node_b_repo="omnimarket",
            node_b_name="node_projection_registration",
            shared_topics=["onex.evt.platform.node-heartbeat.v1"],
            shared_protocols=[],
            overlap_type="topic_co_consumer",
            direction="co_consumer",
        )
        edge2 = ModelDependencyEdge(
            node_a_repo="omnimarket",
            node_a_name="node_projection_delegation",
            node_b_repo="omnimarket",
            node_b_name="node_projection_registration",
            shared_topics=["onex.evt.platform.node-heartbeat.v1"],
            shared_protocols=[],
            overlap_type="topic_co_consumer",
            direction="co_consumer",
        )
        assert edge1.edge_id == edge2.edge_id

    def test_edge_id_is_order_independent(self) -> None:
        edge1 = ModelDependencyEdge(
            node_a_repo="omnimarket",
            node_a_name="node_a",
            node_b_repo="omnimarket",
            node_b_name="node_b",
            shared_topics=["topic.v1"],
            shared_protocols=[],
            overlap_type="topic_co_consumer",
            direction="co_consumer",
        )
        edge2 = ModelDependencyEdge(
            node_a_repo="omnimarket",
            node_a_name="node_b",
            node_b_repo="omnimarket",
            node_b_name="node_a",
            shared_topics=["topic.v1"],
            shared_protocols=[],
            overlap_type="topic_co_consumer",
            direction="co_consumer",
        )
        assert edge1.edge_id == edge2.edge_id


class TestModelContractDependencyOutput:
    def test_wave_computation_separates_overlapping_nodes(self) -> None:
        output = ModelContractDependencyOutput(
            entries=[],
            edges=[
                ModelDependencyEdge(
                    node_a_repo="omnimarket",
                    node_a_name="node_a",
                    node_b_repo="omnimarket",
                    node_b_name="node_b",
                    shared_topics=["shared.v1"],
                    shared_protocols=[],
                    overlap_type="topic_co_consumer",
                    direction="co_consumer",
                ),
            ],
            waves=[
                ModelDependencyWave(wave_number=0, node_refs=["omnimarket/node_a"]),
                ModelDependencyWave(wave_number=1, node_refs=["omnimarket/node_b"]),
            ],
            hotspot_topics=[],
        )
        assert len(output.waves) == 2
        assert output.edges[0].edge_id is not None
