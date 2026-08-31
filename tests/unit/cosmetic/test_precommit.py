# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the pre-commit version check and fix module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from onex_change_control.cosmetic.checks.precommit import run_check

if TYPE_CHECKING:
    from pathlib import Path

SPEC = {
    "precommit": {
        "versions": {
            "https://github.com/astral-sh/ruff-pre-commit": "v0.15.6",
            "https://github.com/pre-commit/pre-commit-hooks": "v6.0.0",
        },
    },
}

CORRECT_CONFIG = """\
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.6
    hooks:
      - id: ruff
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: trailing-whitespace
"""

WRONG_CONFIG = """\
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.0
    hooks:
      - id: ruff
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
"""


@pytest.mark.unit
class TestPrecommitCheck:
    """Check-mode tests."""

    def test_correct_versions_pass(self, tmp_path: Path) -> None:
        """Correct versions produce no violations."""
        (tmp_path / ".pre-commit-config.yaml").write_text(CORRECT_CONFIG)
        violations = run_check(tmp_path, SPEC)
        assert violations == []

    def test_wrong_versions_detected(self, tmp_path: Path) -> None:
        """Wrong versions produce violations for each mismatched repo."""
        (tmp_path / ".pre-commit-config.yaml").write_text(WRONG_CONFIG)
        violations = run_check(tmp_path, SPEC)
        assert len(violations) == 2
        assert all(v.check == "precommit" for v in violations)

    def test_missing_config_skips(self, tmp_path: Path) -> None:
        """Missing .pre-commit-config.yaml returns empty list."""
        violations = run_check(tmp_path, SPEC)
        assert violations == []

    def test_local_repos_ignored(self, tmp_path: Path) -> None:
        """Repos with 'repo: local' are not checked."""
        config = """\
repos:
  - repo: local
    hooks:
      - id: my-hook
        name: My Hook
        entry: python -m my_hook
        language: system
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.6
    hooks:
      - id: ruff
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: trailing-whitespace
"""
        (tmp_path / ".pre-commit-config.yaml").write_text(config)
        violations = run_check(tmp_path, SPEC)
        assert violations == []

    def test_untracked_repos_ignored(self, tmp_path: Path) -> None:
        """Repos not in the spec produce no violations."""
        config = """\
repos:
  - repo: https://github.com/some-org/some-tool
    rev: v1.0.0
    hooks:
      - id: some-hook
"""
        (tmp_path / ".pre-commit-config.yaml").write_text(config)
        violations = run_check(tmp_path, SPEC)
        assert violations == []


@pytest.mark.unit
class TestPrecommitFix:
    """Fix-mode tests."""

    def test_fix_updates_versions(self, tmp_path: Path) -> None:
        """Fix mode updates wrong versions in-place."""
        (tmp_path / ".pre-commit-config.yaml").write_text(WRONG_CONFIG)
        run_check(tmp_path, SPEC, fix=True)
        content = (tmp_path / ".pre-commit-config.yaml").read_text()
        assert "v0.15.6" in content
        assert "v6.0.0" in content
        assert "v0.14.0" not in content
        assert "v5.0.0" not in content

    def test_fix_preserves_comments(self, tmp_path: Path) -> None:
        """Fix mode preserves YAML comments."""
        config = """\
# My important comment
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.0  # old version
    hooks:
      - id: ruff
"""
        (tmp_path / ".pre-commit-config.yaml").write_text(config)
        spec = {
            "precommit": {
                "versions": {"https://github.com/astral-sh/ruff-pre-commit": "v0.15.6"}
            }
        }
        run_check(tmp_path, spec, fix=True)
        content = (tmp_path / ".pre-commit-config.yaml").read_text()
        assert "# My important comment" in content
        assert "v0.15.6" in content
