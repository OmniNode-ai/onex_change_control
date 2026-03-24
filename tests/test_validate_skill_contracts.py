# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for validate_skill_contracts.py — skill contract parity checks."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from scripts.validation.validate_skill_contracts import (
    check_args_parity,
    check_duplicate_frontmatter,
    check_spec_prompt_predicates,
    check_sub_skill_args,
    check_sub_skill_exists,
)


@pytest.fixture
def tmp_skill(tmp_path: Path) -> Path:
    """Create a minimal skill directory with SKILL.md and prompt.md."""
    skill_dir = tmp_path / "my_skill"
    skill_dir.mkdir()
    return skill_dir


def _write_skill_md(skill_dir: Path, content: str) -> Path:
    p = skill_dir / "SKILL.md"
    p.write_text(textwrap.dedent(content))
    return p


def _write_prompt_md(skill_dir: Path, content: str) -> Path:
    p = skill_dir / "prompt.md"
    p.write_text(textwrap.dedent(content))
    return p


# ── Task 1: Args-parity tests ───────────────────────────────────────────


class TestArgsParity:
    """Check 1: SKILL.md frontmatter args must appear in prompt.md."""

    def test_matching_args_no_violations(self, tmp_skill: Path) -> None:
        _write_skill_md(
            tmp_skill,
            """\
            ---
            description: test skill
            args:
              - name: --dry-run
                description: preview mode
                required: false
              - name: --repo
                description: target repo
                required: true
            ---
            # Test
            """,
        )
        _write_prompt_md(
            tmp_skill,
            """\
            Parse arguments:
            - `--dry-run`: if set, skip mutations
            - `--repo`: target repository
            """,
        )
        violations = check_args_parity(tmp_skill)
        assert len(violations) == 0

    def test_missing_arg_in_prompt_is_error(self, tmp_skill: Path) -> None:
        _write_skill_md(
            tmp_skill,
            """\
            ---
            description: test skill
            args:
              - name: --dry-run
                description: preview mode
                required: false
              - name: --repo
                description: target repo
                required: true
              - name: --label
                description: filter by label
                required: false
            ---
            # Test
            """,
        )
        _write_prompt_md(
            tmp_skill,
            """\
            Parse arguments:
            - `--dry-run`: if set, skip mutations
            - `--repo`: target repository
            """,
        )
        violations = check_args_parity(tmp_skill)
        assert len(violations) == 1
        assert violations[0].check == "args-parity"
        assert "--label" in violations[0].message

    def test_no_frontmatter_args_no_violations(self, tmp_skill: Path) -> None:
        _write_skill_md(
            tmp_skill,
            """\
            ---
            description: test skill
            ---
            # Test
            """,
        )
        _write_prompt_md(tmp_skill, "# Prompt\nDo stuff.")
        violations = check_args_parity(tmp_skill)
        assert len(violations) == 0

    def test_no_prompt_md_is_warning(self, tmp_skill: Path) -> None:
        _write_skill_md(
            tmp_skill,
            """\
            ---
            description: test skill
            args:
              - name: --dry-run
                description: preview
                required: false
            ---
            # Test
            """,
        )
        violations = check_args_parity(tmp_skill)
        assert len(violations) == 1
        assert violations[0].severity == "WARNING"


# ── Task 2: Sub-skill-exists and sub-skill-args tests ───────────────────


class TestSubSkillExists:
    """Check 2: Skill(skill='onex:X') refs must point to real dirs."""

    def test_valid_sub_skill_reference(self, tmp_path: Path) -> None:
        skills_root = tmp_path / "skills"
        skills_root.mkdir()
        parent = skills_root / "merge_sweep"
        parent.mkdir()
        child = skills_root / "pr_polish"
        child.mkdir()
        (child / "SKILL.md").write_text("---\ndescription: polish\n---\n# PR Polish")

        _write_prompt_md(parent, 'Dispatch: Skill(skill="onex:pr-polish", args="...")')
        violations = check_sub_skill_exists(parent, skills_root)
        assert len(violations) == 0

    def test_missing_sub_skill_is_error(self, tmp_path: Path) -> None:
        skills_root = tmp_path / "skills"
        skills_root.mkdir()
        parent = skills_root / "merge_sweep"
        parent.mkdir()

        _write_prompt_md(parent, 'Dispatch: Skill(skill="onex:nonexistent-skill")')
        violations = check_sub_skill_exists(parent, skills_root)
        assert len(violations) == 1
        assert violations[0].check == "sub-skill-exists"
        assert "nonexistent-skill" in violations[0].message

    def test_no_prompt_md_no_violations(self, tmp_path: Path) -> None:
        skills_root = tmp_path / "skills"
        skills_root.mkdir()
        parent = skills_root / "merge_sweep"
        parent.mkdir()
        violations = check_sub_skill_exists(parent, skills_root)
        assert len(violations) == 0


