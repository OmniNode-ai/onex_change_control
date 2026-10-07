# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for freestanding imperative-IO detection.

Freestanding modules are source files under ``src/`` that are NOT node
handlers (not under ``node_*/handlers/``). The node-contract scanner never
sees them, so these tests pin the AST-based detector that does.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from onex_change_control.enums.enum_compliance_verdict import EnumComplianceVerdict
from onex_change_control.enums.enum_compliance_violation import EnumComplianceViolation
from onex_change_control.scanners.handler_contract_compliance import (
    scan_freestanding_imperative_io,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def _violations(result: object) -> list[EnumComplianceViolation]:
    return [f.violation for f in result.active_findings]  # type: ignore[attr-defined]


def test_consumer_style_module_is_flagged(tmp_path: Path) -> None:
    """The keystone case: raw httpx.post with a hardcoded max_tokens literal."""
    module = _write(
        tmp_path / "consumer.py",
        '''
        """Inference consumer."""

        import httpx


        def run_inference(endpoint: str, prompt: str) -> str:
            response = httpx.post(
                endpoint,
                json={"prompt": prompt, "max_tokens": 2048},
                timeout=30,
            )
            return response.json()["choices"][0]["message"]["content"]
        ''',
    )

    result = scan_freestanding_imperative_io(module, repo="sea")

    violations = _violations(result)
    assert EnumComplianceViolation.RAW_HTTP_INFERENCE in violations
    assert EnumComplianceViolation.HARDCODED_CONFIG in violations
    assert result.verdict == EnumComplianceVerdict.IMPERATIVE


def test_clean_module_is_not_flagged(tmp_path: Path) -> None:
    """A pure compute module with no IO must produce zero findings."""
    module = _write(
        tmp_path / "scoring.py",
        '''
        """Pure scoring helpers."""

        from __future__ import annotations


        def score(values: list[int]) -> float:
            return sum(values) / len(values) if values else 0.0
        ''',
    )

    result = scan_freestanding_imperative_io(module, repo="sea")

    assert result.active_findings == []
    assert result.verdict == EnumComplianceVerdict.COMPLIANT


def test_direct_db_connection_flagged(tmp_path: Path) -> None:
    module = _write(
        tmp_path / "store.py",
        """
        import asyncpg


        async def open_conn(dsn: str) -> object:
            return await asyncpg.connect(dsn)
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.DIRECT_DB in _violations(result)


def test_raw_kafka_producer_flagged(tmp_path: Path) -> None:
    module = _write(
        tmp_path / "publisher.py",
        """
        from aiokafka import AIOKafkaProducer


        def make() -> object:
            return AIOKafkaProducer(bootstrap_servers="localhost:9092")
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.RAW_KAFKA in _violations(result)


def test_subprocess_network_flagged(tmp_path: Path) -> None:
    module = _write(
        tmp_path / "deployer.py",
        """
        import subprocess


        def push(remote: str) -> None:
            subprocess.run(["scp", "artifact", remote], check=True)
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.SUBPROCESS_NETWORK in _violations(result)


def test_subprocess_ssh_flagged(tmp_path: Path) -> None:
    """ssh is a network subprocess op."""
    module = _write(
        tmp_path / "remote.py",
        """
        import subprocess


        def exec_remote(host: str) -> None:
            subprocess.run(["ssh", host, "uptime"], check=True)
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.SUBPROCESS_NETWORK in _violations(result)


def test_subprocess_local_git_rev_parse_not_flagged(tmp_path: Path) -> None:
    """Local git plumbing (rev-parse) is NOT a network op.

    Pins the OMN-12540 false-positive fix: a git commit-SHA provenance read
    (``git rev-parse HEAD``) must not be flagged as subprocess_network. Mirror
    of onex-self-extending-agent/src/__main__.py git provenance.
    """
    module = _write(
        tmp_path / "provenance.py",
        """
        import subprocess


        def commit_sha() -> str:
            return subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip()
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.SUBPROCESS_NETWORK not in _violations(result)
    assert result.verdict == EnumComplianceVerdict.COMPLIANT


@pytest.mark.parametrize(
    "argv",
    [
        '["git", "log", "--oneline", "-1"]',
        '["git", "describe", "--tags"]',
        '["git", "status", "--porcelain"]',
        '["git", "branch", "--show-current"]',
        '["git", "show", "HEAD:file"]',
        '["git", "diff", "--name-only"]',
        '["git", "-C", "/repo", "rev-parse", "HEAD"]',
    ],
)
def test_subprocess_local_git_verbs_not_flagged(tmp_path: Path, argv: str) -> None:
    """Local git porcelain/plumbing verbs perform no network IO."""
    module = _write(
        tmp_path / "localgit.py",
        f"""
        import subprocess


        def run() -> object:
            return subprocess.run({argv}, check=True)
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.SUBPROCESS_NETWORK not in _violations(result)


@pytest.mark.parametrize(
    "argv",
    [
        '["git", "fetch", "origin"]',
        '["git", "clone", "https://example.com/r.git"]',
        '["git", "push", "origin", "main"]',
        '["git", "pull", "--ff-only"]',
        '["git", "ls-remote", "origin"]',
        '["git", "-C", "/repo", "fetch", "origin"]',
    ],
)
def test_subprocess_network_git_verbs_flagged(tmp_path: Path, argv: str) -> None:
    """Network git verbs (fetch/clone/push/pull/ls-remote) ARE flagged."""
    module = _write(
        tmp_path / "netgit.py",
        f"""
        import subprocess


        def run() -> object:
            return subprocess.run({argv}, check=True)
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.SUBPROCESS_NETWORK in _violations(result)


def test_hardcoded_lan_ip_flagged(tmp_path: Path) -> None:
    module = _write(
        tmp_path / "config.py",
        """
        DEFAULT_HOST = "192.168.86.201"
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.HARDCODED_CONFIG in _violations(result)


def test_hardcoded_topic_flagged(tmp_path: Path) -> None:
    module = _write(
        tmp_path / "topics.py",
        """
        def emit(bus) -> None:
            bus.publish("onex.evt.delegation.inference-requested.v1", {})
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.HARDCODED_TOPIC in _violations(result)


def test_topics_constants_module_is_flagged(tmp_path: Path) -> None:
    """A topics.py-style constants module IS flagged — no topic-module exemption.

    Per OmniNode architecture, topic strings belong in contract event_bus
    declarations. Topic literals anywhere outside contracts — including
    dedicated ``topics.py`` / ``*_topics.py`` constants modules — MUST stay
    flagged (OMN-12540 keeps topics strict).
    """
    module = _write(
        tmp_path / "topics.py",
        '''
        """Centralized topic name constants."""

        DELEGATION_REQUESTED = "onex.evt.delegation.inference-requested.v1"
        ROUTING_COMPLETED = "agent.routing.completed.v1"
        ''',
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.HARDCODED_TOPIC in _violations(result)
    assert result.verdict != EnumComplianceVerdict.COMPLIANT


def test_local_timeout_kwarg_not_flagged(tmp_path: Path) -> None:
    """A bare local ``timeout=`` is a control-flow bound, not inference config.

    OMN-12540: ``timeout`` was removed from the inference-param set because it
    over-flagged legitimate local timeouts (subprocess, sockets, asyncio).
    """
    module = _write(
        tmp_path / "runner.py",
        """
        import subprocess


        def run() -> object:
            return subprocess.run(["echo", "hi"], timeout=30, check=True)
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.HARDCODED_CONFIG not in _violations(result)
    assert result.verdict == EnumComplianceVerdict.COMPLIANT


def test_inference_params_still_flagged_without_timeout(tmp_path: Path) -> None:
    """Removing timeout must not weaken genuine inference-param detection."""
    module = _write(
        tmp_path / "infer.py",
        """
        def gen(client) -> str:
            return client.complete(
                prompt="hi",
                max_tokens=512,
                top_k=40,
                presence_penalty=0.5,
                frequency_penalty=0.5,
            )
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.HARDCODED_CONFIG in _violations(result)


def test_hardcoded_temperature_kwarg_flagged(tmp_path: Path) -> None:
    module = _write(
        tmp_path / "gen.py",
        """
        def generate(client) -> str:
            return client.complete(prompt="hi", temperature=0.7, top_p=0.95)
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.HARDCODED_CONFIG in _violations(result)


def test_hardcoded_max_tokens_in_dict_literal_flagged(tmp_path: Path) -> None:
    """The keystone shape: max_tokens as a JSON dict value, not a call kwarg."""
    module = _write(
        tmp_path / "body.py",
        """
        def build() -> dict:
            return {"messages": [], "max_tokens": 2048}
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert EnumComplianceViolation.HARDCODED_CONFIG in _violations(result)


def test_inline_suppression_disarms_finding(tmp_path: Path) -> None:
    """An inline '# no-contract-check:' comment suppresses the finding on that line."""
    module = _write(
        tmp_path / "legacy.py",
        """
        import httpx


        def call(endpoint: str) -> object:
            return httpx.get(endpoint)  # no-contract-check: legacy probe, OMN-1
        """,
    )

    result = scan_freestanding_imperative_io(module, repo="sea")

    # The finding still exists, but is suppressed and does not count as active.
    assert any(f.suppressed for f in result.findings)
    assert result.active_findings == []
    assert result.verdict == EnumComplianceVerdict.COMPLIANT


def test_docstring_topic_not_flagged(tmp_path: Path) -> None:
    """Topic-shaped strings in docstrings are not findings."""
    module = _write(
        tmp_path / "doc.py",
        '''
        """Publishes onex.evt.demo.thing.v1 events somewhere."""

        VALUE = 1
        ''',
    )

    result = scan_freestanding_imperative_io(module, repo="sea")
    assert result.active_findings == []


def test_syntax_error_module_is_skipped(tmp_path: Path) -> None:
    module = _write(tmp_path / "broken.py", "def (:\n")
    result = scan_freestanding_imperative_io(module, repo="sea")
    assert result.active_findings == []
    assert result.verdict == EnumComplianceVerdict.COMPLIANT
