# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for handler contract compliance scanner."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.enums.enum_compliance_verdict import EnumComplianceVerdict
from onex_change_control.enums.enum_compliance_violation import EnumComplianceViolation
from onex_change_control.scanners.handler_contract_compliance import (
    cross_reference,
    parse_contract_handler_routing,
    parse_contract_topics,
    parse_contract_transports,
    scan_handler_topics,
    scan_handler_transports,
    scan_node_py_logic,
)

# --- Fixtures ---


@pytest.fixture
def tmp_node_dir(tmp_path: Path) -> Path:
    """Create a minimal node directory structure under src/."""
    src = tmp_path / "src" / "test_repo" / "nodes" / "node_test"
    src.mkdir(parents=True)
    handlers = src / "handlers"
    handlers.mkdir()
    (handlers / "__init__.py").write_text("")
    return src


@pytest.fixture
def compliant_contract(tmp_node_dir: Path) -> Path:
    """Write a compliant contract.yaml."""
    contract = tmp_node_dir / "contract.yaml"
    contract.write_text(
        """
name: "node_test"
node_type: "EFFECT_GENERIC"
contract_version:
  major: 1
  minor: 0
  patch: 0
event_bus:
  publish_topics:
    - "onex.evt.test.computed.v1"
  subscribe_topics:
    - "onex.evt.test.requested.v1"
handler_routing:
  routing_strategy: "operation_match"
  handlers:
    - operation: "compute"
      handler:
        name: "HandlerTest"
        module: "test_repo.nodes.node_test.handlers.handler_test"
"""
    )
    return contract


@pytest.fixture
def compliant_handler(tmp_node_dir: Path) -> Path:
    """Write a compliant handler (no hardcoded topics, no undeclared transports)."""
    handler = tmp_node_dir / "handlers" / "handler_test.py"
    handler.write_text(
        '''
"""Handler for test compute operation."""

from __future__ import annotations


class HandlerTest:
    """Test handler - fully compliant."""

    async def handle(self, input_data: dict) -> dict:
        """Process input and return result."""
        return {"status": "ok"}
'''
    )
    return handler


@pytest.fixture
def imperative_handler(tmp_node_dir: Path) -> Path:
    """Write a handler with hardcoded topics and undeclared transports."""
    handler = tmp_node_dir / "handlers" / "handler_imperative.py"
    handler.write_text(
        '''
"""Imperative handler with violations."""

from __future__ import annotations

import httpx


class HandlerImperative:
    """Handler with contract violations."""

    async def handle(self, input_data: dict) -> dict:
        """Process with hardcoded topic and HTTP transport."""
        topic = "agent-actions"
        another = "onex.evt.bad.topic.v1"
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://example.com")
        return {"topic": topic, "another": another}
'''
    )
    return handler


@pytest.fixture
def clean_node_py(tmp_node_dir: Path) -> Path:
    """Write a clean declarative node.py."""
    node_py = tmp_node_dir / "node.py"
    node_py.write_text(
        '''
"""Declarative node."""

class NodeTest:
    """Clean declarative node."""

    def __init__(self, container):
        super().__init__(container)
'''
    )
    return node_py


@pytest.fixture
def logic_node_py(tmp_node_dir: Path) -> Path:
    """Write a node.py with custom logic."""
    node_py = tmp_node_dir / "node.py"
    node_py.write_text(
        '''
"""Node with custom logic."""

class NodeTest:
    """Node that has business logic (violation)."""

    def __init__(self, container):
        super().__init__(container)

    def process_data(self, data):
        """This should be in a handler."""
        return data

    def validate_input(self, input_data):
        """This should also be in a handler."""
        return True
'''
    )
    return node_py


# --- parse_contract_topics ---


class TestParseContractTopics:
    def test_extracts_topics(self, compliant_contract: Path) -> None:
        publish, subscribe = parse_contract_topics(compliant_contract)
        assert "onex.evt.test.computed.v1" in publish
        assert "onex.evt.test.requested.v1" in subscribe

    def test_missing_file(self, tmp_path: Path) -> None:
        publish, subscribe = parse_contract_topics(tmp_path / "nonexistent.yaml")
        assert publish == []
        assert subscribe == []

    def test_no_event_bus(self, tmp_node_dir: Path) -> None:
        contract = tmp_node_dir / "contract.yaml"
        contract.write_text("name: test\nnode_type: COMPUTE_GENERIC\n")
        publish, subscribe = parse_contract_topics(contract)
        assert publish == []
        assert subscribe == []


# --- parse_contract_transports ---


class TestParseContractTransports:
    def test_infers_kafka_from_topics(self, compliant_contract: Path) -> None:
        transports = parse_contract_transports(compliant_contract)
        assert "KAFKA" in transports

    def test_empty_for_no_transports(self, tmp_node_dir: Path) -> None:
        contract = tmp_node_dir / "contract.yaml"
        contract.write_text("name: test\nnode_type: COMPUTE_GENERIC\n")
        transports = parse_contract_transports(contract)
        assert transports == []


# --- parse_contract_handler_routing ---