class TestSubSkillArgs:
    """Check 3: Dispatched sub-skill args must match target SKILL.md."""

    def test_compatible_args_no_violations(self, tmp_path: Path) -> None:
        skills_root = tmp_path / "skills"
        skills_root.mkdir()
        parent = skills_root / "merge_sweep"
        parent.mkdir()
        child = skills_root / "pr_polish"
        child.mkdir()
        _write_skill_md(
            child,
            """\
            ---
            description: polish
            args:
              - name: pr_number
                description: PR number
                required: true
              - name: --required-clean-runs
                description: clean passes
                required: false
            ---
            # PR Polish
            """,
        )
        _write_prompt_md(
            parent,
            'Skill(skill="onex:pr-polish", args="<N> --required-clean-runs 2")',
        )
        violations = check_sub_skill_args(parent, skills_root)
        assert len(violations) == 0

    def test_unknown_arg_dispatched_is_warning(self, tmp_path: Path) -> None:
        skills_root = tmp_path / "skills"
        skills_root.mkdir()
        parent = skills_root / "merge_sweep"
        parent.mkdir()
        child = skills_root / "pr_polish"
        child.mkdir()
        _write_skill_md(
            child,
            """\
            ---
            description: polish
            args:
              - name: pr_number
                description: PR number
                required: true
            ---
            # PR Polish
            """,
        )
        _write_prompt_md(
            parent,
            'Skill(skill="onex:pr-polish", args="<N> --unknown-flag foo")',
        )
        violations = check_sub_skill_args(parent, skills_root)
        assert len(violations) == 1
        assert violations[0].check == "sub-skill-args"
        assert "--unknown-flag" in violations[0].message


# ── Task 3: Duplicate-frontmatter and spec-prompt-predicate tests ────────


class TestDuplicateFrontmatter:
    """Check 4: No duplicate keys in SKILL.md frontmatter."""

    def test_duplicate_key_is_error(self, tmp_skill: Path) -> None:
        # Write raw (not through helper) to preserve duplicate keys
        (tmp_skill / "SKILL.md").write_text(
            "---\ndescription: test\nmode: full\nlevel: basic\nmode: full\n---\n# Test"
        )
        violations = check_duplicate_frontmatter(tmp_skill)
        assert len(violations) == 1
        assert violations[0].check == "duplicate-frontmatter"
        assert "mode" in violations[0].message

    def test_no_duplicates_clean(self, tmp_skill: Path) -> None:
        _write_skill_md(
            tmp_skill,
            """\
            ---
            description: test
            mode: full
            level: basic
            ---
            # Test
            """,
        )
        violations = check_duplicate_frontmatter(tmp_skill)
        assert len(violations) == 0


class TestSpecPromptPredicates:
    """Check 5: Key predicates/constants in SKILL.md must appear in prompt.md."""

    def test_status_value_missing_from_prompt_is_warning(self, tmp_skill: Path) -> None:
        _write_skill_md(
            tmp_skill,
            """\
            ---
            description: test
            ---
            Status values: `queued`, `partial`, `error`, `nothing_to_merge`
            """,
        )
        _write_prompt_md(
            tmp_skill,
            """\
            Emit status: queued or partial or error
            """,
        )
        violations = check_spec_prompt_predicates(tmp_skill)
        # nothing_to_merge is in SKILL.md but not in prompt.md
        assert any("nothing_to_merge" in v.message for v in violations)

    def test_all_status_values_present_clean(self, tmp_skill: Path) -> None:
        _write_skill_md(
            tmp_skill,
            """\
            ---
            description: test
            ---
            Status values: `queued`, `partial`
            """,
        )
        _write_prompt_md(
            tmp_skill,
            """\
            Emit status: queued or partial
            """,
        )
        violations = check_spec_prompt_predicates(tmp_skill)
        assert len(violations) == 0
