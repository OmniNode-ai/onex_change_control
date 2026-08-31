# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the GitHub templates and workflow extension check module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from onex_change_control.cosmetic.checks.github import run_check

if TYPE_CHECKING:
    from pathlib import Path

SPEC = {
    "github": {
        "required_templates": ["PULL_REQUEST_TEMPLATE.md"],
        "workflow_extension": ".yml",
    },
}


@pytest.mark.unit
class TestGitHubCheck:
    """Check-mode tests."""

    def test_template_exists_passes(self, tmp_path: Path) -> None:
        """Existing PR template produces no template violations."""
        github = tmp_path / ".github"
        github.mkdir()
        (github / "PULL_REQUEST_TEMPLATE.md").write_text("## PR\n")
        violations = run_check(tmp_path, SPEC)
        assert not any(
            v.message.startswith("Missing required template") for v in violations
        )

    def test_missing_template_detected(self, tmp_path: Path) -> None:
        """Missing PR template is flagged as non-fixable."""
        github = tmp_path / ".github"
        github.mkdir()
        violations = run_check(tmp_path, SPEC)
        template_violations = [v for v in violations if "template" in v.message.lower()]
        assert len(template_violations) == 1
        assert not template_violations[0].fixable

    def test_no_github_dir_skips(self, tmp_path: Path) -> None:
        """Missing .github/ returns empty list."""
        violations = run_check(tmp_path, SPEC)
        assert violations == []

    def test_yaml_extension_detected(self, tmp_path: Path) -> None:
        """Workflow with .yaml extension is flagged."""
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yaml").write_text("name: CI\n")
        (tmp_path / ".github" / "PULL_REQUEST_TEMPLATE.md").write_text("## PR\n")
        violations = run_check(tmp_path, SPEC)
        ext_violations = [v for v in violations if ".yaml" in v.message]
        assert len(ext_violations) == 1
        assert ext_violations[0].fixable

    def test_yml_extension_passes(self, tmp_path: Path) -> None:
        """Workflow with .yml extension produces no extension violations."""
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text("name: CI\n")
        (tmp_path / ".github" / "PULL_REQUEST_TEMPLATE.md").write_text("## PR\n")
        violations = run_check(tmp_path, SPEC)
        assert violations == []


@pytest.mark.unit
class TestGitHubFix:
    """Fix-mode tests."""

    def test_fix_renames_yaml_to_yml(self, tmp_path: Path) -> None:
        """Fix mode renames .yaml workflow files to .yml."""
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yaml").write_text("name: CI\n")
        (tmp_path / ".github" / "PULL_REQUEST_TEMPLATE.md").write_text("## PR\n")
        run_check(tmp_path, SPEC, fix=True)
        assert (wf_dir / "ci.yml").exists()
        assert not (wf_dir / "ci.yaml").exists()

    def test_template_violation_not_fixed(self, tmp_path: Path) -> None:
        """Missing template remains unfixed (needs human content)."""
        github = tmp_path / ".github"
        github.mkdir()
        violations = run_check(tmp_path, SPEC, fix=True)
        template_violations = [v for v in violations if "template" in v.message.lower()]
        assert len(template_violations) == 1
        assert not (github / "PULL_REQUEST_TEMPLATE.md").exists()
