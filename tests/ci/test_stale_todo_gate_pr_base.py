# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Regression tests for the stale TODO workflow diff base."""

from __future__ import annotations

from pathlib import Path

WORKFLOWS = (
    Path(".github/workflows/stale-todo-gate.yml"),
    Path("workflows/stale-todo-gate.yml"),
)


def test_stale_todo_gate_diffs_against_pull_request_base() -> None:
    for workflow_path in WORKFLOWS:
        workflow = workflow_path.read_text()

        assert "BASE_REF: ${{ github.base_ref || 'main' }}" in workflow
        assert 'git fetch --no-tags --depth=1 origin "$BASE_REF"' in workflow
        assert '"origin/${BASE_REF}...HEAD"' in workflow
        assert "origin/main...HEAD" not in workflow
