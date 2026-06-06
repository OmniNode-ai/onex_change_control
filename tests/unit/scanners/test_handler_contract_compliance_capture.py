# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Equivalence tests for scanners/handler_contract_compliance.py (OMN-12177).

Captures current pass/fail behavior as a regression baseline before refactoring.
Does NOT modify the scanner. Tests all public functions directly using
representative fixture files.

Covered functions:
    parse_contract_topics        — extract publish/subscribe from contract.yaml
    parse_contract_transports    — extract declared transports
    parse_contract_handler_routing — extract handler routing entries
    scan_handler_topics          — detect hardcoded topic strings in handler AST
    scan_handler_transports      — detect transport call-sites in handler AST
    scan_node_py_logic           — detect custom methods in node.py
    cross_reference              — full audit of a node directory
    _determine_verdict           — verdict from violation list

Pass cases (compliant):
    - Handler with no hardcoded topics, transport declared, in routing
    - Node.py with only __init__
    - Contract with EFFECT node that has topics (KAFKA inferred)

Fail cases (violations):
    - Handler with hardcoded topic string literal
    - Handler using undeclared transport (HTTP without declaration)
    - Handler not listed in handler_routing
    - Node.py with custom methods beyond __init__
    - Missing contract.yaml produces MISSING_CONTRACT verdict

Boundary cases:
    - Allowlisted handler gets ALLOWLISTED verdict regardless of violations
    - Two violations threshold → IMPERATIVE verdict
    - One violation → HYBRID verdict
    - Zero violations → COMPLIANT verdict
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.enums.enum_compliance_verdict import EnumComplianceVerdict
from onex_change_control.enums.enum_compliance_violation import EnumComplianceViolation
from onex_change_control.scanners.handler_contract_compliance import (
    _determine_verdict,
    cross_reference,
    parse_contract_handler_routing,
    parse_contract_topics,
    parse_contract_transports,
    scan_handler_topics,
    scan_handler_transports,
    scan_node_py_logic,
)

# ---------------------------------------------------------------------------
# Shared node-directory scaffold
# ---------------------------------------------------------------------------


def _make_node_dir(tmp_path: Path, repo: str = "test_repo") -> Path:
    """Create minimal node directory structure under src/."""
    node_dir = tmp_path / "src" / repo / "nodes" / "node_test"
    node_dir.mkdir(parents=True)
    handlers_dir = node_dir / "handlers"
    handlers_dir.mkdir()
    (handlers_dir / "__init__.py").write_text("")
    return node_dir


def _write_contract(node_dir: Path, content: str) -> Path:
    contract = node_dir / "contract.yaml"
    contract.write_text(content)
    return contract


def _write_handler(node_dir: Path, name: str, content: str) -> Path:
    handler = node_dir / "handlers" / name
    handler.write_text(content)
    return handler


def _write_node_py(node_dir: Path, content: str) -> Path:
    node_py = node_dir / "node.py"
    node_py.write_text(content)
    return node_py


_COMPLIANT_CONTRACT = """\
name: node_test
node_type: EFFECT_GENERIC
event_bus:
  publish_topics:
    - onex.evt.test.computed.v1
  subscribe_topics:
    - onex.evt.test.requested.v1
handler_routing:
  routing_strategy: operation_match
  handlers:
    - operation: compute
      handler:
        name: HandlerTest
        module: test_repo.nodes.node_test.handlers.handler_test
"""

_COMPLIANT_HANDLER = '''\
"""Compliant handler."""

from __future__ import annotations


class HandlerTest:
    def handle(self, event: dict) -> dict:
        return {"status": "ok"}
'''

_CLEAN_NODE_PY = '''\
"""Node module."""

from __future__ import annotations


class NodeTest:
    def __init__(self, container):
        super().__init__(container)
'''


# ---------------------------------------------------------------------------
# parse_contract_topics
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_contract_topics_returns_publish_and_subscribe(tmp_path: Path) -> None:
    """Publish and subscribe topics are extracted from event_bus section."""
    node_dir = _make_node_dir(tmp_path)
    _write_contract(node_dir, _COMPLIANT_CONTRACT)
    pub, sub = parse_contract_topics(node_dir / "contract.yaml")
    assert "onex.evt.test.computed.v1" in pub
    assert "onex.evt.test.requested.v1" in sub


