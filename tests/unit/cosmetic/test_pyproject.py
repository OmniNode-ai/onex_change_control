# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the pyproject.toml check and fix module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from onex_change_control.cosmetic.checks.pyproject import run_check

if TYPE_CHECKING:
    from pathlib import Path

SPEC = {
    "pyproject": {
        "author": {"name": "OmniNode.ai", "email": "jonah@omninode.ai"},
        "license_format": "table",
        "license_text": "MIT",
        "requires_python": ">=3.12",
        "classifiers": {
            "required": [
                "License :: OSI Approved :: MIT License",
                "Programming Language :: Python :: 3.12",
            ],
        },
        "url_keys": {
            "required": ["Homepage", "Repository"],
        },
        "ruff": {
            "required": True,
            "target_version": "py312",
        },
    },
}

CORRECT_PYPROJECT = """\
[project]
name = "my-pkg"
version = "0.1.0"
authors = [
    {name = "OmniNode.ai", email = "jonah@omninode.ai"},
]
license = {text = "MIT"}
requires-python = ">=3.12"
classifiers = [
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.12",
]

[project.urls]
Homepage = "https://omninode.ai"
Repository = "https://github.com/OmniNode-ai/example"

[tool.ruff]
target-version = "py312"
"""


@pytest.mark.unit
class TestPyprojectCheck:
    """Check-mode tests."""

    def test_correct_pyproject_passes(self, tmp_path: Path) -> None:
        """Fully compliant pyproject produces no violations."""
        (tmp_path / "pyproject.toml").write_text(CORRECT_PYPROJECT)
        violations = run_check(tmp_path, SPEC)
        assert violations == []

    def test_no_pyproject_skips(self, tmp_path: Path) -> None:
        """Missing pyproject.toml returns empty list."""
        violations = run_check(tmp_path, SPEC)
        assert violations == []

    def test_string_license_detected(self, tmp_path: Path) -> None:
        """Bare string license is flagged."""
        content = CORRECT_PYPROJECT.replace(
            'license = {text = "MIT"}', 'license = "MIT"'
        )
        (tmp_path / "pyproject.toml").write_text(content)
        violations = run_check(tmp_path, SPEC)
        assert any("table format" in v.message for v in violations)

    def test_wrong_author_detected(self, tmp_path: Path) -> None:
        """Wrong author is flagged."""
        content = CORRECT_PYPROJECT.replace("OmniNode.ai", "Wrong Corp").replace(
            "jonah@omninode.ai", "wrong@example.com"
        )
        (tmp_path / "pyproject.toml").write_text(content)
        violations = run_check(tmp_path, SPEC)
        assert any("author" in v.message.lower() for v in violations)

    def test_missing_ruff_section_detected(self, tmp_path: Path) -> None:
        """Missing [tool.ruff] is flagged."""
        content = CORRECT_PYPROJECT.replace(
            '[tool.ruff]\ntarget-version = "py312"\n', ""
        )
        (tmp_path / "pyproject.toml").write_text(content)
        violations = run_check(tmp_path, SPEC)
        assert any("ruff" in v.message.lower() for v in violations)

    def test_missing_classifiers_detected(self, tmp_path: Path) -> None:
        """Missing required classifiers are flagged."""
        content = CORRECT_PYPROJECT.replace(
            (
                "classifiers = [\n"
                '    "License :: OSI Approved :: MIT License",\n'
                '    "Programming Language :: Python :: 3.12",\n'
                "]"
            ),
            "classifiers = []",
        )
        (tmp_path / "pyproject.toml").write_text(content)
        violations = run_check(tmp_path, SPEC)
        assert any("classifier" in v.message.lower() for v in violations)

    def test_wrong_requires_python_detected(self, tmp_path: Path) -> None:
        """Wrong requires-python is flagged."""
        content = CORRECT_PYPROJECT.replace(
            'requires-python = ">=3.12"', 'requires-python = ">=3.10"'
        )
        (tmp_path / "pyproject.toml").write_text(content)
        violations = run_check(tmp_path, SPEC)
        assert any("requires-python" in v.message for v in violations)


@pytest.mark.unit
class TestPyprojectFix:
    """Fix-mode tests."""

    def test_fix_corrects_author(self, tmp_path: Path) -> None:
        """Fix mode corrects the author field."""
        content = CORRECT_PYPROJECT.replace("OmniNode.ai", "Wrong Corp").replace(
            "jonah@omninode.ai", "wrong@example.com"
        )
        (tmp_path / "pyproject.toml").write_text(content)
        run_check(tmp_path, SPEC, fix=True)
        import tomllib

        data = tomllib.loads((tmp_path / "pyproject.toml").read_text())
        authors = data["project"]["authors"]
        assert any(a["name"] == "OmniNode.ai" for a in authors)

    def test_fix_corrects_license(self, tmp_path: Path) -> None:
        """Fix mode sets license to table format."""
        content = CORRECT_PYPROJECT.replace(
            'license = {text = "MIT"}', 'license = "MIT"'
        )
        (tmp_path / "pyproject.toml").write_text(content)
        run_check(tmp_path, SPEC, fix=True)
        import tomllib

        data = tomllib.loads((tmp_path / "pyproject.toml").read_text())
        assert isinstance(data["project"]["license"], dict)
        assert data["project"]["license"]["text"] == "MIT"

    def test_fix_adds_missing_classifiers(self, tmp_path: Path) -> None:
        """Fix mode adds missing required classifiers without removing existing."""
        content = CORRECT_PYPROJECT.replace(
            (
                "classifiers = [\n"
                '    "License :: OSI Approved :: MIT License",\n'
                '    "Programming Language :: Python :: 3.12",\n'
                "]"
            ),
            'classifiers = ["Custom :: Classifier"]',
        )
        (tmp_path / "pyproject.toml").write_text(content)
        run_check(tmp_path, SPEC, fix=True)
        import tomllib

        data = tomllib.loads((tmp_path / "pyproject.toml").read_text())
        classifiers = data["project"]["classifiers"]
        assert "Custom :: Classifier" in classifiers
        assert "License :: OSI Approved :: MIT License" in classifiers
