# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the skill hygiene validator."""

from __future__ import annotations

from typing import TYPE_CHECKING

from scripts.validation.validate_skill_hygiene import (
    CHECK_NAME_MATCHES_DIR,
    CHECK_NO_DUPLICATE_NAMES,
    CHECK_NO_ORPHAN_TOPICS,
    CHECK_NO_PYTHON_IN_SKILLS,
    CHECK_NO_UNINDEXED_NESTING,
    CHECK_SKILL_MD_REQUIRED,
    CHECK_UNDERSCORE_NAMES,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    HygieneViolation,
    main,
    validate_skill_hygiene,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(
    path: Path, name: str | None = None, extra_frontmatter: str = ""
) -> None:
    """Create a minimal SKILL.md in the given directory."""
    path.mkdir(parents=True, exist_ok=True)
    dir_name = name or path.name
    content = (
        f"---\nname: {dir_name}\ndescription: test\n"
        f"level: basic\ndebug: false\n"
        f"{extra_frontmatter}---\n\nPrompt content.\n"
    )
    (path / "SKILL.md").write_text(content)


def _make_dir(path: Path) -> None:
    """Create a directory without SKILL.md."""
    path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Check #1: underscore-names
# ---------------------------------------------------------------------------


class TestUnderscoreNames:
    def test_clean_underscore_dirs(self, tmp_path: Path) -> None:
        _make_skill(tmp_path / "epic_team")
        _make_skill(tmp_path / "gap")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_UNDERSCORE_NAMES]
        assert len(errs) == 0

    def test_kebab_case_is_error(self, tmp_path: Path) -> None:
        _make_skill(tmp_path / "epic-team")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_UNDERSCORE_NAMES]
        assert len(errs) == 1
        assert errs[0].severity == SEVERITY_ERROR
        assert "epic-team" in errs[0].message
        assert "epic_team" in errs[0].message

    def test_nested_kebab_case(self, tmp_path: Path) -> None:
        parent = tmp_path / "routing"
        _make_skill(parent, extra_frontmatter="index: true\n")
        child = parent / "request-agent-routing"
        _make_skill(child)
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_UNDERSCORE_NAMES]
        assert any("request-agent-routing" in v.message for v in errs)

    def test_infra_dirs_skipped(self, tmp_path: Path) -> None:
        _make_dir(tmp_path / "_lib")
        _make_dir(tmp_path / "_shared")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_UNDERSCORE_NAMES]
        assert len(errs) == 0


# ---------------------------------------------------------------------------
# Check #2: no-duplicate-names
# ---------------------------------------------------------------------------


class TestNoDuplicateNames:
    def test_no_duplicates(self, tmp_path: Path) -> None:
        _make_skill(tmp_path / "epic_team")
        _make_skill(tmp_path / "gap")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_DUPLICATE_NAMES]
        assert len(errs) == 0

    def test_dash_underscore_collision(self, tmp_path: Path) -> None:
        _make_skill(tmp_path / "generate-ticket-contract")
        _make_dir(tmp_path / "generate_ticket_contract")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_DUPLICATE_NAMES]
        assert len(errs) == 1
        assert errs[0].severity == SEVERITY_ERROR


# ---------------------------------------------------------------------------
# Check #3: no-unindexed-nesting
# ---------------------------------------------------------------------------


class TestNoUnindexedNesting:
    def test_indexed_parent_is_clean(self, tmp_path: Path) -> None:
        parent = tmp_path / "routing"
        _make_skill(parent, extra_frontmatter="index: true\n")
        _make_skill(parent / "child_skill")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_UNINDEXED_NESTING]
        assert len(errs) == 0

    def test_unindexed_parent_is_error(self, tmp_path: Path) -> None:
        parent = tmp_path / "routing"
        _make_skill(parent)  # no index: true
        _make_skill(parent / "child_skill")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_UNINDEXED_NESTING]
        assert len(errs) == 1
        assert errs[0].severity == SEVERITY_ERROR

    def test_parent_without_children_is_fine(self, tmp_path: Path) -> None:
        _make_skill(tmp_path / "standalone")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_UNINDEXED_NESTING]
        assert len(errs) == 0


# ---------------------------------------------------------------------------
# Check #4: skill-md-required
# ---------------------------------------------------------------------------


