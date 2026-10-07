# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the claude_md_cross_ref scanner (OMN-7913).

The scanner reads a CLAUDE.md file and cross-references its instructions
against actual repository state. These tests exercise each check category
against fixture directories:

* path references in regular lines (existing, missing, template-skipped)
* shell-command script references inside fenced code blocks
* table-cell directory references
* git-history "convention" spot checks (never-use-pip / always-use-uv-run)
* the ``check_claude_md`` entry point (missing file, code-block skipping)

All fixtures are built under ``tmp_path`` so no real repo state is touched.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from onex_change_control.models.model_doc_cross_ref_check import ModelDocCrossRefCheck
from onex_change_control.scanners.claude_md_cross_ref import (
    _check_commands_in_code_block,
    _check_conventions,
    _check_paths_in_line,
    _check_table_entries,
    check_claude_md,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_claude_md(repo_root: Path, body: str) -> Path:
    """Write a CLAUDE.md into ``repo_root`` and return its path."""
    claude_md = repo_root / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    claude_md.write_text(body, encoding="utf-8")
    return claude_md


def _touch(repo_root: Path, rel: str, text: str = "x\n") -> None:
    """Create a file (and parents) under ``repo_root``."""
    target = repo_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _init_git_repo(repo_root: Path, files: dict[str, str]) -> None:
    """Init a git repo at ``repo_root`` with a single commit of ``files``."""
    repo_root.mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True, env=env)
    for rel, text in files.items():
        _touch(repo_root, rel, text)
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=repo_root,
        check=True,
        env=env,
    )


@pytest.mark.unit
class TestCheckPathsInLine:
    """Backtick-enclosed path references in regular lines."""

    def test_existing_path_is_verified(self, tmp_path: Path) -> None:
        _touch(tmp_path, "src/pkg/thing.py")
        results = _check_paths_in_line("See `src/pkg/thing.py` now.", 7, str(tmp_path))
        assert len(results) == 1
        check = results[0]
        assert isinstance(check, ModelDocCrossRefCheck)
        assert check.check_type == "path"
        assert check.verified is True
        assert check.line_number == 7
        assert check.instruction == "src/pkg/thing.py"
        assert "exists" in check.evidence

    def test_missing_path_is_not_verified(self, tmp_path: Path) -> None:
        results = _check_paths_in_line("Run `scripts/absent.sh`.", 3, str(tmp_path))
        assert len(results) == 1
        assert results[0].verified is False
        assert "NOT FOUND" in results[0].evidence

    def test_template_placeholder_is_skipped(self, tmp_path: Path) -> None:
        results = _check_paths_in_line("Edit `src/<name>.py`.", 1, str(tmp_path))
        assert results == []

    def test_non_path_backticks_ignored(self, tmp_path: Path) -> None:
        # No src/tests/scripts/docs/.github/plugins segment -> not a path ref.
        results = _check_paths_in_line("Call `some_function()`.", 1, str(tmp_path))
        assert results == []


@pytest.mark.unit
class TestCheckCommandsInCodeBlock:
    """Script references inside fenced shell command blocks."""

    def test_existing_script_verified(self, tmp_path: Path) -> None:
        _touch(tmp_path, "scripts/run.sh")
        lines = ["```bash", "bash scripts/run.sh --flag", "```"]
        results = _check_commands_in_code_block(lines, 1, 2, str(tmp_path))
        assert len(results) == 1
        assert results[0].check_type == "command"
        assert results[0].verified is True
        assert results[0].line_number == 2

    def test_missing_script_not_verified(self, tmp_path: Path) -> None:
        lines = ["```bash", "python3 scripts/absent.py", "```"]
        results = _check_commands_in_code_block(lines, 1, 2, str(tmp_path))
        assert len(results) == 1
        assert results[0].verified is False
        assert "NOT FOUND" in results[0].evidence

    def test_command_without_slash_is_skipped(self, tmp_path: Path) -> None:
        lines = ["```bash", "python3 script", "```"]
        results = _check_commands_in_code_block(lines, 1, 2, str(tmp_path))
        assert results == []

    def test_absolute_script_path_checked(self, tmp_path: Path) -> None:
        abs_script = tmp_path / "abs_tool.py"
        abs_script.write_text("print('hi')\n", encoding="utf-8")
        lines = ["```bash", f"python3 {abs_script}", "```"]
        results = _check_commands_in_code_block(lines, 1, 2, str(tmp_path / "other"))
        assert len(results) == 1
        assert results[0].verified is True


