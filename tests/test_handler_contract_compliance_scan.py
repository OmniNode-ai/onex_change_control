# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Integration tests for the handler-contract compliance scanner (OMN-6844).

Validates the cross_reference scanner against synthetic node directories
with known compliance states. Each test creates a minimal node directory
with a handler and optionally a contract.yaml, then verifies the scanner
produces the expected verdict and violations.

Key scanner behaviors validated:
- MISSING_HANDLER_ROUTING fires when handler module is not in contract routing
- HARDCODED_TOPIC fires for any topic string literal (even if contract-declared)
- >=2 violations -> IMPERATIVE, 1 violation -> HYBRID, 0 -> COMPLIANT
- LOGIC_IN_NODE fires when node.py has custom methods beyond __init__
- Allowlisted handlers get ALLOWLISTED verdict regardless of violations
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.enums.enum_compliance_verdict import EnumComplianceVerdict
from onex_change_control.enums.enum_compliance_violation import EnumComplianceViolation
from onex_change_control.scanners.handler_contract_compliance import cross_reference

pytestmark = pytest.mark.unit


def _create_node_dir(  # noqa: PLR0913
    tmp_path: Path,
    *,
    contract_yaml: str | None = None,
    handler_code: str = "",
    handler_module: str = "handler_example",
    node_code: str | None = None,
    add_handler_routing: bool = False,
) -> Path:
    """Create a synthetic node directory for testing.

    Uses a src/ layout so _infer_module_path resolves correctly:
      tmp_path/src/test_pkg/nodes/node_test/handlers/handler_example.py
      -> module: test_pkg.nodes.node_test.handlers.handler_example

    Args:
        tmp_path: Pytest tmp_path fixture.
        contract_yaml: Contents of contract.yaml. If None, no contract is created.
        handler_code: Contents of the handler file.
        handler_module: Module name for the handler file (without .py).
        node_code: Contents of node.py. If None, a minimal
            declarative node is created.
        add_handler_routing: If True, adds handler_routing to
            contract pointing to handler.

    Returns:
        Path to the node directory.
    """
    node_dir = tmp_path / "src" / "test_pkg" / "nodes" / "node_test"
    node_dir.mkdir(parents=True)
    module_prefix = "test_pkg.nodes.node_test"

    if contract_yaml is not None:
        yaml_content = textwrap.dedent(contract_yaml)
        if add_handler_routing:
            yaml_content += textwrap.dedent(f"""\
                handler_routing:
                  routing_strategy: "operation_match"
                  handlers:
                    - handler:
                        name: "HandlerExample"
                        module: "{module_prefix}.handlers.{handler_module}"
                      operation: "example"
            """)
        (node_dir / "contract.yaml").write_text(yaml_content)

    handlers_dir = node_dir / "handlers"
    handlers_dir.mkdir()
    (handlers_dir / "__init__.py").write_text("")
    (handlers_dir / f"{handler_module}.py").write_text(textwrap.dedent(handler_code))

    node_py_content = (
        textwrap.dedent(node_code)
        if node_code
        else textwrap.dedent("""\
        class NodeTest:
            def __init__(self, container):
                super().__init__(container)
    """)
    )
    (node_dir / "node.py").write_text(node_py_content)

    return node_dir


class TestComplianceVerdictCompliant:
    """Handlers that fully match their contract should be COMPLIANT."""

    def test_handler_registered_in_routing_no_violations(self, tmp_path: Path) -> None:
        """Handler with no topics/transports AND registered in routing is COMPLIANT."""
        node_dir = _create_node_dir(
            tmp_path,
            contract_yaml="""\
                name: node_test
                node_type: COMPUTE_GENERIC
                contract_version: {major: 1, minor: 0, patch: 0}
                node_version: "1.0.0"
                description: Test node
                input_model: {name: ModelTestInput, module: test}
                output_model: {name: ModelTestOutput, module: test}
            """,
            handler_code="""\
                def handle(self, data):
                    return {"result": True}
            """,
            add_handler_routing=True,
        )
        results = cross_reference(node_dir, repo="test_repo")
        assert len(results) == 1
        assert results[0].verdict == EnumComplianceVerdict.COMPLIANT
        assert results[0].violations == []


