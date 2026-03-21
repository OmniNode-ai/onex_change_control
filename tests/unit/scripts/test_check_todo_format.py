# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_todo_format pre-commit hook."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.scripts.check_todo_format import check_file

# ---------------------------------------------------------------------------
# Valid formats (should pass)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_valid_todo_passes(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text("# TODO(OMN-1234): fix the widget\n")
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_valid_fixme_passes(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text("# FIXME(OMN-999): known issue with retry\n")
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_valid_hack_passes(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text("# HACK(OMN-42): workaround for upstream bug\n")
    violations = check_file(str(f))
    assert violations == []


# ---------------------------------------------------------------------------
# Invalid formats (should fail)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bare_todo_fails(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text("# TODO: fix this\n")
    violations = check_file(str(f))
    assert len(violations) == 1
    assert "bare TODO/FIXME/HACK" in violations[0]


@pytest.mark.unit
def test_bare_fixme_fails(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text("# FIXME: broken\n")
    violations = check_file(str(f))
    assert len(violations) == 1


@pytest.mark.unit
def test_bare_hack_fails(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text("# HACK: workaround\n")
    violations = check_file(str(f))
    assert len(violations) == 1


@pytest.mark.unit
def test_omn_tbd_fails(tmp_path: Path) -> None:
    """OMN-TBD is not a valid ticket number."""
    f = tmp_path / "service.py"
    f.write_text("# TODO(OMN-TBD): placeholder\n")
    violations = check_file(str(f))
    assert len(violations) == 1


# ---------------------------------------------------------------------------
# Docstrings and string literals (should pass -- not comments)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_todo_in_docstring_passes(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('"""\nTODO: fix this later\n"""\nx = 1\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_todo_in_string_passes(tmp_path: Path) -> None:
    """A bare TODO inside a string literal is not a comment."""
    f = tmp_path / "service.py"
    f.write_text('msg = "TODO: fix this"\n')
    violations = check_file(str(f))
    assert violations == []


# ---------------------------------------------------------------------------
# Exemptions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_exempt_marker_passes(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text("# TODO: old  # TODO_FORMAT_EXEMPT: legacy debt from v1 migration\n")
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_exempt_no_reason_fails(tmp_path: Path) -> None:
    """Exemption without a reason is not valid."""
    f = tmp_path / "service.py"
    f.write_text("# TODO: old  # TODO_FORMAT_EXEMPT:\n")
    violations = check_file(str(f))
    assert len(violations) == 1


# ---------------------------------------------------------------------------
# Path exclusions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_test_file_excluded(tmp_path: Path) -> None:
    d = tmp_path / "tests"
    d.mkdir()
    f = d / "test_foo.py"
    f.write_text("# TODO: fix this test\n")
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_docs_file_excluded(tmp_path: Path) -> None:
    d = tmp_path / "docs"
    d.mkdir()
    f = d / "example.py"
    f.write_text("# TODO: document this\n")
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_non_python_file_excluded(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("TODO: fix this\n")
    violations = check_file(str(f))
    assert violations == []


# ---------------------------------------------------------------------------
# Inline code comments
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_inline_code_comment_fails(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text("x = 1  # TODO: fix this\n")
    violations = check_file(str(f))
    assert len(violations) == 1


@pytest.mark.unit
def test_inline_code_comment_valid_passes(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text("x = 1  # TODO(OMN-1234): fix this\n")
    violations = check_file(str(f))
    assert violations == []


# ---------------------------------------------------------------------------
# Multiple violations
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_multiple_violations_counted(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text("# TODO: first\nx = 1\n# FIXME: second\ny = 2  # HACK: third\n")
    violations = check_file(str(f))
    assert len(violations) == 3


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_todo_in_f_string_passes(tmp_path: Path) -> None:
    """A bare TODO inside an f-string is not a comment."""
    f = tmp_path / "service.py"
    f.write_text('msg = f"TODO: {value}"\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_mixed_valid_and_invalid(tmp_path: Path) -> None:
    """Only invalid TODOs are flagged."""
    f = tmp_path / "service.py"
    f.write_text(
        "# TODO(OMN-100): valid\n# TODO: invalid\n# FIXME(OMN-200): also valid\n"
    )
    violations = check_file(str(f))
    assert len(violations) == 1
    assert ":2:" in violations[0]


@pytest.mark.unit
def test_self_exclusion(tmp_path: Path) -> None:
    """The script itself is excluded by basename."""
    f = tmp_path / "check_todo_format.py"
    f.write_text("# TODO: this is the hook itself\n")
    violations = check_file(str(f))
    assert violations == []
