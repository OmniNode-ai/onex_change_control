#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""
ONEX Skill Hygiene Validator v1.0

Cross-repo validator that enforces skill directory naming, structure, and
separation of Python code from skill declarations. Designed to run against
any repository's skill tree (e.g. omniclaude/plugins/onex/skills/).

Checks (7):
  1. underscore-names      — Skill dir names must use underscores, not dashes
  2. no-duplicate-names    — No two dirs normalize to the same name
  3. no-unindexed-nesting  — Parent+child SKILL.md requires index: true in parent
  4. skill-md-required     — Every skill dir must contain SKILL.md
  5. name-matches-dir      — SKILL.md name: frontmatter must match dir name
  6. no-python-in-skills   — No .py files in skill dirs; Python in _-prefixed only
  7. no-orphan-topics      — topics.yaml without SKILL.md is suspicious

Severity:
  Without --strict: checks #4 and #5 are WARNING (migration mode).
  With --strict:    checks #4 and #5 are promoted to ERROR (steady-state CI).

Directory grammar:
  - Skill directory:          no _ prefix, contains SKILL.md + prompt.md + topics.yaml
  - Infrastructure directory: _ prefix (_lib/, _shared/, _bin/, etc.), may contain .py

Exit codes:
  0 — pass (no errors, warnings are non-fatal)
  1 — errors found
  2 — script error (bad args, missing path, etc.)

Usage:
  python scripts/validation/validate_skill_hygiene.py --skills-root path/to/skills
  python scripts/validation/validate_skill_hygiene.py --skills-root path/to/skills --strict
  python scripts/validation/validate_skill_hygiene.py --skills-root path/to/skills --json

Linear tickets: OMN-5200
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"

CHECK_UNDERSCORE_NAMES = "underscore-names"
CHECK_NO_DUPLICATE_NAMES = "no-duplicate-names"
CHECK_NO_UNINDEXED_NESTING = "no-unindexed-nesting"
CHECK_SKILL_MD_REQUIRED = "skill-md-required"
CHECK_NAME_MATCHES_DIR = "name-matches-dir"
CHECK_NO_PYTHON_IN_SKILLS = "no-python-in-skills"
CHECK_NO_ORPHAN_TOPICS = "no-orphan-topics"

# Checks that are WARNING in migration mode, ERROR in --strict mode
STRICT_PROMOTED_CHECKS = {CHECK_SKILL_MD_REQUIRED, CHECK_NAME_MATCHES_DIR}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class HygieneViolation:
    """A single skill hygiene violation."""

    path: str
    check: str
    severity: str
    message: str

    def format_line(self) -> str:
        return f"  {self.severity}: [{self.check}] {self.path}: {self.message}"

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "check": self.check,
            "severity": self.severity,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Infrastructure directory detection
# ---------------------------------------------------------------------------


def is_infrastructure_dir(name: str) -> bool:
    """Return True if the directory name indicates an infrastructure dir.

    Infrastructure directories start with _ (e.g. _lib, _shared, _bin,
    _golden_path_validate). They may contain Python freely and are
    skipped by all skill checks.
    """
    return name.startswith("_")


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def parse_skill_md_frontmatter(skill_md_path: Path) -> dict[str, str]:
    """Parse YAML frontmatter from a SKILL.md file.

    Returns a dict of key-value pairs from the --- delimited frontmatter.
    Only handles simple key: value pairs (no nested YAML).
    """
    result: dict[str, str] = {}
    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except OSError:
        return result

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return result

    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            # Handle simple values only (not arrays/objects)
            if key and not value.startswith("[") and not value.startswith("{"):
                result[key] = value
            elif key and value.startswith("["):
                # Store array values as-is for completeness
                result[key] = value

    return result


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------


def check_underscore_names(skills_root: Path, strict: bool) -> list[HygieneViolation]:
    """Check #1: Skill dir names must use underscores, not dashes."""
    violations: list[HygieneViolation] = []

    for entry in sorted(skills_root.iterdir()):
        if not entry.is_dir() or is_infrastructure_dir(entry.name):
            continue
        if "-" in entry.name:
            violations.append(
                HygieneViolation(
                    path=entry.name,
                    check=CHECK_UNDERSCORE_NAMES,
                    severity=SEVERITY_ERROR,
                    message=f"Skill directory uses dashes: '{entry.name}' — rename to '{entry.name.replace('-', '_')}'",
                )
            )
        # Also check nested child dirs
        for child in sorted(entry.iterdir()):
            if not child.is_dir() or is_infrastructure_dir(child.name):
                continue
            if "-" in child.name:
                violations.append(
                    HygieneViolation(
                        path=f"{entry.name}/{child.name}",
                        check=CHECK_UNDERSCORE_NAMES,
                        severity=SEVERITY_ERROR,
                        message=f"Nested skill directory uses dashes: '{child.name}' — rename to '{child.name.replace('-', '_')}'",
                    )
                )

    return violations