class TestSkillMdRequired:
    def test_dir_with_skill_md(self, tmp_path: Path) -> None:
        _make_skill(tmp_path / "gap")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_SKILL_MD_REQUIRED]
        assert len(errs) == 0

    def test_dir_without_skill_md_is_warning(self, tmp_path: Path) -> None:
        _make_dir(tmp_path / "mystery_dir")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_SKILL_MD_REQUIRED]
        assert len(errs) == 1
        assert errs[0].severity == SEVERITY_WARNING

    def test_strict_promotes_to_error(self, tmp_path: Path) -> None:
        _make_dir(tmp_path / "mystery_dir")
        result = validate_skill_hygiene(tmp_path, strict=True)
        errs = [v for v in result.violations if v.check == CHECK_SKILL_MD_REQUIRED]
        assert len(errs) == 1
        assert errs[0].severity == SEVERITY_ERROR

    def test_infra_dir_not_checked(self, tmp_path: Path) -> None:
        _make_dir(tmp_path / "_lib")
        result = validate_skill_hygiene(tmp_path, strict=True)
        errs = [v for v in result.violations if v.check == CHECK_SKILL_MD_REQUIRED]
        assert len(errs) == 0


# ---------------------------------------------------------------------------
# Check #5: name-matches-dir
# ---------------------------------------------------------------------------


class TestNameMatchesDir:
    def test_matching_name(self, tmp_path: Path) -> None:
        _make_skill(tmp_path / "epic_team", name="epic_team")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NAME_MATCHES_DIR]
        assert len(errs) == 0

    def test_mismatched_name_is_warning(self, tmp_path: Path) -> None:
        _make_skill(tmp_path / "epic_team", name="epic-team")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NAME_MATCHES_DIR]
        assert len(errs) == 1
        assert errs[0].severity == SEVERITY_WARNING

    def test_strict_promotes_to_error(self, tmp_path: Path) -> None:
        _make_skill(tmp_path / "epic_team", name="wrong_name")
        result = validate_skill_hygiene(tmp_path, strict=True)
        errs = [v for v in result.violations if v.check == CHECK_NAME_MATCHES_DIR]
        assert len(errs) == 1
        assert errs[0].severity == SEVERITY_ERROR

    def test_nested_child_name_mismatch(self, tmp_path: Path) -> None:
        parent = tmp_path / "routing"
        _make_skill(parent, extra_frontmatter="index: true\n")
        _make_skill(parent / "child_skill", name="wrong_child")
        result = validate_skill_hygiene(tmp_path, strict=True)
        errs = [v for v in result.violations if v.check == CHECK_NAME_MATCHES_DIR]
        assert any("wrong_child" in v.message for v in errs)


# ---------------------------------------------------------------------------
# Check #6: no-python-in-skills
# ---------------------------------------------------------------------------


class TestNoPythonInSkills:
    def test_clean_skill_dir(self, tmp_path: Path) -> None:
        _make_skill(tmp_path / "gap")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_PYTHON_IN_SKILLS]
        assert len(errs) == 0

    def test_python_in_skill_dir_is_error(self, tmp_path: Path) -> None:
        skill = tmp_path / "gap"
        _make_skill(skill)
        (skill / "helper.py").write_text("# bad\n")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_PYTHON_IN_SKILLS]
        assert len(errs) == 1
        assert errs[0].severity == SEVERITY_ERROR
        assert "helper.py" in errs[0].message

    def test_python_in_infra_dir_is_fine(self, tmp_path: Path) -> None:
        lib = tmp_path / "_lib" / "gap"
        lib.mkdir(parents=True)
        (lib / "models.py").write_text("# ok\n")
        (lib / "__init__.py").write_text("")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_PYTHON_IN_SKILLS]
        assert len(errs) == 0

    def test_toplevel_init_py_allowed(self, tmp_path: Path) -> None:
        (tmp_path / "__init__.py").write_text("")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_PYTHON_IN_SKILLS]
        assert len(errs) == 0

    def test_nested_python_in_skill_subdir(self, tmp_path: Path) -> None:
        skill = tmp_path / "system_status" / "check_health"
        skill.mkdir(parents=True)
        (skill / "execute.py").write_text("# bad\n")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_PYTHON_IN_SKILLS]
        assert len(errs) == 1


# ---------------------------------------------------------------------------
# Check #7: no-orphan-topics
# ---------------------------------------------------------------------------


