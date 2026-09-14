# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for --scan-freestanding coverage in check-imperative-contracts.

These pin the integration: enumerating freestanding modules (everything under
``src/`` that is NOT a node handler) and auditing them alongside handlers.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from onex_change_control.scripts.check_imperative_contracts import main, scan_repo
from onex_change_control.validators.arch_handler_contract_compliance import (
    _find_freestanding_modules,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _make_repo(tmp_path: Path) -> Path:
    """Repo with one node handler AND one freestanding consumer module."""
    repo_root = tmp_path / "sea"
    (repo_root / ".git").mkdir(parents=True)

    # A node + handler (seen by the node scanner).
    node_dir = repo_root / "src" / "sea" / "nodes" / "node_demo"
    handlers = node_dir / "handlers"
    handlers.mkdir(parents=True)
    (node_dir / "contract.yaml").write_text(
        textwrap.dedent(
            """\
            name: node_demo
            node_type: EFFECT_GENERIC
            contract_version: {major: 1, minor: 0, patch: 0}
            handler_routing:
              routing_strategy: operation_match
              handlers:
                - operation: run
                  handler:
                    name: HandlerDemo
                    module: sea.nodes.node_demo.handlers.handler_demo
            """
        ),
        encoding="utf-8",
    )
    (node_dir / "node.py").write_text(
        "class NodeDemo:\n    def __init__(self, c):\n        super().__init__(c)\n",
        encoding="utf-8",
    )
    (handlers / "__init__.py").write_text("", encoding="utf-8")
    (handlers / "handler_demo.py").write_text(
        '"""Compliant handler."""\n\n\nclass HandlerDemo:\n'
        "    async def handle(self, data: dict) -> dict:\n"
        '        return {"ok": True}\n',
        encoding="utf-8",
    )

    # A freestanding consumer module (invisible to the node scanner).
    pipeline = repo_root / "src" / "pipeline"
    pipeline.mkdir(parents=True)
    (pipeline / "__init__.py").write_text("", encoding="utf-8")
    (pipeline / "consumer.py").write_text(
        textwrap.dedent(
            '''\
            """Raw inference consumer."""

            import httpx


            def run(endpoint: str, prompt: str) -> object:
                return httpx.post(
                    endpoint,
                    json={"prompt": prompt, "max_tokens": 2048},
                    timeout=30,
                )
            '''
        ),
        encoding="utf-8",
    )
    return repo_root


def test_find_freestanding_modules_excludes_handlers(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)

    modules = _find_freestanding_modules(repo_root)
    names = {m.name for m in modules}

    assert "consumer.py" in names
    # Node handler and node.py are NOT freestanding.
    assert "handler_demo.py" not in names
    assert "node.py" not in names
    # __init__.py modules are skipped.
    assert "__init__.py" not in names


def test_find_freestanding_modules_skips_tests_and_caches(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    (repo_root / "src" / "__pycache__").mkdir(parents=True)
    (repo_root / "src" / "__pycache__" / "cached.py").write_text(
        "import httpx\n", encoding="utf-8"
    )
    tests_dir = repo_root / "src" / "sea" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_thing.py").write_text("import httpx\n", encoding="utf-8")

    names = {m.name for m in _find_freestanding_modules(repo_root)}
    assert "cached.py" not in names
    assert "test_thing.py" not in names


def test_scan_repo_without_flag_ignores_freestanding(tmp_path: Path) -> None:
    """Default behavior is unchanged: only the (compliant) handler is scanned."""
    repo_root = _make_repo(tmp_path)

    summary = scan_repo(repo_root)

    # No freestanding scan -> consumer.py debt invisible, handler is clean.
    assert summary.new_violation_count == 0


def test_scan_repo_with_flag_detects_freestanding(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)

    summary = scan_repo(repo_root, scan_freestanding=True)

    assert summary.freestanding_module_count >= 1
    assert summary.new_violation_count >= 1
    flagged = {r.module_path for r in summary.freestanding_results if r.violations}
    assert any(p.endswith("consumer.py") for p in flagged)


def test_main_scan_freestanding_fails_on_freestanding_debt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _make_repo(tmp_path)

    exit_code = main(["--repo-root", str(tmp_path / "sea"), "--scan-freestanding"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "consumer.py" in output


def test_main_without_flag_passes_clean_handler_repo(
    tmp_path: Path,
) -> None:
    _make_repo(tmp_path)

    exit_code = main(["--repo-root", str(tmp_path / "sea")])

    assert exit_code == 0