def check_no_duplicate_names(skills_root: Path, strict: bool) -> list[HygieneViolation]:
    """Check #2: No two dirs normalize to the same name after - → _ ."""
    violations: list[HygieneViolation] = []

    names: dict[str, list[str]] = {}
    for entry in sorted(skills_root.iterdir()):
        if not entry.is_dir() or is_infrastructure_dir(entry.name):
            continue
        normalized = entry.name.replace("-", "_")
        names.setdefault(normalized, []).append(entry.name)

    for normalized, originals in sorted(names.items()):
        if len(originals) > 1:
            violations.append(
                HygieneViolation(
                    path=", ".join(originals),
                    check=CHECK_NO_DUPLICATE_NAMES,
                    severity=SEVERITY_ERROR,
                    message=f"Directories normalize to same name '{normalized}': {originals}",
                )
            )

    return violations


def check_no_unindexed_nesting(
    skills_root: Path, strict: bool
) -> list[HygieneViolation]:
    """Check #3: Parent with SKILL.md + child dirs with SKILL.md must have index: true."""
    violations: list[HygieneViolation] = []

    for entry in sorted(skills_root.iterdir()):
        if not entry.is_dir() or is_infrastructure_dir(entry.name):
            continue

        parent_skill_md = entry / "SKILL.md"
        if not parent_skill_md.exists():
            continue

        # Check if any child dirs have SKILL.md
        has_child_skills = False
        for child in sorted(entry.iterdir()):
            if child.is_dir() and not is_infrastructure_dir(child.name):
                if (child / "SKILL.md").exists():
                    has_child_skills = True
                    break

        if not has_child_skills:
            continue

        # Parent has SKILL.md and children have SKILL.md — check for index: true
        frontmatter = parse_skill_md_frontmatter(parent_skill_md)
        index_val = frontmatter.get("index", "").lower()
        if index_val != "true":
            violations.append(
                HygieneViolation(
                    path=entry.name,
                    check=CHECK_NO_UNINDEXED_NESTING,
                    severity=SEVERITY_ERROR,
                    message=f"Parent dir '{entry.name}' has SKILL.md with child skill dirs but missing 'index: true' in frontmatter",
                )
            )

    return violations


def _collect_skill_dirs(skills_root: Path) -> list[tuple[Path, str]]:
    """Collect all non-infrastructure dirs that should be skill dirs.

    Returns (dir_path, display_name) tuples. Includes nested children.
    """
    result: list[tuple[Path, str]] = []
    for entry in sorted(skills_root.iterdir()):
        if not entry.is_dir() or is_infrastructure_dir(entry.name):
            continue

        # Check if this is an index parent (has child skill dirs)
        has_child_skills = False
        for child in sorted(entry.iterdir()):
            if child.is_dir() and not is_infrastructure_dir(child.name):
                if (child / "SKILL.md").exists():
                    has_child_skills = True
                    # Add child as a skill dir
                    result.append((child, f"{entry.name}/{child.name}"))

        if not has_child_skills:
            # Leaf skill dir
            result.append((entry, entry.name))
        # else: index parent — not itself a skill to validate for SKILL.md requirement

    return result


def check_skill_md_required(skills_root: Path, strict: bool) -> list[HygieneViolation]:
    """Check #4: Every non-index skill dir must have SKILL.md."""
    violations: list[HygieneViolation] = []
    severity = SEVERITY_ERROR if strict else SEVERITY_WARNING

    for dir_path, display_name in _collect_skill_dirs(skills_root):
        if not (dir_path / "SKILL.md").exists():
            violations.append(
                HygieneViolation(
                    path=display_name,
                    check=CHECK_SKILL_MD_REQUIRED,
                    severity=severity,
                    message=f"Skill directory '{display_name}' is missing SKILL.md",
                )
            )

    return violations


def check_name_matches_dir(skills_root: Path, strict: bool) -> list[HygieneViolation]:
    """Check #5: SKILL.md name: frontmatter must match directory name."""
    violations: list[HygieneViolation] = []
    severity = SEVERITY_ERROR if strict else SEVERITY_WARNING

    for entry in sorted(skills_root.iterdir()):
        if not entry.is_dir() or is_infrastructure_dir(entry.name):
            continue

        skill_md = entry / "SKILL.md"
        if skill_md.exists():
            frontmatter = parse_skill_md_frontmatter(skill_md)
            name = frontmatter.get("name", "")
            if name and name != entry.name:
                violations.append(
                    HygieneViolation(
                        path=entry.name,
                        check=CHECK_NAME_MATCHES_DIR,
                        severity=severity,
                        message=f"SKILL.md name: '{name}' does not match directory name '{entry.name}'",
                    )
                )

        # Check nested children too
        for child in sorted(entry.iterdir()):
            if not child.is_dir() or is_infrastructure_dir(child.name):
                continue
            child_skill_md = child / "SKILL.md"
            if child_skill_md.exists():
                frontmatter = parse_skill_md_frontmatter(child_skill_md)
                name = frontmatter.get("name", "")
                if name and name != child.name:
                    violations.append(
                        HygieneViolation(
                            path=f"{entry.name}/{child.name}",
                            check=CHECK_NAME_MATCHES_DIR,
                            severity=severity,
                            message=f"SKILL.md name: '{name}' does not match directory name '{child.name}'",
                        )
                    )

    return violations