@pytest.mark.unit
def test_parse_contract_topics_missing_contract_returns_empty(tmp_path: Path) -> None:
    """Missing contract.yaml returns empty lists."""
    pub, sub = parse_contract_topics(tmp_path / "nonexistent.yaml")
    assert pub == []
    assert sub == []


@pytest.mark.unit
def test_parse_contract_topics_dict_form_supported(tmp_path: Path) -> None:
    """Topics specified as {'topic': 'name'} dicts are normalized to strings."""
    node_dir = _make_node_dir(tmp_path)
    _write_contract(
        node_dir,
        "event_bus:\n  publish_topics:\n    - topic: onex.evt.foo.bar.v1\n",
    )
    pub, _ = parse_contract_topics(node_dir / "contract.yaml")
    assert "onex.evt.foo.bar.v1" in pub


# ---------------------------------------------------------------------------
# parse_contract_transports
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_contract_transports_effect_with_topics_infers_kafka(
    tmp_path: Path,
) -> None:
    """EFFECT node with declared topics gets KAFKA inferred."""
    node_dir = _make_node_dir(tmp_path)
    _write_contract(
        node_dir,
        "node_type: EFFECT_GENERIC\n"
        "event_bus:\n"
        "  publish_topics:\n"
        "    - onex.evt.test.computed.v1\n",
    )
    transports = parse_contract_transports(node_dir / "contract.yaml")
    assert "KAFKA" in transports


@pytest.mark.unit
def test_parse_contract_transports_explicit_metadata_transport(tmp_path: Path) -> None:
    """metadata.transport_type is extracted and uppercased."""
    node_dir = _make_node_dir(tmp_path)
    _write_contract(node_dir, "metadata:\n  transport_type: http\n")
    transports = parse_contract_transports(node_dir / "contract.yaml")
    assert "HTTP" in transports


@pytest.mark.unit
def test_parse_contract_transports_missing_contract_returns_empty(
    tmp_path: Path,
) -> None:
    """Missing contract.yaml returns empty list."""
    transports = parse_contract_transports(tmp_path / "nonexistent.yaml")
    assert transports == []


# ---------------------------------------------------------------------------
# parse_contract_handler_routing
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_contract_handler_routing_returns_entries(tmp_path: Path) -> None:
    """Handler routing entries are extracted with name, module, operation."""
    node_dir = _make_node_dir(tmp_path)
    _write_contract(node_dir, _COMPLIANT_CONTRACT)
    entries = parse_contract_handler_routing(node_dir / "contract.yaml")
    assert len(entries) == 1
    assert entries[0]["module"] == "test_repo.nodes.node_test.handlers.handler_test"
    assert entries[0]["operation"] == "compute"


@pytest.mark.unit
def test_parse_contract_handler_routing_empty_when_no_section(
    tmp_path: Path,
) -> None:
    """No handler_routing section returns empty list."""
    node_dir = _make_node_dir(tmp_path)
    _write_contract(node_dir, "name: node_test\n")
    entries = parse_contract_handler_routing(node_dir / "contract.yaml")
    assert entries == []


# ---------------------------------------------------------------------------
# scan_handler_topics
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_handler_topics_no_hardcoded_topics_returns_empty(
    tmp_path: Path,
) -> None:
    """Handler with no hardcoded topic strings returns empty list."""
    handler = tmp_path / "handler_clean.py"
    handler.write_text(_COMPLIANT_HANDLER)
    topics = scan_handler_topics(handler)
    assert topics == []


@pytest.mark.unit
def test_scan_handler_topics_detects_onex_topic_literal(tmp_path: Path) -> None:
    """Handler with a hardcoded onex.evt.* string returns that topic."""
    handler = tmp_path / "handler_bad.py"
    handler.write_text(
        'def handle():\n    topic = "onex.evt.test.foo.v1"\n    return topic\n'
    )
    topics = scan_handler_topics(handler)
    assert "onex.evt.test.foo.v1" in topics


@pytest.mark.unit
def test_scan_handler_topics_skips_docstrings(tmp_path: Path) -> None:
    """Topic strings that appear only in docstrings are NOT flagged."""
    handler = tmp_path / "handler_docstring.py"
    handler.write_text(
        '"""Handler that publishes to onex.evt.test.foo.v1."""\n\ndef handle(): pass\n'
    )
    topics = scan_handler_topics(handler)
    assert topics == []


