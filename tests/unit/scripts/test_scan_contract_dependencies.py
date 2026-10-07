# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for contract dependency scanner."""

from pathlib import Path

from onex_change_control.scripts.scan_contract_dependencies import (
    scan_contracts,
)


class TestScanContracts:
    def test_extracts_topics_from_contract(self, tmp_path: Path) -> None:
        contract = tmp_path / "node_foo" / "contract.yaml"
        contract.parent.mkdir()
        contract.write_text("""
name: node_foo
event_bus:
  subscribe_topics:
    - onex.evt.omniclaude.task-delegated.v1
  publish_topics:
    - onex.evt.omnimarket.foo-completed.v1
""")
        entries = scan_contracts(tmp_path, repo_name="omnimarket")
        assert len(entries) == 1
        assert entries[0].node_name == "node_foo"
        assert entries[0].subscribe_topics == ["onex.evt.omniclaude.task-delegated.v1"]
        assert entries[0].publish_topics == ["onex.evt.omnimarket.foo-completed.v1"]

    def test_handles_missing_event_bus(self, tmp_path: Path) -> None:
        contract = tmp_path / "node_bar" / "contract.yaml"
        contract.parent.mkdir()
        contract.write_text("name: node_bar\nnode_type: COMPUTE_GENERIC\n")
        entries = scan_contracts(tmp_path, repo_name="omnimarket")
        assert len(entries) == 1
        assert entries[0].subscribe_topics == []
        assert entries[0].publish_topics == []

    def test_skips_unreadable_yaml(self, tmp_path: Path) -> None:
        """Malformed YAML should be skipped with a warning, not crash the scanner."""
        contract = tmp_path / "node_bad" / "contract.yaml"
        contract.parent.mkdir()
        contract.write_text(":\tinvalid: yaml: {{{")
        entries = scan_contracts(tmp_path, repo_name="omnimarket")
        assert len(entries) == 0

    def test_skips_contract_missing_name(self, tmp_path: Path) -> None:
        contract = tmp_path / "node_noname" / "contract.yaml"
        contract.parent.mkdir()
        contract.write_text("node_type: COMPUTE_GENERIC\n")
        entries = scan_contracts(tmp_path, repo_name="omnimarket")
        assert len(entries) == 0

    def test_skips_contract_with_wrong_event_bus_shape(self, tmp_path: Path) -> None:
        contract = tmp_path / "node_badevt" / "contract.yaml"
        contract.parent.mkdir()
        contract.write_text("name: node_badevt\nevent_bus: not_a_dict\n")
        entries = scan_contracts(tmp_path, repo_name="omnimarket")
        assert len(entries) == 0

    def test_extracts_db_table_with_access_mode(self, tmp_path: Path) -> None:
        contract = tmp_path / "node_writer" / "contract.yaml"
        contract.parent.mkdir()
        contract.write_text("""
name: node_writer
db_io:
  db_tables:
    - name: delegation_events
      access: write
""")
        entries = scan_contracts(tmp_path, repo_name="omnimarket")
        assert len(entries) == 1
        assert len(entries[0].db_tables) == 1
        assert entries[0].db_tables[0].name == "delegation_events"
        assert entries[0].db_tables[0].access == "write"

    def test_protocols_always_empty_no_catch_all_topics(self, tmp_path: Path) -> None:
        """OMN-7931: scanner must not assign catch-all TOPICS.

        Assigning protocols=["TOPICS"] to every topic-declaring
        node creates C(n,2) noise edges in the compute step.
        Scanner must leave protocols=[] and let topic overlap
        be captured via subscribe_topics/publish_topics.
        """
        contract = tmp_path / "node_with_topics" / "contract.yaml"
        contract.parent.mkdir()
        contract.write_text("""
name: node_with_topics
event_bus:
  subscribe_topics:
    - onex.evt.omniclaude.task-delegated.v1
  publish_topics:
    - onex.evt.omnimarket.foo-completed.v1
""")
        entries = scan_contracts(tmp_path, repo_name="omnimarket")
        assert len(entries) == 1
        assert entries[0].protocols == []
        assert entries[0].subscribe_topics == ["onex.evt.omniclaude.task-delegated.v1"]
        assert entries[0].publish_topics == ["onex.evt.omnimarket.foo-completed.v1"]
