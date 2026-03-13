# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the .pre-commit-hooks.yaml export file."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.unit
def test_pre_commit_hooks_yaml_valid() -> None:
    """Verify the hooks export file is valid and well-formed."""
    hooks_path = REPO_ROOT / ".pre-commit-hooks.yaml"
    assert hooks_path.exists(), f"Missing {hooks_path}"
    with hooks_path.open() as f:
        hooks = yaml.safe_load(f)
    assert isinstance(hooks, list)
    assert len(hooks) >= 1
    hook = hooks[0]
    assert hook["id"] == "cosmetic-lint"
    assert hook["language"] == "python"
    assert "cosmetic-lint" in hook["entry"]