@pytest.mark.unit
class TestCheckTableEntries:
    """Directory references in markdown table cells."""

    def test_existing_directory_verified(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        line = "| `src/` | core package |"
        results = _check_table_entries(line, 12, str(tmp_path))
        assert len(results) == 1
        assert results[0].check_type == "table"
        assert results[0].verified is True
        assert results[0].line_number == 12

    def test_missing_directory_not_verified(self, tmp_path: Path) -> None:
        line = "| `ghost/` | nowhere |"
        results = _check_table_entries(line, 4, str(tmp_path))
        assert len(results) == 1
        assert results[0].verified is False
        assert "NOT FOUND" in results[0].evidence

    def test_non_directory_cells_ignored(self, tmp_path: Path) -> None:
        line = "| Name | Purpose |"
        results = _check_table_entries(line, 1, str(tmp_path))
        assert results == []


@pytest.mark.unit
class TestCheckConventions:
    """Git-history spot checks for stated conventions."""

    def test_pip_convention_clean_repo_verified(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_git_repo(repo, {"README.md": "hello world\n"})
        lines = ["Never use pip; prefer the package manager."]
        results = _check_conventions(lines, str(repo))
        assert len(results) == 1
        assert results[0].check_type == "convention"
        assert results[0].verified is True
        assert "No 'pip install'" in results[0].evidence

    def test_pip_convention_violation_detected(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_git_repo(repo, {"setup.sh": "pip install requests\n"})
        lines = ["Never use pip in this repo."]
        results = _check_conventions(lines, str(repo))
        assert len(results) == 1
        assert results[0].verified is False
        assert "commits with 'pip install'" in results[0].evidence

    def test_uv_run_convention_usage_verified(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_git_repo(repo, {"Makefile": "test:\n\tuv run pytest\n"})
        lines = ["Always use uv run for commands."]
        results = _check_conventions(lines, str(repo))
        assert len(results) == 1
        assert results[0].check_type == "convention"
        assert results[0].verified is True
        assert "uv run" in results[0].evidence

    def test_lines_without_conventions_produce_nothing(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _init_git_repo(repo, {"README.md": "docs\n"})
        results = _check_conventions(["Just a normal sentence."], str(repo))
        assert results == []


@pytest.mark.unit
class TestCheckClaudeMd:
    """End-to-end ``check_claude_md`` entry point behavior."""

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert check_claude_md(str(tmp_path / "CLAUDE.md"), str(tmp_path)) == []

    def test_repo_with_no_claude_md_returns_empty(self, tmp_path: Path) -> None:
        # A repo directory exists but there is no CLAUDE.md within it.
        (tmp_path / "src").mkdir()
        missing = tmp_path / "does_not_exist" / "CLAUDE.md"
        assert check_claude_md(str(missing), str(tmp_path)) == []

    def test_paths_and_tables_aggregated(self, tmp_path: Path) -> None:
        _touch(tmp_path, "src/pkg/mod.py")
        (tmp_path / "docs").mkdir()
        body = "\n".join(
            [
                "# Repo",
                "Edit `src/pkg/mod.py` to change behavior.",
                "Missing at `scripts/gone.sh`.",
                "",
                "| Dir | Note |",
                "|-----|------|",
                "| `docs/` | documentation |",
                "| `nope/` | absent |",
            ]
        )
        claude_md = _write_claude_md(tmp_path, body)
        results = check_claude_md(str(claude_md), str(tmp_path))

        by_verified = {(r.check_type, r.verified) for r in results}
        assert ("path", True) in by_verified
        assert ("path", False) in by_verified
        assert ("table", True) in by_verified
        assert ("table", False) in by_verified

    def test_code_block_paths_are_not_treated_as_regular_paths(
        self, tmp_path: Path
    ) -> None:
        # A `src/...` backtick inside a fenced block must not be scanned as a
        # regular-line path; only the code-block command scanner applies there.
        _touch(tmp_path, "scripts/run.sh")
        body = "\n".join(
            [
                "Intro line.",
                "```bash",
                "bash scripts/run.sh",
                "echo `src/should_not_be_pathchecked.py`",
                "```",
                "Outro.",
            ]
        )
        claude_md = _write_claude_md(tmp_path, body)
        results = check_claude_md(str(claude_md), str(tmp_path))

        # The command scanner should verify scripts/run.sh ...
        command_checks = [r for r in results if r.check_type == "command"]
        assert any(r.verified for r in command_checks)
        # ... and nothing should be reported as a regular-line path from inside
        # the fenced block.
        path_instructions = {r.instruction for r in results if r.check_type == "path"}
        assert "src/should_not_be_pathchecked.py" not in path_instructions
