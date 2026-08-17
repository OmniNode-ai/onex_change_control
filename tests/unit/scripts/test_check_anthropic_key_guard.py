# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_anthropic_key_guard pre-commit hook."""

from __future__ import annotations

import typing

import pytest

if typing.TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.scripts.check_anthropic_key_guard import _check_file


@pytest.fixture
def tmp_py(tmp_path: Path) -> Path:
    """Return a temporary .py file path."""
    return tmp_path / "test_file.py"


class TestRequiredCheckDetection:
    """Tests that required-check patterns are detected."""

    def test_getenv_required_check(self, tmp_py: Path) -> None:
        tmp_py.write_text('if not os.getenv("ANTHROPIC_API_KEY"):\n    sys.exit(1)\n')
        violations = _check_file(tmp_py)
        assert len(violations) == 1
        assert "ANTHROPIC_API_KEY treated as required" in violations[0]

    def test_environ_bracket_access(self, tmp_py: Path) -> None:
        tmp_py.write_text('key = os.environ["ANTHROPIC_API_KEY"]\n')
        violations = _check_file(tmp_py)
        assert len(violations) == 1

    def test_markdown_required_table(self, tmp_path: Path) -> None:
        md = tmp_path / "README.md"
        md.write_text("| `ANTHROPIC_API_KEY` | API key | Required for headless |\n")
        violations = _check_file(md)
        assert len(violations) == 1

    def test_shell_requires_comment(self, tmp_path: Path) -> None:
        sh = tmp_path / "script.sh"
        sh.write_text("# Requires: claude CLI, ANTHROPIC_API_KEY\n")
        violations = _check_file(sh)
        assert len(violations) == 1

    def test_shell_missing_check(self, tmp_path: Path) -> None:
        sh = tmp_path / "script.sh"
        sh.write_text('missing+=("ANTHROPIC_API_KEY")\n')
        violations = _check_file(sh)
        assert len(violations) == 1


class TestExemptions:
    """Tests that safe patterns are NOT flagged."""

    def test_optional_getenv_with_default(self, tmp_py: Path) -> None:
        tmp_py.write_text('key = os.environ.get("ANTHROPIC_API_KEY", "")\n')
        violations = _check_file(tmp_py)
        assert len(violations) == 0

    def test_commented_env_example(self, tmp_path: Path) -> None:
        env = tmp_path / ".env.example"
        env.write_text("# ANTHROPIC_API_KEY=sk-ant-...\n")
        violations = _check_file(env)
        assert len(violations) == 0

    def test_log_sanitizer_exempt(self, tmp_path: Path) -> None:
        sanitizer = tmp_path / "log_sanitizer.py"
        sanitizer.write_text(
            "r'(OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY)[\"\\']'\n"
        )
        violations = _check_file(sanitizer)
        assert len(violations) == 0

    def test_exemption_marker(self, tmp_py: Path) -> None:
        content = (
            'if not os.getenv("ANTHROPIC_API_KEY"):'
            "  # anthropic-key-ok: direct API usage\n"
        )
        tmp_py.write_text(content)
        violations = _check_file(tmp_py)
        assert len(violations) == 0

    def test_known_vars_inventory(self, tmp_path: Path) -> None:
        validate = tmp_path / "validate_env.py"
        validate.write_text('"ANTHROPIC_API_KEY", "GEMINI_API_KEY",\n')
        violations = _check_file(validate)
        assert len(violations) == 0

    def test_demo_example_exempt(self, tmp_path: Path) -> None:
        demo = tmp_path / "examples" / "demo" / "handler.py"
        demo.parent.mkdir(parents=True)
        demo.write_text('key = os.environ["ANTHROPIC_API_KEY"]\n')
        violations = _check_file(demo)
        assert len(violations) == 0
