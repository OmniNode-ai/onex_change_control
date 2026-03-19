# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_hardcoded_topics pre-commit hook."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.scripts.check_hardcoded_topics import check_file

# ---------------------------------------------------------------------------
# Python files
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_python_topic_in_code_is_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('TOPIC = "onex.evt.foo.bar.v1"\n')
    violations = check_file(str(f))
    assert len(violations) == 1
    assert "hardcoded topic string" in violations[0]


@pytest.mark.unit
def test_python_topic_in_docstring_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('"""\nThis publishes to "onex.evt.foo.bar.v1".\n"""\nx = 1\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_python_topic_in_comment_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('# publishes to "onex.evt.foo.bar.v1"\nx = 1\n')
    violations = check_file(str(f))
    assert violations == []


# ---------------------------------------------------------------------------
# TypeScript / JavaScript files
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ts_topic_in_code_is_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.ts"
    f.write_text('const TOPIC = "onex.cmd.agent.route.v1";\n')
    violations = check_file(str(f))
    assert len(violations) == 1


@pytest.mark.unit
def test_ts_topic_in_single_line_comment_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.ts"
    f.write_text('// publishes to "onex.evt.foo.bar.v1"\nconst x = 1;\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_ts_topic_in_multiline_block_comment_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.ts"
    f.write_text('/*\n * Publishes to "onex.evt.foo.bar.v1"\n */\nconst x = 1;\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_ts_topic_in_single_line_block_comment_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.ts"
    f.write_text('/* topic: "onex.evt.foo.bar.v1" */\nconst x = 1;\n')
    violations = check_file(str(f))
    assert violations == []


# ---------------------------------------------------------------------------
# YAML files
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_yaml_topic_in_comment_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text('# topic: "onex.evt.foo.bar.v1"\nkey: value\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_yaml_topic_in_value_is_violation(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text('topic: "onex.evt.foo.bar.v1"\n')
    violations = check_file(str(f))
    assert len(violations) == 1


# ---------------------------------------------------------------------------
# Approved basenames
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_approved_basename_topics_ts_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "topics.ts"
    f.write_text('export const TOPIC = "onex.evt.foo.bar.v1";\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_approved_basename_contract_yaml_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "contract.yaml"
    f.write_text('topic: "onex.evt.foo.bar.v1"\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_approved_basename_handler_contract_yaml_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "handler_contract.yaml"
    f.write_text('topic: "onex.evt.foo.bar.v1"\n')
    violations = check_file(str(f))
    assert violations == []


# ---------------------------------------------------------------------------
# Test file paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_test_file_path_no_violation(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    f = tests_dir / "test_events.py"
    f.write_text('TOPIC = "onex.evt.foo.bar.v1"\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_test_ts_file_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "events.test.ts"
    f.write_text('const TOPIC = "onex.evt.foo.bar.v1";\n')
    violations = check_file(str(f))
    assert violations == []