@pytest.mark.unit
def test_scan_handler_topics_detects_bare_topic_name(tmp_path: Path) -> None:
    """Known bare topic names (like 'agent-actions') are detected."""
    handler = tmp_path / "handler_bare.py"
    handler.write_text('def handle():\n    t = "agent-actions"\n    return t\n')
    topics = scan_handler_topics(handler)
    assert "agent-actions" in topics


# ---------------------------------------------------------------------------
# scan_handler_transports
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_handler_transports_clean_handler_returns_empty(tmp_path: Path) -> None:
    """Handler with no transport call-sites returns empty list."""
    handler = tmp_path / "handler_clean.py"
    handler.write_text(_COMPLIANT_HANDLER)
    transports = scan_handler_transports(handler)
    assert transports == []


@pytest.mark.unit
def test_scan_handler_transports_detects_http_client(tmp_path: Path) -> None:
    """httpx.get call-site is detected as HTTP transport."""
    handler = tmp_path / "handler_http.py"
    handler.write_text(
        "import httpx\ndef handle():\n    resp = httpx.get('http://example.com')\n"
    )
    transports = scan_handler_transports(handler)
    assert "HTTP" in transports


@pytest.mark.unit
def test_scan_handler_transports_detects_kafka_producer(tmp_path: Path) -> None:
    """KafkaProducer call-site is detected as KAFKA transport."""
    handler = tmp_path / "handler_kafka.py"
    handler.write_text(
        "from kafka import KafkaProducer\n"
        "def handle():\n    p = KafkaProducer(bootstrap_servers='localhost:9092')\n"
    )
    transports = scan_handler_transports(handler)
    assert "KAFKA" in transports


@pytest.mark.unit
def test_scan_handler_transports_import_only_not_counted(tmp_path: Path) -> None:
    """Import-only (no call-site) transport reference is not flagged."""
    handler = tmp_path / "handler_import_only.py"
    handler.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import httpx\n"
        "def handle(): pass\n"
    )
    transports = scan_handler_transports(handler)
    assert "HTTP" not in transports


# ---------------------------------------------------------------------------
# scan_node_py_logic
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scan_node_py_logic_only_init_returns_empty(tmp_path: Path) -> None:
    """node.py with only __init__ returns no custom methods."""
    node_py = tmp_path / "node.py"
    node_py.write_text(_CLEAN_NODE_PY)
    methods = scan_node_py_logic(node_py)
    assert methods == []


@pytest.mark.unit
def test_scan_node_py_logic_detects_custom_methods(tmp_path: Path) -> None:
    """node.py with custom methods beyond __init__ returns those method names."""
    node_py = tmp_path / "node.py"
    node_py.write_text(
        "class NodeTest:\n"
        "    def __init__(self, c): super().__init__(c)\n"
        "    def _process(self): pass\n"
        "    def validate(self): pass\n"
    )
    methods = scan_node_py_logic(node_py)
    assert "_process" in methods
    assert "validate" in methods
    assert "__init__" not in methods


@pytest.mark.unit
def test_scan_node_py_logic_missing_file_returns_empty(tmp_path: Path) -> None:
    """Missing node.py returns empty list (does not crash)."""
    methods = scan_node_py_logic(tmp_path / "nonexistent.py")
    assert methods == []


# ---------------------------------------------------------------------------
# _determine_verdict
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_determine_verdict_no_violations_compliant() -> None:
    """Zero violations → COMPLIANT."""
    verdict = _determine_verdict([], is_allowlisted=False)
    assert verdict == EnumComplianceVerdict.COMPLIANT


@pytest.mark.unit
def test_determine_verdict_one_violation_hybrid() -> None:
    """One violation → HYBRID."""
    verdict = _determine_verdict(
        [EnumComplianceViolation.HARDCODED_TOPIC], is_allowlisted=False
    )
    assert verdict == EnumComplianceVerdict.HYBRID


@pytest.mark.unit
def test_determine_verdict_two_violations_imperative() -> None:
    """Two or more violations → IMPERATIVE."""
    verdict = _determine_verdict(
        [
            EnumComplianceViolation.HARDCODED_TOPIC,
            EnumComplianceViolation.MISSING_HANDLER_ROUTING,
        ],
        is_allowlisted=False,
    )
    assert verdict == EnumComplianceVerdict.IMPERATIVE


