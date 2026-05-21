# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression tests for the validate-boundaries composite action."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTION_PATH = REPO_ROOT / ".github" / "actions" / "validate-boundaries" / "action.yml"


def _load_action() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(ACTION_PATH.read_text()))


def test_validate_boundaries_sparse_checkout_includes_package_metadata() -> None:
    """The action's uv sync needs files referenced by pyproject metadata."""
    action = _load_action()
    checkout_step = next(
        step
        for step in action["runs"]["steps"]
        if step.get("name") == "Checkout onex_change_control validators"
    )

    sparse_checkout = checkout_step["with"]["sparse-checkout"].splitlines()

    assert "src/" in sparse_checkout
    assert "README.md" in sparse_checkout
    assert "pyproject.toml" in sparse_checkout
    assert "uv.lock" in sparse_checkout


def test_validate_boundaries_clone_token_base64_is_portable() -> None:
    """Runner images may not have GNU base64 flags such as -w0."""
    action_text = ACTION_PATH.read_text()

    assert "base64 | tr -d '\\n'" in action_text
    assert "base64 -w0" not in action_text


def test_validate_boundaries_scopes_migration_conflicts_to_migration_diffs() -> None:
    """Non-migration PRs should not fail on existing cross-repo schema drift."""
    action = _load_action()
    steps = action["runs"]["steps"]

    scope_step = next(
        step for step in steps if step.get("name") == "Detect boundary validation scope"
    )
    assert scope_step["id"] == "scope"
    assert "GITHUB_EVENT_NAME" in scope_step["run"]
    assert "docker/migrations/.+\\.sql" in scope_step["run"]
    assert "UNKNOWN_CHECK_REQUESTED=false" in scope_step["run"]
    assert "UNKNOWN_CHECK_REQUESTED=true" in scope_step["run"]
    assert "MIGRATION_CONFLICTS_SHOULD_RUN=false" in scope_step["run"]
    assert "SHOULD_RUN_BOUNDARY_CHECKS=false" in scope_step["run"]
    assert '"${UNKNOWN_CHECK_REQUESTED}" == "true"' in scope_step["run"]

    gated_step_names = {
        "Checkout onex_change_control validators",
        "Set up Python",
        "Install uv",
        "Install onex_change_control",
        "Clone peer repos",
        "Symlink calling repo into workspace",
        "Run boundary checks",
    }
    for step in steps:
        if step.get("name") in gated_step_names:
            assert step["if"] == (
                "steps.scope.outputs.should_run_boundary_checks == 'true'"
            )

    boundary_step = next(
        step for step in steps if step.get("name") == "Run boundary checks"
    )
    assert "migration-conflicts: SKIPPED" in boundary_step["run"]
    assert "steps.scope.outputs.migration_conflicts_should_run" in boundary_step["run"]
