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


def test_validate_boundaries_validator_install_retries_egress_failures() -> None:
    """The validator install should tolerate transient GitHub/TLS fetch errors."""
    action = _load_action()
    steps = action["runs"]["steps"]

    setup_python_step = next(
        step for step in steps if step.get("name") == "Set up Python"
    )
    setup_uv_step = next(step for step in steps if step.get("name") == "Install uv")
    install_step = next(
        step for step in steps if step.get("name") == "Install onex_change_control"
    )

    assert setup_python_step["uses"] == "actions/setup-python@v6"
    assert setup_uv_step["uses"] == "astral-sh/setup-uv@v7"
    assert install_step["env"]["UV_HTTP_TIMEOUT"] == "600"
    assert install_step["env"]["UV_SYNC_ATTEMPTS"] == "5"
    assert install_step["env"]["UV_SYNC_RETRY_DELAY_SECONDS"] == "10"
    assert install_step["env"]["UV_CONCURRENT_DOWNLOADS"] == "2"

    run_script = install_step["run"]
    assert "git config --global http.version HTTP/1.1" in run_script
    assert "until uv sync --no-group dev; do" in run_script
    assert (
        'echo "::warning::uv sync attempt ${attempt}/${UV_SYNC_ATTEMPTS} failed'
        in run_script
    )
    assert 'echo "::error::uv sync failed after ${attempt} attempt(s)"' in run_script


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


def test_validate_boundaries_does_not_clone_calling_repo_as_peer() -> None:
    """Caller checkout is authoritative and should not count as peer clone drift."""
    action = _load_action()
    steps = action["runs"]["steps"]

    clone_step = next(step for step in steps if step.get("name") == "Clone peer repos")
    symlink_step = next(
        step
        for step in steps
        if step.get("name") == "Symlink calling repo into workspace"
    )

    assert 'CALLING_REPO="${GITHUB_REPOSITORY##*/}"' in clone_step["run"]
    assert '[[ "$repo" == "$CALLING_REPO" ]]' in clone_step["run"]
    assert "Skipping clone of calling repo" in clone_step["run"]
    assert 'DEGRADED="${DEGRADED}${repo},"' in clone_step["run"]
    assert 'REPO_NAME="${GITHUB_REPOSITORY##*/}"' in symlink_step["run"]
    assert (
        'ln -s "$GITHUB_WORKSPACE" "/tmp/omni_repos/${REPO_NAME}"'
        in symlink_step["run"]
    )


def test_validate_boundaries_backfills_omnimarket_peer_repo() -> None:
    """OMN-13701 moved intent-classified consumption from omnimemory to omnimarket."""
    action = _load_action()
    repos_default = action["inputs"]["repos"]["default"]
    clone_step = next(
        step
        for step in action["runs"]["steps"]
        if step.get("name") == "Clone peer repos"
    )

    assert "omnimemory,omnimarket" in repos_default
    assert "OMNIMARKET_PRESENT=false" in clone_step["run"]
    assert 'REPOS+=("omnimarket")' in clone_step["run"]
