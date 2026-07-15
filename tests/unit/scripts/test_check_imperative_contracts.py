# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from onex_change_control.scripts.check_imperative_contracts import (
    discover_repo_roots,
    main,
    scan_repo,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _create_repo_with_imperative_handler(tmp_path: Path, repo_name: str) -> Path:
    repo_root = tmp_path / repo_name
    node_dir = repo_root / "src" / "test_pkg" / "nodes" / "node_test"
    handlers_dir = node_dir / "handlers"
    handlers_dir.mkdir(parents=True)
    (repo_root / ".git").mkdir()
    (node_dir / "contract.yaml").write_text(
        textwrap.dedent(
            """\
            name: node_test
            node_type: EFFECT_GENERIC
            contract_version: {major: 1, minor: 0, patch: 0}
            node_version: "1.0.0"
            description: Test node
            input_model: {name: ModelTestInput, module: test}
            output_model: {name: ModelTestOutput, module: test}
            handler_routing:
              routing_strategy: operation_match
              handlers:
                - operation: run
                  handler:
                    name: HandlerExample
                    module: test_pkg.nodes.node_test.handlers.handler_example
            """
        ),
        encoding="utf-8",
    )
    (node_dir / "node.py").write_text(
        textwrap.dedent(
            """\
            class NodeTest:
                def __init__(self, container):
                    super().__init__(container)
            """
        ),
        encoding="utf-8",
    )
    (handlers_dir / "__init__.py").write_text("", encoding="utf-8")
    (handlers_dir / "handler_example.py").write_text(
        textwrap.dedent(
            """\
            TOPIC = "onex.evt.platform.undeclared-topic.v1"

            def handle(data):
                return {"topic": TOPIC, "data": data}
            """
        ),
        encoding="utf-8",
    )
    return repo_root


def test_discover_repo_roots_skips_worktrees(tmp_path: Path) -> None:
    repo_root = _create_repo_with_imperative_handler(tmp_path, "repo_a")
    worktrees = tmp_path / "omni_worktrees" / "repo_b"
    worktrees.mkdir(parents=True)
    (worktrees / ".git").mkdir()

    assert discover_repo_roots(tmp_path) == [repo_root]


def test_scan_repo_reports_unbaselined_imperative_handler(tmp_path: Path) -> None:
    repo_root = _create_repo_with_imperative_handler(tmp_path, "repo_a")

    summary = scan_repo(repo_root)

    assert summary.handler_count == 1
    assert summary.allowlisted_count == 0
    assert summary.new_violation_count == 1


def test_repo_local_allowlist_baselines_existing_violation(tmp_path: Path) -> None:
    repo_root = _create_repo_with_imperative_handler(tmp_path, "repo_a")
    (repo_root / "arch-handler-contract-compliance-allowlist.yaml").write_text(
        textwrap.dedent(
            """\
            allowlisted_handlers:
              - path: test_pkg/nodes/node_test/handlers/handler_example.py
                violations:
                  - hardcoded_topic
                  - missing_handler_routing
                ticket: OMN-11846
            """
        ),
        encoding="utf-8",
    )

    summary = scan_repo(repo_root)

    assert summary.allowlisted_count == 1
    assert summary.new_violation_count == 0


def test_explicit_allowlists_dir_preferred_over_repo_local(tmp_path: Path) -> None:
    repo_root = _create_repo_with_imperative_handler(tmp_path, "repo_a")
    allowlists_dir = tmp_path / "allowlists"
    allowlists_dir.mkdir()
    (repo_root / "arch-handler-contract-compliance-allowlist.yaml").write_text(
        "allowlisted_handlers: []\n",
        encoding="utf-8",
    )
    (allowlists_dir / "repo_a.yaml").write_text(
        textwrap.dedent(
            """\
            allowlisted_handlers:
              - path: test_pkg/nodes/node_test/handlers/handler_example.py
                violations:
                  - hardcoded_topic
                  - missing_handler_routing
                ticket: OMN-11846
            """
        ),
        encoding="utf-8",
    )

    summary = scan_repo(repo_root, allowlists_dir=allowlists_dir)

    assert summary.allowlist_path == allowlists_dir / "repo_a.yaml"
    assert summary.allowlisted_count == 1
    assert summary.new_violation_count == 0


def test_main_fails_on_new_imperative_violation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _create_repo_with_imperative_handler(tmp_path, "repo_a")

    exit_code = main(["--workspace-root", str(tmp_path)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "repo_a" in output
    assert "Blocking LIVE imperative violations" in output


def test_main_no_fail_reports_without_blocking(tmp_path: Path) -> None:
    _create_repo_with_imperative_handler(tmp_path, "repo_a")

    exit_code = main(["--workspace-root", str(tmp_path), "--no-fail"])

    assert exit_code == 0


# ---------------------------------------------------------------------------
# Allowlist ticket-format enforcement (OMN-11878)
# ---------------------------------------------------------------------------


def test_scan_repo_reports_placeholder_ticket_as_blocking(tmp_path: Path) -> None:
    """A '# migration pending' placeholder ticket surfaces as a scan blocker."""
    repo_root = _create_repo_with_imperative_handler(tmp_path, "repo_a")
    (repo_root / "arch-handler-contract-compliance-allowlist.yaml").write_text(
        textwrap.dedent(
            """\
            allowlisted_handlers:
              - path: test_pkg/nodes/node_test/handlers/handler_example.py
                violations:
                  - hardcoded_topic
                  - missing_handler_routing
                ticket: '# migration pending'
            """
        ),
        encoding="utf-8",
    )

    summary = scan_repo(repo_root)

    # The path is still allowlisted for the violation scan itself...
    assert summary.allowlisted_count == 1
    assert summary.new_violation_count == 0
    # ...but the placeholder ticket is reported as its own blocker.
    assert len(summary.ticket_format_violations) == 1
    assert "handler_example.py" in summary.ticket_format_violations[0]


def test_main_fails_on_placeholder_allowlist_ticket(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI exits 1 when an allowlist entry has a placeholder ticket."""
    repo_root = _create_repo_with_imperative_handler(tmp_path, "repo_a")
    (repo_root / "arch-handler-contract-compliance-allowlist.yaml").write_text(
        textwrap.dedent(
            """\
            allowlisted_handlers:
              - path: test_pkg/nodes/node_test/handlers/handler_example.py
                violations:
                  - hardcoded_topic
                  - missing_handler_routing
                ticket: '# migration pending'
            """
        ),
        encoding="utf-8",
    )

    exit_code = main(["--workspace-root", str(tmp_path)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "ticket-format violations" in output


def test_main_passes_with_real_ticket_on_allowlist(tmp_path: Path) -> None:
    """A real OMN-#### ticket on the allowlist entry does not block the CLI."""
    repo_root = _create_repo_with_imperative_handler(tmp_path, "repo_a")
    (repo_root / "arch-handler-contract-compliance-allowlist.yaml").write_text(
        textwrap.dedent(
            """\
            allowlisted_handlers:
              - path: test_pkg/nodes/node_test/handlers/handler_example.py
                violations:
                  - hardcoded_topic
                  - missing_handler_routing
                ticket: OMN-11878
            """
        ),
        encoding="utf-8",
    )

    exit_code = main(["--workspace-root", str(tmp_path)])

    assert exit_code == 0
