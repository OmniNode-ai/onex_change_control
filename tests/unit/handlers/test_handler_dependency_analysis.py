# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for contract dependency analysis handler."""
import pytest

from onex_change_control.handlers.handler_dependency_analysis import (
    compute_dependency_graph,
)
from onex_change_control.models.model_contract_dependency_input import (
    ModelContractDependencyInput,
    ModelContractEntry,
    ModelDbTableRef,
)


class TestComputeDependencyGraph:
    def test_topic_overlap_creates_edge(self) -> None:
        """Two nodes subscribing to the same topic should produce an edge."""
        entries = [
            ModelContractEntry(
                repo="omnimarket", node_name="node_projection_delegation",
                subscribe_topics=["onex.evt.omniclaude.task-delegated.v1"],
                publish_topics=[], protocols=[],
            ),
            ModelContractEntry(
                repo="omnimarket", node_name="node_projection_savings",
                subscribe_topics=["onex.evt.omniclaude.task-delegated.v1"],
                publish_topics=[], protocols=[],
            ),
        ]
        inp = ModelContractDependencyInput(entries=entries)
        result = compute_dependency_graph(inp)

        assert len(result.edges) == 1
        assert "onex.evt.omniclaude.task-delegated.v1" in result.edges[0].shared_topics

    def test_producer_consumer_creates_edge(self) -> None:
        """A node publishing to a topic that another subscribes to creates an edge."""
        entries = [
            ModelContractEntry(
                repo="omniclaude", node_name="node_emit_hook",
                subscribe_topics=[], protocols=[],
                publish_topics=["onex.evt.omniclaude.task-delegated.v1"],
            ),
            ModelContractEntry(
                repo="omnimarket", node_name="node_projection_delegation",
                subscribe_topics=["onex.evt.omniclaude.task-delegated.v1"],
                publish_topics=[], protocols=[],
            ),
        ]
        inp = ModelContractDependencyInput(entries=entries)
        result = compute_dependency_graph(inp)

        assert len(result.edges) == 1
        assert result.edges[0].overlap_type == "topic_producer_consumer"
        assert result.edges[0].direction == "producer_to_consumer"

    def test_no_overlap_produces_no_edges(self) -> None:
        entries = [
            ModelContractEntry(
                repo="omnimarket", node_name="node_a",
                subscribe_topics=["topic.a.v1"],
                publish_topics=[], protocols=[],
            ),
            ModelContractEntry(
                repo="omnimarket", node_name="node_b",
                subscribe_topics=["topic.b.v1"],
                publish_topics=[], protocols=[],
            ),
        ]
        inp = ModelContractDependencyInput(entries=entries)
        result = compute_dependency_graph(inp)

        assert len(result.edges) == 0
        assert len(result.waves) == 1  # all in wave 0

    def test_waves_separate_overlapping_nodes(self) -> None:
        """Nodes with overlap must be in different waves (conservative overlap-based grouping)."""
        entries = [
            ModelContractEntry(
                repo="omnimarket", node_name="node_a",
                subscribe_topics=["shared.v1"],
                publish_topics=[], protocols=[],
            ),
            ModelContractEntry(
                repo="omnimarket", node_name="node_b",
                subscribe_topics=["shared.v1"],
                publish_topics=[], protocols=[],
            ),
            ModelContractEntry(
                repo="omnidash", node_name="node_c",
                subscribe_topics=["other.v1"],
                publish_topics=[], protocols=[],
            ),
        ]
        inp = ModelContractDependencyInput(entries=entries)
        result = compute_dependency_graph(inp)

        # node_c should be in wave 0 (no overlap)
        # node_a and node_b should be in different waves
        wave_0_refs = result.waves[0].node_refs
        assert "omnidash/node_c" in wave_0_refs
        all_refs = [ref for w in result.waves for ref in w.node_refs]
        assert "omnimarket/node_a" in all_refs
        assert "omnimarket/node_b" in all_refs

    def test_hotspot_topics_detected(self) -> None:
        """Topics appearing in 3+ nodes should be flagged as hotspots."""
        entries = [
            ModelContractEntry(repo="r", node_name=f"node_{i}", subscribe_topics=["hot.v1"], publish_topics=[], protocols=[])
            for i in range(4)
        ]
        inp = ModelContractDependencyInput(entries=entries)
        result = compute_dependency_graph(inp)

        assert len(result.hotspot_topics) >= 1
        assert result.hotspot_topics[0].topic == "hot.v1"
        assert result.hotspot_topics[0].overlap_count >= 3

    def test_db_table_overlap_creates_edge(self) -> None:
        """Two nodes writing to the same DB table should produce an edge."""
        entries = [
            ModelContractEntry(
                repo="omnimarket", node_name="node_a",
                subscribe_topics=[], publish_topics=[], protocols=[],
                db_tables=[ModelDbTableRef(name="delegation_events", access="write")],
            ),
            ModelContractEntry(
                repo="omnimarket", node_name="node_b",
                subscribe_topics=[], publish_topics=[], protocols=[],
                db_tables=[ModelDbTableRef(name="delegation_events", access="write")],
            ),
        ]
        inp = ModelContractDependencyInput(entries=entries)
        result = compute_dependency_graph(inp)

        assert len(result.edges) == 1
        assert result.edges[0].overlap_type == "db_table_shared_write"

    def test_empty_input_produces_empty_output(self) -> None:
        inp = ModelContractDependencyInput(entries=[])
        result = compute_dependency_graph(inp)

        assert len(result.edges) == 0
        assert len(result.waves) == 0
        assert len(result.hotspot_topics) == 0

    def test_protocol_overlap_creates_edge(self) -> None:
        """Two nodes declaring the same protocol surface should produce an edge."""
        entries = [
            ModelContractEntry(
                repo="omnimarket", node_name="node_a",
                subscribe_topics=[], publish_topics=[], protocols=["PUBLIC_API"],
            ),
            ModelContractEntry(
                repo="omnimarket", node_name="node_b",
                subscribe_topics=[], publish_topics=[], protocols=["PUBLIC_API"],
            ),
        ]
        inp = ModelContractDependencyInput(entries=entries)
        result = compute_dependency_graph(inp)

        assert len(result.edges) == 1
        assert result.edges[0].overlap_type == "protocol"

    def test_mixed_overlap_produces_single_edge_with_both_dimensions(self) -> None:
        """A pair sharing both a topic AND a db_table should produce a single 'mixed' edge, not two separate edges."""
        entries = [
            ModelContractEntry(
                repo="omnimarket", node_name="node_a",
                subscribe_topics=["shared.v1"], publish_topics=[], protocols=[],
                db_tables=[ModelDbTableRef(name="shared_table", access="write")],
            ),
            ModelContractEntry(
                repo="omnimarket", node_name="node_b",
                subscribe_topics=["shared.v1"], publish_topics=[], protocols=[],
                db_tables=[ModelDbTableRef(name="shared_table", access="write")],
            ),
        ]
        inp = ModelContractDependencyInput(entries=entries)
        result = compute_dependency_graph(inp)

        # Must produce exactly one edge for the pair, not two
        assert len(result.edges) == 1
        assert result.edges[0].overlap_type == "mixed"
        # Both overlap dimensions must be represented
        assert "shared.v1" in result.edges[0].shared_topics
        assert "shared_table" in result.edges[0].shared_db_tables
