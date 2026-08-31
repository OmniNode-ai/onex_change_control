# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for SuiteManager [OMN-6783]."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.eval.suite_manager import SuiteManager

_MINIMAL_SUITE_YAML = """\
suite_id: test-suite
name: Test Suite
description: A test suite
version: "1.0.0"
created_at: "2026-01-01T00:00:00Z"
tasks:
  - task_id: task-001
    name: Task One
    category: bug-fix
    prompt: Fix the bug
    repo: test_repo
    setup_commands: []
    success_criteria:
      - "true"
    max_duration_seconds: 60
  - task_id: task-002
    name: Task Two
    category: feature
    prompt: Add the feature
    repo: test_repo
    setup_commands: []
    success_criteria:
      - "true"
    max_duration_seconds: 60
"""

_DUPLICATE_IDS_YAML = """\
suite_id: dup-suite
name: Dup Suite
version: "1.0.0"
created_at: "2026-01-01T00:00:00Z"
tasks:
  - task_id: same-id
    name: Task One
    category: bug-fix
    prompt: Fix
    repo: test_repo
  - task_id: same-id
    name: Task Two
    category: bug-fix
    prompt: Fix again
    repo: test_repo
"""


@pytest.mark.unit
class TestSuiteManager:
    def test_load_valid_suite(self, tmp_path: Path) -> None:
        suite_file = tmp_path / "test.yaml"
        suite_file.write_text(_MINIMAL_SUITE_YAML)

        manager = SuiteManager(suites_dir=tmp_path)
        suite = manager.load_suite("test.yaml")

        assert suite.suite_id == "test-suite"
        assert suite.version == "1.0.0"
        assert len(suite.tasks) == 2

    def test_load_missing_file(self, tmp_path: Path) -> None:
        manager = SuiteManager(suites_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            manager.load_suite("nonexistent.yaml")

    def test_load_invalid_yaml(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("{{invalid yaml")

        manager = SuiteManager(suites_dir=tmp_path)
        with pytest.raises(ValueError, match="Invalid YAML"):
            manager.load_suite("bad.yaml")

    def test_load_duplicate_task_ids(self, tmp_path: Path) -> None:
        dup_file = tmp_path / "dup.yaml"
        dup_file.write_text(_DUPLICATE_IDS_YAML)

        manager = SuiteManager(suites_dir=tmp_path)
        with pytest.raises(ValueError, match="Duplicate task_ids"):
            manager.load_suite("dup.yaml")

    def test_list_suites(self, tmp_path: Path) -> None:
        (tmp_path / "a.yaml").write_text(_MINIMAL_SUITE_YAML)
        (tmp_path / "b.yml").write_text(_MINIMAL_SUITE_YAML)
        (tmp_path / "c.txt").write_text("not a suite")

        manager = SuiteManager(suites_dir=tmp_path)
        suites = manager.list_suites()
        assert suites == ["a.yaml", "b.yml"]

    def test_list_suites_empty_dir(self, tmp_path: Path) -> None:
        manager = SuiteManager(suites_dir=tmp_path / "nonexistent")
        assert manager.list_suites() == []

    def test_get_suite_hash(self, tmp_path: Path) -> None:
        suite_file = tmp_path / "test.yaml"
        suite_file.write_text(_MINIMAL_SUITE_YAML)

        manager = SuiteManager(suites_dir=tmp_path)
        manager.load_suite("test.yaml")
        h = manager.get_suite_hash("test.yaml")
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex

    def test_has_changed_after_modification(self, tmp_path: Path) -> None:
        suite_file = tmp_path / "test.yaml"
        suite_file.write_text(_MINIMAL_SUITE_YAML)

        manager = SuiteManager(suites_dir=tmp_path)
        manager.load_suite("test.yaml")
        assert manager.has_changed("test.yaml") is False

        suite_file.write_text(_MINIMAL_SUITE_YAML + "\n# modified")
        assert manager.has_changed("test.yaml") is True

    def test_has_changed_no_cache(self, tmp_path: Path) -> None:
        suite_file = tmp_path / "test.yaml"
        suite_file.write_text(_MINIMAL_SUITE_YAML)

        manager = SuiteManager(suites_dir=tmp_path)
        assert manager.has_changed("test.yaml") is True