class TestNoOrphanTopics:
    def test_topics_with_skill_md(self, tmp_path: Path) -> None:
        skill = tmp_path / "gap"
        _make_skill(skill)
        (skill / "topics.yaml").write_text("topics: []\n")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_ORPHAN_TOPICS]
        assert len(errs) == 0

    def test_orphan_topics_is_warning(self, tmp_path: Path) -> None:
        orphan = tmp_path / "mystery"
        orphan.mkdir()
        (orphan / "topics.yaml").write_text("topics: []\n")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_ORPHAN_TOPICS]
        assert len(errs) == 1
        assert errs[0].severity == SEVERITY_WARNING

    def test_topics_in_infra_dir_skipped(self, tmp_path: Path) -> None:
        infra = tmp_path / "_lib" / "something"
        infra.mkdir(parents=True)
        (infra / "topics.yaml").write_text("topics: []\n")
        result = validate_skill_hygiene(tmp_path)
        errs = [v for v in result.violations if v.check == CHECK_NO_ORPHAN_TOPICS]
        assert len(errs) == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_clean_exit_zero(self, tmp_path: Path) -> None:
        _make_skill(tmp_path / "gap")
        assert main(["--skills-root", str(tmp_path)]) == 0

    def test_errors_exit_one(self, tmp_path: Path) -> None:
        _make_skill(tmp_path / "kebab-case")
        assert main(["--skills-root", str(tmp_path)]) == 1

    def test_invalid_path_exit_two(self, tmp_path: Path) -> None:
        assert main(["--skills-root", str(tmp_path / "nonexistent")]) == 2

    def test_strict_mode(self, tmp_path: Path) -> None:
        _make_dir(tmp_path / "no_skill_md")
        # Without strict: warning only, exit 0
        assert main(["--skills-root", str(tmp_path)]) == 0
        # With strict: promoted to error, exit 1
        assert main(["--skills-root", str(tmp_path), "--strict"]) == 1

    def test_json_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_skill(tmp_path / "kebab-case")
        main(["--skills-root", str(tmp_path), "--json"])
        import json

        output = json.loads(capsys.readouterr().out)
        assert output["error_count"] > 0
        assert isinstance(output["violations"], list)


# ---------------------------------------------------------------------------
# HygieneViolation
# ---------------------------------------------------------------------------


class TestHygieneViolation:
    def test_format_line(self) -> None:
        v = HygieneViolation(
            path="epic-team",
            check="underscore-names",
            severity="ERROR",
            message="uses dashes",
        )
        line = v.format_line()
        assert "ERROR" in line
        assert "underscore-names" in line
        assert "epic-team" in line

    def test_to_dict(self) -> None:
        v = HygieneViolation(
            path="test",
            check="test-check",
            severity="WARNING",
            message="test msg",
        )
        d = v.to_dict()
        assert d["path"] == "test"
        assert d["severity"] == "WARNING"


# ---------------------------------------------------------------------------
# Integration: all checks together
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_fully_clean_tree(self, tmp_path: Path) -> None:
        """A well-structured skill tree passes all checks."""
        _make_skill(tmp_path / "epic_team")
        _make_skill(tmp_path / "gap")
        parent = tmp_path / "routing"
        _make_skill(parent, extra_frontmatter="index: true\n")
        _make_skill(parent / "request_agent_routing")
        _make_dir(tmp_path / "_lib")
        (tmp_path / "_lib" / "helper.py").write_text("# fine\n")
        (tmp_path / "__init__.py").write_text("")

        result = validate_skill_hygiene(tmp_path, strict=True)
        assert len(result.errors) == 0
        assert len(result.warnings) == 0

    def test_multiple_violations(self, tmp_path: Path) -> None:
        """A messy tree triggers multiple checks."""
        # kebab-case (check #1)
        _make_skill(tmp_path / "epic-team")
        # duplicate names (check #2)
        _make_dir(tmp_path / "epic_team")
        # python in skill dir (check #6)
        (tmp_path / "epic-team" / "helper.py").write_text("# bad\n")

        result = validate_skill_hygiene(tmp_path)
        checks_hit = {v.check for v in result.violations}
        assert CHECK_UNDERSCORE_NAMES in checks_hit
        assert CHECK_NO_DUPLICATE_NAMES in checks_hit
        assert CHECK_NO_PYTHON_IN_SKILLS in checks_hit