class TestComplianceVerdictHybrid:
    """Handlers with exactly 1 violation should be HYBRID."""

    def test_unregistered_handler_is_hybrid(self, tmp_path: Path) -> None:
        """Handler not in routing but otherwise clean has 1 violation -> HYBRID."""
        node_dir = _create_node_dir(
            tmp_path,
            contract_yaml="""\
                name: node_test
                node_type: COMPUTE_GENERIC
                contract_version: {major: 1, minor: 0, patch: 0}
                node_version: "1.0.0"
                description: Test node
                input_model: {name: ModelTestInput, module: test}
                output_model: {name: ModelTestOutput, module: test}
            """,
            handler_code="""\
                def handle(self, data):
                    return {"result": True}
            """,
        )
        results = cross_reference(node_dir, repo="test_repo")
        assert len(results) == 1
        assert results[0].verdict == EnumComplianceVerdict.HYBRID
        assert EnumComplianceViolation.MISSING_HANDLER_ROUTING in results[0].violations
        assert len(results[0].violations) == 1


class TestComplianceVerdictImperative:
    """Handlers with >=2 violations should be IMPERATIVE."""

    def test_hardcoded_topic_plus_missing_routing(self, tmp_path: Path) -> None:
        """Hardcoded topic + missing routing = 2+ violations -> IMPERATIVE."""
        node_dir = _create_node_dir(
            tmp_path,
            contract_yaml="""\
                name: node_test
                node_type: EFFECT_GENERIC
                contract_version: {major: 1, minor: 0, patch: 0}
                node_version: "1.0.0"
                description: Test node
                input_model: {name: ModelTestInput, module: test}
                output_model: {name: ModelTestOutput, module: test}
            """,
            handler_code="""\
                TOPIC = "onex.evt.platform.undeclared-topic.v1"
                def handle(self, data):
                    self.publish(TOPIC, data)
            """,
        )
        results = cross_reference(node_dir, repo="test_repo")
        assert len(results) == 1
        assert results[0].verdict == EnumComplianceVerdict.IMPERATIVE
        assert EnumComplianceViolation.HARDCODED_TOPIC in results[0].violations
        assert EnumComplianceViolation.MISSING_HANDLER_ROUTING in results[0].violations
        assert "onex.evt.platform.undeclared-topic.v1" in results[0].undeclared_topics

    def test_declared_topic_still_hardcoded(self, tmp_path: Path) -> None:
        """Even if topic is in contract, using string literal = HARDCODED_TOPIC."""
        node_dir = _create_node_dir(
            tmp_path,
            contract_yaml="""\
                name: node_test
                node_type: EFFECT_GENERIC
                contract_version: {major: 1, minor: 0, patch: 0}
                node_version: "1.0.0"
                description: Test node
                input_model: {name: ModelTestInput, module: test}
                output_model: {name: ModelTestOutput, module: test}
                event_bus:
                  publish_topics:
                    - "onex.evt.platform.test-event.v1"
            """,
            handler_code="""\
                TOPIC = "onex.evt.platform.test-event.v1"
                def handle(self, data):
                    self.publish(TOPIC, data)
            """,
        )
        results = cross_reference(node_dir, repo="test_repo")
        assert len(results) == 1
        assert EnumComplianceViolation.HARDCODED_TOPIC in results[0].violations


class TestComplianceVerdictMissingContract:
    """Handlers without a contract.yaml should be MISSING_CONTRACT."""

    def test_no_contract_yaml(self, tmp_path: Path) -> None:
        """Missing contract.yaml produces MISSING_CONTRACT verdict."""
        node_dir = _create_node_dir(
            tmp_path,
            contract_yaml=None,
            handler_code="""\
                def handle(self, data):
                    return data
            """,
        )
        results = cross_reference(node_dir, repo="test_repo")
        assert len(results) == 1
        assert results[0].verdict == EnumComplianceVerdict.MISSING_CONTRACT


