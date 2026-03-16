# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OmniNode Team
"""Tests for check_published_events_structure.py validator."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

if TYPE_CHECKING:
    from pathlib import Path

from scripts.validation.check_published_events_structure import check_contract


@pytest.fixture
def write_contract(tmp_path: Path):
    """Helper to write a contract.yaml with given published_events."""

    def _write(
        published_events: list | dict | None = None, extra: dict | None = None
    ) -> Path:
        data: dict = {"name": "test_node", "node_type": "ORCHESTRATOR_GENERIC"}
        if published_events is not None:
            data["published_events"] = published_events
        if extra:
            data.update(extra)
        path = tmp_path / "contract.yaml"
        path.write_text(yaml.dump(data, default_flow_style=False))
        return path

    return _write


class TestCleanContracts:
    """Contracts that should pass validation."""

    def test_valid_single_entry(self, write_contract):
        path = write_contract(
            [
                {
                    "topic": "onex.evt.platform.node-registered.v1",
                    "event_type": "NodeRegistered",
                }
            ]
        )
        assert check_contract(path) == []

    def test_valid_multiple_entries(self, write_contract):
        path = write_contract(
            [
                {
                    "topic": "onex.evt.platform.node-registered.v1",
                    "event_type": "NodeRegistered",
                },
                {
                    "topic": "onex.cmd.platform.request-introspection.v1",
                    "event_type": "RequestIntrospection",
                },
                {
                    "topic": "onex.intent.platform.runtime-tick.v1",
                    "event_type": "RuntimeTick",
                },
            ]
        )
        assert check_contract(path) == []

    def test_no_published_events_section(self, write_contract):
        path = write_contract(None)
        assert check_contract(path) == []

    def test_empty_published_events(self, write_contract):
        path = write_contract([])
        assert check_contract(path) == []

    def test_entry_with_optional_description(self, write_contract):
        path = write_contract(
            [
                {
                    "topic": "onex.evt.platform.node-registered.v1",
                    "event_type": "NodeRegistered",
                    "description": "Emitted when a node is registered",
                }
            ]
        )
        assert check_contract(path) == []


class TestDuplicateEventTypes:
    """Contracts with duplicate event_type values."""

    def test_duplicate_event_type(self, write_contract):
        path = write_contract(
            [
                {
                    "topic": "onex.evt.platform.node-registered.v1",
                    "event_type": "NodeRegistered",
                },
                {
                    "topic": "onex.evt.platform.node-registered-v2.v1",
                    "event_type": "NodeRegistered",
                },
            ]
        )
        violations = check_contract(path)
        assert len(violations) == 1
        assert "duplicate event_type 'NodeRegistered'" in violations[0]
        assert "first seen at index 0" in violations[0]


class TestMissingFields:
    """Contracts with missing required fields."""

    def test_missing_topic(self, write_contract):
        path = write_contract([{"event_type": "NodeRegistered"}])
        violations = check_contract(path)
        assert len(violations) == 1
        assert "missing required field 'topic'" in violations[0]

    def test_missing_event_type(self, write_contract):
        path = write_contract([{"topic": "onex.evt.platform.node-registered.v1"}])
        violations = check_contract(path)
        assert len(violations) == 1
        assert "missing required field 'event_type'" in violations[0]

    def test_missing_both_fields(self, write_contract):
        path = write_contract([{}])
        violations = check_contract(path)
        assert len(violations) == 2
        assert any("missing required field 'topic'" in v for v in violations)
        assert any("missing required field 'event_type'" in v for v in violations)


class TestMalformedTopic:
    """Contracts with topic format violations."""

    def test_non_onex_topic(self, write_contract):
        path = write_contract(
            [
                {
                    "topic": "my-custom-topic",
                    "event_type": "CustomEvent",
                }
            ]
        )
        violations = check_contract(path)
        assert len(violations) == 1
        assert "does not match ONEX 5-segment format" in violations[0]

    def test_missing_version_segment(self, write_contract):
        path = write_contract(
            [
                {
                    "topic": "onex.evt.platform.node-registered",
                    "event_type": "NodeRegistered",
                }
            ]
        )
        violations = check_contract(path)
        assert len(violations) == 1
        assert "does not match ONEX 5-segment format" in violations[0]

    def test_invalid_kind(self, write_contract):
        path = write_contract(
            [
                {
                    "topic": "onex.query.platform.node-registered.v1",
                    "event_type": "NodeRegistered",
                }
            ]
        )
        violations = check_contract(path)
        assert len(violations) == 1
        assert "does not match ONEX 5-segment format" in violations[0]


class TestNonPascalCase:
    """Contracts with non-PascalCase event_type values."""

    def test_snake_case_event_type(self, write_contract):
        path = write_contract(
            [
                {
                    "topic": "onex.evt.platform.node-registered.v1",
                    "event_type": "node_registered",
                }
            ]
        )
        violations = check_contract(path)
        assert len(violations) == 1
        assert "not PascalCase" in violations[0]

    def test_camel_case_event_type(self, write_contract):
        path = write_contract(
            [
                {
                    "topic": "onex.evt.platform.node-registered.v1",
                    "event_type": "nodeRegistered",
                }
            ]
        )
        violations = check_contract(path)
        assert len(violations) == 1
        assert "not PascalCase" in violations[0]

    def test_single_char_event_type(self, write_contract):
        path = write_contract(
            [
                {
                    "topic": "onex.evt.platform.node-registered.v1",
                    "event_type": "N",
                }
            ]
        )
        violations = check_contract(path)
        assert len(violations) == 1
        assert "not PascalCase" in violations[0]


class TestMultipleViolations:
    """Contracts with multiple violation types."""

    def test_multiple_violations_in_one_contract(self, write_contract):
        path = write_contract(
            [
                {
                    "topic": "bad-topic",
                    "event_type": "snake_case",
                },
                {
                    "topic": "onex.evt.platform.node-registered.v1",
                    "event_type": "NodeRegistered",
                },
                {
                    "event_type": "NodeRegistered",
                },
            ]
        )
        violations = check_contract(path)
        # bad topic + non-PascalCase + missing topic + duplicate event_type
        assert len(violations) == 4

    def test_non_list_published_events(self, write_contract):
        path = write_contract({"not": "a list"})
        violations = check_contract(path)
        assert len(violations) == 1
        assert "must be a list" in violations[0]

    def test_non_dict_entry(self, write_contract):
        path = write_contract(["just a string"])
        violations = check_contract(path)
        assert len(violations) == 1
        assert "must be a mapping" in violations[0]
