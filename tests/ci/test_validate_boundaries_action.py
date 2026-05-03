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
