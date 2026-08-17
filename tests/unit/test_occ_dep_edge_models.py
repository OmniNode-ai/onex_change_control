# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Unit tests for OCC dependency edge models (OMN-10487)."""

from __future__ import annotations

import pytest
import yaml

from onex_change_control.models.model_occ_dep_edge import (
    ModelOccDepEdge,
    ModelOccDepEdgeStore,
    ModelOccRerunRecord,
    ModelOccRerunState,
)


@pytest.mark.unit
def test_occ_dep_edge_roundtrip() -> None:
    edge = ModelOccDepEdge(
        ticket_id="OMN-10487",
        product_repo="omnibase_core",
        product_pr_number=999,
        failure_reason="OCC_DEPENDENCY_OPEN",
        recorded_at="2026-05-12T00:00:00Z",
    )
    assert edge.ticket_id == "OMN-10487"
    assert edge.product_repo == "omnibase_core"
    assert edge.product_pr_number == 999
    assert edge.failure_reason == "OCC_DEPENDENCY_OPEN"


@pytest.mark.unit
def test_occ_dep_edge_store_default_is_empty() -> None:
    store = ModelOccDepEdgeStore()
    assert store.schema_version == "1.0.0"
    assert store.edges == []


@pytest.mark.unit
def test_occ_dep_edge_store_upsert_idempotent() -> None:
    edge = ModelOccDepEdge(
        ticket_id="OMN-10487",
        product_repo="omnibase_core",
        product_pr_number=999,
        failure_reason="OCC_DEPENDENCY_MISSING",
        recorded_at="2026-05-12T00:00:00Z",
    )
    store = ModelOccDepEdgeStore(edges=[edge, edge])
    # Duplicate edges ARE allowed by the model — dedup is the workflow's job.
    assert len(store.edges) == 2


@pytest.mark.unit
def test_occ_dep_edge_store_yaml_roundtrip() -> None:
    edge = ModelOccDepEdge(
        ticket_id="OMN-10487",
        product_repo="omnibase_core",
        product_pr_number=999,
        failure_reason="OCC_DEPENDENCY_OPEN",
        recorded_at="2026-05-12T00:00:00Z",
    )
    store = ModelOccDepEdgeStore(edges=[edge])
    raw = yaml.dump(store.model_dump(), default_flow_style=False)
    loaded = yaml.safe_load(raw)
    restored = ModelOccDepEdgeStore.model_validate(loaded)
    assert restored.edges[0].ticket_id == "OMN-10487"
    assert restored.edges[0].failure_reason == "OCC_DEPENDENCY_OPEN"


@pytest.mark.unit
def test_occ_rerun_record_roundtrip() -> None:
    record = ModelOccRerunRecord(
        occ_merge_commit="abc123def456" * 3 + "abcd",
        product_repo="omnibase_infra",
        product_pr_number=42,
        rerun_at="2026-05-12T01:00:00Z",
    )
    assert record.occ_merge_commit.startswith("abc123")
    assert record.product_pr_number == 42


@pytest.mark.unit
def test_occ_rerun_state_default_is_empty() -> None:
    state = ModelOccRerunState()
    assert state.schema_version == "1.0.0"
    assert state.reruns == []


@pytest.mark.unit
def test_occ_rerun_state_yaml_roundtrip() -> None:
    record = ModelOccRerunRecord(
        occ_merge_commit="a" * 40,
        product_repo="omnibase_core",
        product_pr_number=100,
        rerun_at="2026-05-12T00:00:00Z",
    )
    state = ModelOccRerunState(reruns=[record])
    raw = yaml.dump(state.model_dump(), default_flow_style=False)
    loaded = yaml.safe_load(raw)
    restored = ModelOccRerunState.model_validate(loaded)
    assert restored.reruns[0].occ_merge_commit == "a" * 40


@pytest.mark.unit
def test_drift_edge_file_is_valid_yaml() -> None:
    """The seed drift/occ_dependency_edges.yaml parses and validates."""
    import pathlib

    edge_file = pathlib.Path("drift/occ_dependency_edges.yaml")
    assert edge_file.is_file(), "drift/occ_dependency_edges.yaml must exist"
    raw = yaml.safe_load(edge_file.read_text(encoding="utf-8"))
    store = ModelOccDepEdgeStore.model_validate(raw)
    assert store.schema_version == "1.0.0"
    assert isinstance(store.edges, list)


@pytest.mark.unit
def test_drift_rerun_state_file_is_valid_yaml() -> None:
    """The seed drift/occ_rerun_state.yaml parses and validates."""
    import pathlib

    state_file = pathlib.Path("drift/occ_rerun_state.yaml")
    assert state_file.is_file(), "drift/occ_rerun_state.yaml must exist"
    raw = yaml.safe_load(state_file.read_text(encoding="utf-8"))
    state = ModelOccRerunState.model_validate(raw)
    assert state.schema_version == "1.0.0"
    assert isinstance(state.reruns, list)