class TestComplianceVerdictAllowlisted:
    """Handlers in the allowlist should be ALLOWLISTED."""

    def test_allowlisted_handler_with_violations(self, tmp_path: Path) -> None:
        """Allowlisted handler gets ALLOWLISTED verdict regardless of violations."""
        node_dir = _create_node_dir(
            tmp_path,
            contract_yaml="""\
                name: node_test
                node_type: EFFECT_GENERIC
                contract_version: {major: 1, minor: 0, patch: 0}
                node_version: "1.0.0"
                description: Test node
                input_model: {name: ModelTestInput, module: test}
                output_model: {name: ModelTestOutput, module: test}
            """,
            handler_code="""\
                TOPIC = "onex.evt.platform.undeclared-topic.v1"
                def handle(self, data):
                    self.publish(TOPIC, data)
            """,
        )
        # The allowlist uses rel_path as computed by _audit_handler:
        # relative to node_dir.parent.parent.parent (base_dir).
        base_dir = node_dir.parent.parent.parent
        rel_handler = str(
            (node_dir / "handlers" / "handler_example.py").relative_to(base_dir)
        )
        results = cross_reference(
            node_dir,
            repo="test_repo",
            allowlisted_paths=frozenset({rel_handler}),
        )
        assert len(results) == 1
        assert results[0].verdict == EnumComplianceVerdict.ALLOWLISTED
        assert results[0].allowlisted is True


class TestComplianceScannerEdgeCases:
    """Edge cases for the compliance scanner."""

    def test_empty_handlers_dir(self, tmp_path: Path) -> None:
        """Node with no handler files returns empty results."""
        node_dir = tmp_path / "node_empty"
        node_dir.mkdir()
        (node_dir / "contract.yaml").write_text("name: node_empty\n")
        handlers_dir = node_dir / "handlers"
        handlers_dir.mkdir()
        (handlers_dir / "__init__.py").write_text("")
        (node_dir / "node.py").write_text("class Node: pass\n")

        results = cross_reference(node_dir, repo="test_repo")
        assert results == []

    def test_no_handlers_dir(self, tmp_path: Path) -> None:
        """Node without a handlers/ directory returns empty results."""
        node_dir = tmp_path / "node_nohandlers"
        node_dir.mkdir()
        (node_dir / "contract.yaml").write_text("name: node_nohandlers\n")
        (node_dir / "node.py").write_text("class Node: pass\n")

        results = cross_reference(node_dir, repo="test_repo")
        assert results == []

    def test_logic_in_node_detected(self, tmp_path: Path) -> None:
        """Custom methods in node.py produce LOGIC_IN_NODE in violations."""
        node_dir = _create_node_dir(
            tmp_path,
            contract_yaml="""\
                name: node_test
                node_type: COMPUTE_GENERIC
                contract_version: {major: 1, minor: 0, patch: 0}
                node_version: "1.0.0"
                description: Test node
                input_model: {name: ModelTestInput, module: test}
                output_model: {name: ModelTestOutput, module: test}
            """,
            handler_code="""\
                def handle(self, data):
                    return data
            """,
            node_code="""\
                class NodeTest:
                    def __init__(self, container):
                        super().__init__(container)
                    def custom_business_logic(self, data):
                        return data.transform()
                    def another_method(self, x):
                        return x * 2
            """,
        )
        results = cross_reference(node_dir, repo="test_repo")
        assert len(results) == 1
        # Should have both LOGIC_IN_NODE and MISSING_HANDLER_ROUTING
        violations = results[0].violations
        assert EnumComplianceViolation.LOGIC_IN_NODE in violations
        assert EnumComplianceViolation.MISSING_HANDLER_ROUTING in violations
