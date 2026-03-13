# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the README badge check and fix module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from onex_change_control.cosmetic.checks.readme import run_check

if TYPE_CHECKING:
    from pathlib import Path

SPEC = {
    "readme": {
        "required_badges": [
            {"pattern": "MIT", "description": "License badge"},
            {"pattern": "ruff", "description": "Ruff linter badge"},
            {"pattern": "mypy", "description": "Mypy type checker badge"},
            {"pattern": "pre-commit", "description": "Pre-commit badge"},
        ],
    },
}

README_WITH_ALL_BADGES = """\
# My Project

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

Some description here.
"""

README_NO_BADGES = """\
# My Project

Some description here without any badges.
"""


@pytest.mark.unit
class TestReadmeCheck:
    """Check-mode tests."""

    def test_all_badges_present_passes(self, tmp_path: Path) -> None:
        """README with all required badges produces no violations."""
        (tmp_path / "README.md").write_text(README_WITH_ALL_BADGES)
        violations = run_check(tmp_path, SPEC)
        assert violations == []

    def test_missing_badges_detected(self, tmp_path: Path) -> None:
        """README without badges produces violations."""
        (tmp_path / "README.md").write_text(README_NO_BADGES)
        violations = run_check(tmp_path, SPEC)
        assert len(violations) == 4
        assert all(v.check == "readme" for v in violations)
        assert all(v.fixable for v in violations)

    def test_no_readme_skips(self, tmp_path: Path) -> None:
        """Missing README.md returns empty list."""
        violations = run_check(tmp_path, SPEC)
        assert violations == []


@pytest.mark.unit
class TestReadmeFix:
    """Fix-mode tests."""

    def test_fix_adds_missing_badges(self, tmp_path: Path) -> None:
        """Fix mode inserts missing badges after H1."""
        (tmp_path / "README.md").write_text(README_NO_BADGES)
        run_check(tmp_path, SPEC, fix=True)
        content = (tmp_path / "README.md").read_text().lower()
        assert "mit" in content
        assert "ruff" in content
        assert "mypy" in content
        assert "pre-commit" in content

    def test_fix_does_not_duplicate(self, tmp_path: Path) -> None:
        """Fix mode does not add badges that already exist."""
        (tmp_path / "README.md").write_text(README_WITH_ALL_BADGES)
        run_check(tmp_path, SPEC, fix=True)
        content = (tmp_path / "README.md").read_text()
        # Count badge occurrences — should not be duplicated.
        assert content.count("mypy_badge.svg") == 1

    def test_fix_is_idempotent(self, tmp_path: Path) -> None:
        """Running fix twice produces the same output."""
        (tmp_path / "README.md").write_text(README_NO_BADGES)
        run_check(tmp_path, SPEC, fix=True)
        content_after_first = (tmp_path / "README.md").read_text()
        run_check(tmp_path, SPEC, fix=True)
        content_after_second = (tmp_path / "README.md").read_text()
        assert content_after_first == content_after_second
