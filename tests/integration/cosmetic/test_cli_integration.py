# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Integration tests for the cosmetic-lint CLI — full end-to-end flows."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


def _run_cli(
    *args: str,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the cosmetic-lint CLI as a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "onex_change_control.cosmetic.cli", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


def _create_clean_repo(tmp_path: Path) -> None:
    """Create a minimal clean repo structure that passes all checks."""
    # Python files with correct SPDX headers
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text(
        "# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.\n"
        "# SPDX-License-Identifier: MIT\n"
        "\n"
        "x = 1\n"
    )

    # Correct pyproject.toml
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\nversion = "0.1.0"\n'
        'authors = [{name = "OmniNode.ai", email = "jonah@omninode.ai"}]\n'
        'license = {text = "MIT"}\n'
        'requires-python = ">=3.12"\n'
        "classifiers = [\n"
        '    "Development Status :: 4 - Beta",\n'
        '    "License :: OSI Approved :: MIT License",\n'
        '    "Programming Language :: Python :: 3.12",\n'
        '    "Typing :: Typed",\n'
        "]\n\n"
        "[project.urls]\n"
        'Homepage = "https://omninode.ai"\n'
        'Repository = "https://github.com/OmniNode-ai/example"\n\n'
        "[tool.ruff]\n"
        'target-version = "py312"\n'
    )

    # Pre-commit config with correct versions
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n"
        "  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        "    rev: v0.15.6\n"
        "    hooks:\n"
        "      - id: ruff\n"
        "  - repo: https://github.com/google/yamlfmt\n"
        "    rev: v0.21.0\n"
        "    hooks:\n"
        "      - id: yamlfmt\n"
        "  - repo: https://github.com/pre-commit/pre-commit-hooks\n"
        "    rev: v6.0.0\n"
        "    hooks:\n"
        "      - id: trailing-whitespace\n"
    )

    # README with badges
    (tmp_path / "README.md").write_text(
        "# Test Project\n\n"
        "[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)\n"
        "[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)\n"
        "[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)\n"
        "[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)\n\n"
        "Description.\n"
    )

    # GitHub templates
    github = tmp_path / ".github"
    github.mkdir()
    (github / "PULL_REQUEST_TEMPLATE.md").write_text("## PR\n")
    wf = github / "workflows"
    wf.mkdir()
    (wf / "ci.yml").write_text("name: CI\n")


@pytest.mark.integration
class TestCLIIntegration:
    """Full CLI integration tests using subprocess."""

    def test_clean_repo_exits_zero(self, tmp_path: Path) -> None:
        """Clean repo with all checks passing exits 0."""
        _create_clean_repo(tmp_path)
        result = _run_cli("check", str(tmp_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_spdx_violation_exits_one(self, tmp_path: Path) -> None:
        """Repo with SPDX violations exits 1 with [spdx] in stderr."""
        _create_clean_repo(tmp_path)
        # Add a file without SPDX header
        (tmp_path / "src" / "bad.py").write_text("x = 1\n")
        result = _run_cli("check", str(tmp_path))
        assert result.returncode == 1
        assert "[spdx]" in result.stderr

    def test_fix_then_check_passes(self, tmp_path: Path) -> None:
        """Fix corrects violations, subsequent check passes."""
        _create_clean_repo(tmp_path)
        (tmp_path / "src" / "bad.py").write_text("x = 1\n")

        # Fix should exit 0 (all violations are fixable)
        fix_result = _run_cli("fix", str(tmp_path))
        assert fix_result.returncode == 0, f"fix stderr: {fix_result.stderr}"

        # Check should now pass
        check_result = _run_cli("check", str(tmp_path))
        assert check_result.returncode == 0, f"check stderr: {check_result.stderr}"

    def test_select_filters_checks(self, tmp_path: Path) -> None:
        """--select filters to only specified checks."""
        _create_clean_repo(tmp_path)
        # Add SPDX violation
        (tmp_path / "src" / "bad.py").write_text("x = 1\n")
        # Remove pyproject.toml URL keys (would be a pyproject violation)
        # But with --select spdx, only SPDX violations should appear

        result = _run_cli("check", "--select", "spdx", str(tmp_path))
        assert result.returncode == 1
        assert "[spdx]" in result.stderr
        assert "[pyproject]" not in result.stderr

    def test_custom_spec_override(self, tmp_path: Path) -> None:
        """Custom --spec overrides default values."""
        _create_clean_repo(tmp_path)
        # Write a custom spec with different copyright
        custom_spec = tmp_path / "custom-spec.yaml"
        custom_spec.write_text(
            "spdx:\n"
            '  copyright_text: "2026 Custom Corp"\n'
            '  license_identifier: "MIT"\n'
            '  file_patterns: ["*.py"]\n'
            "  exclude_patterns: []\n"
            "pyproject: {}\n"
            "precommit: {}\n"
            "readme: {}\n"
            "github: {}\n"
        )
        # The correct header from _create_clean_repo uses "2025 OmniNode.ai Inc."
        # which doesn't match the custom spec — should fail
        result = _run_cli(
            "check", "--spec", str(custom_spec), "--select", "spdx", str(tmp_path)
        )
        assert result.returncode == 1
        assert "2026 Custom Corp" in result.stderr

    def test_fix_with_nonfixable_violations_exits_one(self, tmp_path: Path) -> None:
        """Fix with remaining non-fixable violations exits 1."""
        _create_clean_repo(tmp_path)
        # Remove the PR template (non-fixable violation)
        (tmp_path / ".github" / "PULL_REQUEST_TEMPLATE.md").unlink()
        result = _run_cli("fix", str(tmp_path))
        assert result.returncode == 1
        assert "[github]" in result.stderr
