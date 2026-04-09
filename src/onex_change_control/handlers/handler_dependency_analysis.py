# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Contract dependency analysis — pure computation, no I/O.

Computes protocol-level dependency graph from contract declarations.
Detects overlap in topics, protocols, and DB tables between nodes.
Outputs edges and conservative overlap-based waves for parallel
execution grouping.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from onex_change_control.models.model_contract_dependency_output import (
    ModelContractDependencyOutput,
    ModelDependencyEdge,
    ModelDependencyWave,
    ModelHotspotTopic,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from onex_change_control.models.model_contract_dependency_input import (
        ModelContractDependencyInput,
        ModelContractEntry,
    )


def _node_ref(entry: ModelContractEntry) -> str:
    return f"{entry.repo}/{entry.node_name}"


def _topic_direction(a: ModelContractEntry, b: ModelContractEntry, topic: str) -> str:
    """Determine directional relationship between two nodes sharing a topic."""
    a_publishes = topic in a.publish_topics
    a_subscribes = topic in a.subscribe_topics
    b_publishes = topic in b.publish_topics
    b_subscribes = topic in b.subscribe_topics

    if (a_publishes and b_subscribes) or (b_publishes and a_subscribes):
        return "producer_to_consumer"
    if a_subscribes and b_subscribes:
        return "co_consumer"
    if a_publishes and b_publishes:
        return "co_producer"
    return "bidirectional"


def _topic_overlap_type(direction: str) -> str:
    mapping = {
        "producer_to_consumer": "topic_producer_consumer",
        "co_consumer": "topic_co_consumer",
        "co_producer": "topic_co_producer",
        "bidirectional": "topic_co_consumer",
    }
    return mapping.get(direction, "topic_co_consumer")


def _db_overlap_type(
    a: ModelContractEntry, b: ModelContractEntry, table_name: str
) -> str:
    """Determine DB table overlap type based on access modes."""
    a_access = next((t.access for t in a.db_tables if t.name == table_name), None)
    b_access = next((t.access for t in b.db_tables if t.name == table_name), None)
    if a_access in ("write", "read_write") and b_access in (
        "write",
        "read_write",
    ):
        return "db_table_shared_write"
    return "db_table_read_write"


@dataclass
class _EdgeAccumulator:
    """Accumulates overlap dimensions for a node pair."""

    a: ModelContractEntry
    b: ModelContractEntry
    shared_topics: list[str] = field(default_factory=list)
    shared_protocols: list[str] = field(default_factory=list)
    shared_db_tables: list[str] = field(default_factory=list)
    directions: list[str] = field(default_factory=list)
    overlap_types: set[str] = field(default_factory=set)

    def to_edge(self) -> ModelDependencyEdge:
        unique_directions = set(self.directions)
        if len(unique_directions) == 1:
            direction = next(iter(unique_directions))
        else:
            direction = "bidirectional"

        overlap_type = (
            "mixed"
            if len(self.overlap_types) > 1
            else (next(iter(self.overlap_types)) if self.overlap_types else "mixed")
        )

        return ModelDependencyEdge(
            node_a_repo=self.a.repo,
            node_a_name=self.a.node_name,
            node_b_repo=self.b.repo,
            node_b_name=self.b.node_name,
            shared_topics=sorted(self.shared_topics),
            shared_protocols=sorted(self.shared_protocols),
            shared_db_tables=sorted(self.shared_db_tables),
            overlap_type=overlap_type,
            direction=direction,
        )


def _accumulate_topic_overlap(
    topic_index: dict[str, list[ModelContractEntry]],
    get_or_create: Callable[[ModelContractEntry, ModelContractEntry], _EdgeAccumulator],
) -> None:
    """Accumulate topic overlap edges into pair accumulators."""
    for topic, group in topic_index.items():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                acc = get_or_create(a, b)
                if topic not in acc.shared_topics:
                    acc.shared_topics.append(topic)
                direction = _topic_direction(a, b, topic)
                if direction not in acc.directions:
                    acc.directions.append(direction)
                acc.overlap_types.add(_topic_overlap_type(direction))


def _accumulate_db_overlap(
    db_index: dict[str, list[ModelContractEntry]],
    get_or_create: Callable[[ModelContractEntry, ModelContractEntry], _EdgeAccumulator],
) -> None:
    """Accumulate DB table overlap edges into pair accumulators."""
    for table_name, group in db_index.items():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                acc = get_or_create(a, b)
                if table_name not in acc.shared_db_tables:
                    acc.shared_db_tables.append(table_name)
                db_type = _db_overlap_type(a, b, table_name)
                acc.overlap_types.add(db_type)
                # Only set direction when topic analysis
                # hasn't already set one
                if not acc.directions:
                    acc.directions.append("bidirectional")


def _accumulate_protocol_overlap(
    protocol_index: dict[str, list[ModelContractEntry]],
    get_or_create: Callable[[ModelContractEntry, ModelContractEntry], _EdgeAccumulator],
) -> None:
    """Accumulate protocol overlap edges into pair accumulators."""
    for proto, group in protocol_index.items():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                acc = get_or_create(a, b)
                if proto not in acc.shared_protocols:
                    acc.shared_protocols.append(proto)
                acc.overlap_types.add("protocol")
                # Only set direction when topic analysis
                # hasn't already set one
                if not acc.directions:
                    acc.directions.append("bidirectional")


def _compute_edges(
    entries: list[ModelContractEntry],
) -> list[ModelDependencyEdge]:
    """Find all overlap edges between contract entries.

    Each node pair produces at most one edge, even if they overlap on
    multiple dimensions (topic + db_table). Multi-dimension pairs get
    overlap_type="mixed".
    """
    topic_index: dict[str, list[ModelContractEntry]] = defaultdict(list)
    db_index: dict[str, list[ModelContractEntry]] = defaultdict(list)
    protocol_index: dict[str, list[ModelContractEntry]] = defaultdict(list)

    for entry in entries:
        all_topics = set(entry.subscribe_topics) | set(entry.publish_topics)
        for topic in all_topics:
            topic_index[topic].append(entry)
        for table_ref in entry.db_tables:
            db_index[table_ref.name].append(entry)
        for proto in entry.protocols:
            protocol_index[proto].append(entry)

    pair_accumulators: dict[tuple[str, ...], _EdgeAccumulator] = {}

    def _get_or_create(
        a: ModelContractEntry, b: ModelContractEntry
    ) -> _EdgeAccumulator:
        pair = tuple(sorted([_node_ref(a), _node_ref(b)]))
        if pair not in pair_accumulators:
            pair_accumulators[pair] = _EdgeAccumulator(a=a, b=b)
        return pair_accumulators[pair]

    _accumulate_topic_overlap(topic_index, _get_or_create)
    _accumulate_db_overlap(db_index, _get_or_create)
    _accumulate_protocol_overlap(protocol_index, _get_or_create)

    return [acc.to_edge() for acc in pair_accumulators.values()]


def _compute_waves(
    entries: list[ModelContractEntry],
    edges: list[ModelDependencyEdge],
) -> list[ModelDependencyWave]:
    """Compute conservative overlap-based parallel groups.

    Uses greedy graph coloring. Nodes with any detected overlap are
    placed in different waves.
    """
    if not entries:
        return []

    neighbors: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        ra = f"{e.node_a_repo}/{e.node_a_name}"
        rb = f"{e.node_b_repo}/{e.node_b_name}"
        neighbors[ra].add(rb)
        neighbors[rb].add(ra)

    wave_assignment: dict[str, int] = {}
    for entry in entries:
        ref = _node_ref(entry)
        used_waves = {
            wave_assignment[n] for n in neighbors[ref] if n in wave_assignment
        }
        w = 0
        while w in used_waves:
            w += 1
        wave_assignment[ref] = w

    wave_groups: dict[int, list[str]] = defaultdict(list)
    for ref, w in wave_assignment.items():
        wave_groups[w].append(ref)

    return [
        ModelDependencyWave(wave_number=w, node_refs=sorted(refs))
        for w, refs in sorted(wave_groups.items())
    ]


def _compute_hotspots(
    entries: list[ModelContractEntry],
    min_count: int = 3,
) -> list[ModelHotspotTopic]:
    """Find topics touched by many nodes."""
    topic_nodes: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        for topic in set(entry.subscribe_topics) | set(entry.publish_topics):
            topic_nodes[topic].append(_node_ref(entry))

    return sorted(
        [
            ModelHotspotTopic(
                topic=topic,
                overlap_count=len(refs),
                node_refs=sorted(refs),
            )
            for topic, refs in topic_nodes.items()
            if len(refs) >= min_count
        ],
        key=lambda h: h.overlap_count,
        reverse=True,
    )


def compute_dependency_graph(
    inp: ModelContractDependencyInput,
) -> ModelContractDependencyOutput:
    """Pure computation: contracts in, dependency graph out."""
    edges = _compute_edges(inp.entries)
    waves = _compute_waves(inp.entries, edges)
    hotspots = _compute_hotspots(inp.entries)

    return ModelContractDependencyOutput(
        entries=inp.entries,
        edges=edges,
        waves=waves,
        hotspot_topics=hotspots,
    )