@pytest.mark.unit
def test_determine_verdict_allowlisted_overrides() -> None:
    """Allowlisted handler always gets ALLOWLISTED regardless of violations."""
    verdict = _determine_verdict(
        [
            EnumComplianceViolation.HARDCODED_TOPIC,
            EnumComplianceViolation.UNDECLARED_TRANSPORT,
        ],
        is_allowlisted=True,
    )
    assert verdict == EnumComplianceVerdict.ALLOWLISTED


# ---------------------------------------------------------------------------
# cross_reference — end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cross_reference_compliant_handler(tmp_path: Path) -> None:
    """Fully compliant node+handler produces COMPLIANT verdict."""
    node_dir = _make_node_dir(tmp_path)
    _write_contract(node_dir, _COMPLIANT_CONTRACT)
    _write_handler(node_dir, "handler_test.py", _COMPLIANT_HANDLER)
    results = cross_reference(node_dir=node_dir, repo="test_repo")
    assert results
    assert all(r.verdict == EnumComplianceVerdict.COMPLIANT for r in results)


@pytest.mark.unit
def test_cross_reference_no_handlers_dir_returns_empty(tmp_path: Path) -> None:
    """Node directory without a handlers/ subdir returns empty results."""
    node_dir = _make_node_dir(tmp_path)
    _write_contract(node_dir, _COMPLIANT_CONTRACT)
    # Remove handlers dir
    import shutil

    shutil.rmtree(node_dir / "handlers")
    results = cross_reference(node_dir=node_dir, repo="test_repo")
    assert results == []


@pytest.mark.unit
def test_cross_reference_missing_contract_gives_missing_contract_verdict(
    tmp_path: Path,
) -> None:
    """Handler without a contract.yaml gets MISSING_CONTRACT verdict."""
    node_dir = _make_node_dir(tmp_path)
    _write_handler(node_dir, "handler_test.py", _COMPLIANT_HANDLER)
    results = cross_reference(node_dir=node_dir, repo="test_repo")
    assert results
    assert all(r.verdict == EnumComplianceVerdict.MISSING_CONTRACT for r in results)


@pytest.mark.unit
def test_cross_reference_hardcoded_topic_is_violation(tmp_path: Path) -> None:
    """Handler with a hardcoded topic string produces HARDCODED_TOPIC violation."""
    node_dir = _make_node_dir(tmp_path)
    _write_contract(node_dir, _COMPLIANT_CONTRACT)
    _write_handler(
        node_dir,
        "handler_test.py",
        'def handle():\n    t = "onex.evt.test.computed.v1"\n    return t\n',
    )
    results = cross_reference(node_dir=node_dir, repo="test_repo")
    assert results
    violations = [v for r in results for v in r.violations]
    assert EnumComplianceViolation.HARDCODED_TOPIC in violations


@pytest.mark.unit
def test_cross_reference_handler_not_in_routing_is_violation(tmp_path: Path) -> None:
    """Handler not listed in contract handler_routing gets MISSING_HANDLER_ROUTING."""
    node_dir = _make_node_dir(tmp_path)
    _write_contract(
        node_dir,
        "name: node_test\n"
        "event_bus:\n  publish_topics:\n    - onex.evt.test.computed.v1\n"
        "handler_routing:\n  handlers: []\n",
    )
    _write_handler(node_dir, "handler_test.py", _COMPLIANT_HANDLER)
    results = cross_reference(node_dir=node_dir, repo="test_repo")
    assert results
    violations = [v for r in results for v in r.violations]
    assert EnumComplianceViolation.MISSING_HANDLER_ROUTING in violations


@pytest.mark.unit
def test_cross_reference_allowlisted_path_gets_allowlisted_verdict(
    tmp_path: Path,
) -> None:
    """Handler in allowlisted_paths set receives ALLOWLISTED verdict."""
    node_dir = _make_node_dir(tmp_path)
    _write_contract(node_dir, _COMPLIANT_CONTRACT)
    _write_handler(
        node_dir,
        "handler_test.py",
        'def handle():\n    t = "onex.evt.test.computed.v1"\n',
    )
    # Build the rel_path the scanner would compute
    base_dir = node_dir.parent.parent.parent
    handler_path = node_dir / "handlers" / "handler_test.py"
    rel = str(handler_path.relative_to(base_dir))

    results = cross_reference(
        node_dir=node_dir,
        repo="test_repo",
        allowlisted_paths=frozenset({rel}),
    )
    assert results
    assert all(r.verdict == EnumComplianceVerdict.ALLOWLISTED for r in results)