class TestParseContractHandlerRouting:
    def test_extracts_routing(self, compliant_contract: Path) -> None:
        entries = parse_contract_handler_routing(compliant_contract)
        assert len(entries) == 1
        assert entries[0]["name"] == "HandlerTest"
        assert entries[0]["module"] == "test_repo.nodes.node_test.handlers.handler_test"


# --- scan_handler_topics ---


class TestScanHandlerTopics:
    def test_finds_hardcoded_topics(self, imperative_handler: Path) -> None:
        topics = scan_handler_topics(imperative_handler)
        assert "agent-actions" in topics
        assert "onex.evt.bad.topic.v1" in topics

    def test_no_topics_in_compliant(self, compliant_handler: Path) -> None:
        topics = scan_handler_topics(compliant_handler)
        assert topics == []

    def test_skips_docstrings(self, tmp_path: Path) -> None:
        handler = tmp_path / "handler_docstring.py"
        handler.write_text(
            '''
"""This handler uses agent-actions topic."""

class Handler:
    """Processes onex.evt.test.computed.v1 events."""
    pass
'''
        )
        topics = scan_handler_topics(handler)
        assert topics == []


# --- scan_handler_transports ---


class TestScanHandlerTransports:
    def test_detects_http_transport(self, imperative_handler: Path) -> None:
        transports = scan_handler_transports(imperative_handler)
        assert "HTTP" in transports

    def test_no_transports_in_compliant(self, compliant_handler: Path) -> None:
        transports = scan_handler_transports(compliant_handler)
        assert transports == []

    def test_detects_database_transport(self, tmp_path: Path) -> None:
        handler = tmp_path / "handler_db.py"
        handler.write_text(
            """
import asyncpg

async def query():
    conn = await asyncpg.connect()
"""
        )
        transports = scan_handler_transports(handler)
        assert "DATABASE" in transports


# --- scan_node_py_logic ---


class TestScanNodePyLogic:
    def test_clean_node(self, clean_node_py: Path) -> None:
        methods = scan_node_py_logic(clean_node_py)
        assert methods == []

    def test_logic_in_node(self, logic_node_py: Path) -> None:
        methods = scan_node_py_logic(logic_node_py)
        assert "process_data" in methods
        assert "validate_input" in methods

    def test_missing_file(self, tmp_path: Path) -> None:
        methods = scan_node_py_logic(tmp_path / "nonexistent.py")
        assert methods == []


# --- cross_reference ---


class TestCrossReference:
    def test_compliant_handler(
        self,
        tmp_node_dir: Path,
        compliant_contract: Path,  # noqa: ARG002
        compliant_handler: Path,  # noqa: ARG002
        clean_node_py: Path,  # noqa: ARG002
    ) -> None:
        results = cross_reference(tmp_node_dir, "test_repo")
        assert len(results) == 1
        assert results[0].verdict == EnumComplianceVerdict.COMPLIANT
        assert results[0].violations == []

    def test_imperative_handler(
        self,
        tmp_node_dir: Path,
        compliant_contract: Path,  # noqa: ARG002
        imperative_handler: Path,  # noqa: ARG002
        clean_node_py: Path,  # noqa: ARG002
    ) -> None:
        results = cross_reference(tmp_node_dir, "test_repo")
        violating = [
            r for r in results if r.handler_path.endswith("handler_imperative.py")
        ]
        assert len(violating) == 1
        r = violating[0]
        assert r.verdict == EnumComplianceVerdict.IMPERATIVE
        assert EnumComplianceViolation.HARDCODED_TOPIC in r.violations
        assert EnumComplianceViolation.UNDECLARED_TRANSPORT in r.violations

    def test_missing_contract(self, tmp_node_dir: Path) -> None:
        handler = tmp_node_dir / "handlers" / "handler_orphan.py"
        handler.write_text("class HandlerOrphan: pass\n")
        results = cross_reference(tmp_node_dir, "test_repo")
        assert len(results) == 1
        assert results[0].verdict == EnumComplianceVerdict.MISSING_CONTRACT

    def test_logic_in_node_reported(
        self,
        tmp_node_dir: Path,
        compliant_contract: Path,  # noqa: ARG002
        compliant_handler: Path,  # noqa: ARG002
        logic_node_py: Path,  # noqa: ARG002
    ) -> None:
        results = cross_reference(tmp_node_dir, "test_repo")
        assert len(results) == 1
        assert EnumComplianceViolation.LOGIC_IN_NODE in results[0].violations

    def test_allowlisted_handler(
        self,
        tmp_node_dir: Path,
        compliant_contract: Path,  # noqa: ARG002
        imperative_handler: Path,  # noqa: ARG002
    ) -> None:
        handler_path = str(
            (tmp_node_dir / "handlers" / "handler_imperative.py").relative_to(
                tmp_node_dir.parent.parent.parent
            )
        )
        results = cross_reference(
            tmp_node_dir,
            "test_repo",
            allowlisted_paths=frozenset({handler_path}),
        )
        imperative = [
            r for r in results if r.handler_path.endswith("handler_imperative.py")
        ]
        assert len(imperative) == 1
        assert imperative[0].verdict == EnumComplianceVerdict.ALLOWLISTED
        assert imperative[0].allowlisted is True
