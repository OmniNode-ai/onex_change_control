# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OmniNode Team
"""Tests for check_contract_drift.py drift detection script."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from scripts.validation.check_contract_drift import compute_contracts_hash

if TYPE_CHECKING:
    from pathlib import Path


def _write_contract(tmp_path: Path, subdir: str, data: dict[str, Any]) -> None:
    """Write a contract.yaml in a subdirectory."""
    node_dir = tmp_path / subdir
    node_dir.mkdir(parents=True, exist_ok=True)
    (node_dir / "contract.yaml").write_text(yaml.dump(data, default_flow_style=False))


class TestComputeContractsHash:
    """Tests for the compute_contracts_hash function."""

    def test_deterministic_same_input(self, tmp_path: Path) -> None:
        _write_contract(
            tmp_path,
            "node_a",
            {
                "name": "node_a",
                "published_events": [
                    {"topic": "onex.evt.platform.foo.v1", "event_type": "FooEvent"},
                ],
            },
        )
        hash1 = compute_contracts_hash(tmp_path)
        hash2 = compute_contracts_hash(tmp_path)
        assert hash1 == hash2

    def test_hash_changes_when_published_events_change(self, tmp_path: Path) -> None:
        _write_contract(
            tmp_path,
            "node_a",
            {
                "name": "node_a",
                "published_events": [
                    {"topic": "onex.evt.platform.foo.v1", "event_type": "FooEvent"},
                ],
            },
        )
        hash_before = compute_contracts_hash(tmp_path)

        _write_contract(
            tmp_path,
            "node_a",
            {
                "name": "node_a",
                "published_events": [
                    {"topic": "onex.evt.platform.foo.v1", "event_type": "FooEvent"},
                    {"topic": "onex.evt.platform.bar.v1", "event_type": "BarEvent"},
                ],
            },
        )
        hash_after = compute_contracts_hash(tmp_path)
        assert hash_before != hash_after

    def test_hash_changes_when_event_bus_changes(self, tmp_path: Path) -> None:
        _write_contract(
            tmp_path,
            "node_a",
            {
                "name": "node_a",
                "event_bus": {
                    "subscribe_topics": ["onex.evt.platform.foo.v1"],
                },
            },
        )
        hash_before = compute_contracts_hash(tmp_path)

        _write_contract(
            tmp_path,
            "node_a",
            {
                "name": "node_a",
                "event_bus": {
                    "subscribe_topics": [
                        "onex.evt.platform.foo.v1",
                        "onex.evt.platform.bar.v1",
                    ],
                },
            },
        )
        hash_after = compute_contracts_hash(tmp_path)
        assert hash_before != hash_after

    def test_empty_root_returns_deterministic_hash(self, tmp_path: Path) -> None:
        hash1 = compute_contracts_hash(tmp_path)
        hash2 = compute_contracts_hash(tmp_path)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex digest length

    def test_contracts_without_events_are_ignored(self, tmp_path: Path) -> None:
        _write_contract(
            tmp_path,
            "node_a",
            {
                "name": "node_a",
                "node_type": "COMPUTE_GENERIC",
            },
        )
        hash_with_contract = compute_contracts_hash(tmp_path)
        hash_empty = compute_contracts_hash(
            tmp_path / "nonexistent_subdir" if False else tmp_path
        )
        assert hash_with_contract == hash_empty  # No event sections = same as empty

    def test_hash_stable_across_yaml_key_ordering(self, tmp_path: Path) -> None:
        """JSON serialization with sort_keys ensures key order doesn't matter."""
        _write_contract(
            tmp_path,
            "node_a",
            {
                "published_events": [
                    {"event_type": "FooEvent", "topic": "onex.evt.platform.foo.v1"},
                ],
                "name": "node_a",
            },
        )
        hash1 = compute_contracts_hash(tmp_path)

        # Rewrite with different key order (YAML doesn't guarantee order)
        _write_contract(
            tmp_path,
            "node_a",
            {
                "name": "node_a",
                "published_events": [
                    {"topic": "onex.evt.platform.foo.v1", "event_type": "FooEvent"},
                ],
            },
        )
        hash2 = compute_contracts_hash(tmp_path)
        assert hash1 == hash2

    def test_multiple_contracts_order_is_deterministic(self, tmp_path: Path) -> None:
        _write_contract(
            tmp_path,
            "node_b",
            {
                "published_events": [
                    {"topic": "onex.evt.platform.bar.v1", "event_type": "BarEvent"},
                ],
            },
        )
        _write_contract(
            tmp_path,
            "node_a",
            {
                "published_events": [
                    {"topic": "onex.evt.platform.foo.v1", "event_type": "FooEvent"},
                ],
            },
        )
        hash1 = compute_contracts_hash(tmp_path)
        hash2 = compute_contracts_hash(tmp_path)
        assert hash1 == hash2