def check_no_python_in_skills(
    skills_root: Path, strict: bool
) -> list[HygieneViolation]:
    """Check #6: No .py files in skill dirs. Python belongs in _-prefixed dirs only."""
    violations: list[HygieneViolation] = []

    for py_file in sorted(skills_root.rglob("*.py")):
        # Get the relative path parts from skills_root
        rel = py_file.relative_to(skills_root)
        parts = rel.parts

        # Skip if any ancestor dir is infrastructure-prefixed
        in_infra = False
        for part in parts[:-1]:  # check dir components, not the file itself
            if is_infrastructure_dir(part):
                in_infra = True
                break

        if in_infra:
            continue

        # Skip top-level __init__.py (the skills package itself)
        if len(parts) == 1 and parts[0] == "__init__.py":
            continue

        violations.append(
            HygieneViolation(
                path=str(rel),
                check=CHECK_NO_PYTHON_IN_SKILLS,
                severity=SEVERITY_ERROR,
                message=f"Python file in skill directory: {rel} — move to a _-prefixed infrastructure dir (e.g. _lib/)",
            )
        )

    return violations


def check_no_orphan_topics(skills_root: Path, strict: bool) -> list[HygieneViolation]:
    """Check #7: topics.yaml without SKILL.md in same dir is suspicious."""
    violations: list[HygieneViolation] = []

    for topics_file in sorted(skills_root.rglob("topics.yaml")):
        parent = topics_file.parent
        rel = topics_file.relative_to(skills_root)

        # Skip infrastructure dirs
        parts = rel.parts
        in_infra = False
        for part in parts[:-1]:
            if is_infrastructure_dir(part):
                in_infra = True
                break
        if in_infra:
            continue

        if not (parent / "SKILL.md").exists():
            violations.append(
                HygieneViolation(
                    path=str(rel),
                    check=CHECK_NO_ORPHAN_TOPICS,
                    severity=SEVERITY_WARNING,
                    message=f"topics.yaml found without SKILL.md in same directory: {parent.name}/",
                )
            )

    return violations


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_CHECKS = [
    check_underscore_names,
    check_no_duplicate_names,
    check_no_unindexed_nesting,
    check_skill_md_required,
    check_name_matches_dir,
    check_no_python_in_skills,
    check_no_orphan_topics,
]


@dataclass
class ValidationResult:
    """Aggregated results from all checks."""

    violations: list[HygieneViolation] = field(default_factory=list)

    @property
    def errors(self) -> list[HygieneViolation]:
        return [v for v in self.violations if v.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[HygieneViolation]:
        return [v for v in self.violations if v.severity == SEVERITY_WARNING]

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


def validate_skill_hygiene(
    skills_root: Path, *, strict: bool = False
) -> ValidationResult:
    """Run all hygiene checks against a skills directory tree.

    Args:
        skills_root: Path to the skills directory (e.g. plugins/onex/skills/).
        strict: If True, promote checks #4 and #5 from WARNING to ERROR.

    Returns:
        ValidationResult with all violations.
    """
    result = ValidationResult()
    for check_fn in ALL_CHECKS:
        result.violations.extend(check_fn(skills_root, strict))
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate skill directory hygiene",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skills-root",
        required=True,
        type=Path,
        help="Path to the skills directory tree",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Promote migration warnings (checks #4, #5) to errors",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON",
    )

    args = parser.parse_args(argv)

    if not args.skills_root.is_dir():
        print(
            f"ERROR: --skills-root path does not exist or is not a directory: {args.skills_root}",
            file=sys.stderr,
        )
        return 2

    result = validate_skill_hygiene(args.skills_root, strict=args.strict)

    if args.json_output:
        output = {
            "strict": args.strict,
            "skills_root": str(args.skills_root),
            "error_count": len(result.errors),
            "warning_count": len(result.warnings),
            "violations": [v.to_dict() for v in result.violations],
        }
        print(json.dumps(output, indent=2))
    elif result.violations:
        print(f"\nSkill Hygiene Report ({args.skills_root}):")
        print(f"  Mode: {'strict' if args.strict else 'migration'}")
        print()

        for v in result.violations:
            print(v.format_line())

        print()
        print(f"  Errors: {len(result.errors)}, Warnings: {len(result.warnings)}")
    else:
        print(f"Skill hygiene: all checks passed ({args.skills_root})")

    if result.has_errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
