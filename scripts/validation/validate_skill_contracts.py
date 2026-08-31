#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""
ONEX Skill Contract Validator v1.0

Cross-repo validator that enforces argument parity, sub-skill reference
integrity, frontmatter uniqueness, and spec-prompt predicate coverage across
all skills. Designed to run against any repository's skill tree (e.g.
omniclaude/plugins/onex/skills/).

Checks (5):
  1. args-parity           — SKILL.md frontmatter args must appear in prompt.md
  2. sub-skill-exists      — Skill(skill="onex:X") refs must resolve to real dirs
  3. sub-skill-args        — Dispatched sub-skill args must match target's SKILL.md
  4. duplicate-frontmatter — No duplicate top-level keys in SKILL.md frontmatter
  5. spec-prompt-predicates — Backtick-quoted predicates in SKILL.md body must
                              appear in prompt.md

Severity:
  Without --strict: check #5 is WARNING.
  With --strict:    check #5 is promoted to ERROR.

Exit codes:
  0 — pass (no errors, warnings are non-fatal)
  1 — errors found
  2 — script error (bad args, missing path, etc.)

Usage:
  python scripts/validation/validate_skill_contracts.py --skills-root path/to/skills
  python scripts/validation/validate_skill_contracts.py --skills-root path/to/skills --strict
  python scripts/validation/validate_skill_contracts.py --skills-root path/to/skills --json

Linear tickets: OMN-6193
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"

CHECK_ARGS_PARITY = "args-parity"
CHECK_SUB_SKILL_EXISTS = "sub-skill-exists"
CHECK_SUB_SKILL_ARGS = "sub-skill-args"
CHECK_DUPLICATE_FRONTMATTER = "duplicate-frontmatter"
CHECK_SPEC_PROMPT_PREDICATES = "spec-prompt-predicates"

# Checks promoted from WARNING to ERROR in --strict mode
STRICT_PROMOTED_CHECKS = {CHECK_SPEC_PROMPT_PREDICATES}

# Regex for --flag style args in prompt text
RE_FLAG_IN_TEXT = re.compile(r"--[a-z][-a-z0-9]*")

# Regex for Skill(skill="onex:..." ...) or /onex:... references
RE_SUB_SKILL_REF = re.compile(
    r'(?:Skill\s*\(\s*skill\s*=\s*["\']onex:([a-z][-a-z0-9_]*)["\']'
    r"|/onex:([a-z][-a-z0-9_]*))"
)

# Regex for Skill(skill="onex:...", args="...") — captures the args string
RE_SUB_SKILL_DISPATCH = re.compile(
    r'Skill\s*\(\s*skill\s*=\s*["\']onex:([a-z][-a-z0-9_]*)["\']'
    r'[^)]*args\s*=\s*["\']([^"\']*)["\']'
)

# Regex for backtick-quoted identifiers in SKILL.md body
RE_BACKTICK_PREDICATE = re.compile(r"`([a-z][a-z0-9_]*(?:[-][a-z0-9_]+)*)`")

# Context keywords — predicates near these words are checked
PREDICATE_CONTEXT_KEYWORDS = {
    "status",
    "result",
    "predicate",
    "state",
    "values",
    "emit",
    "output",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class HygieneViolation:
    """A single skill contract violation."""

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
# Frontmatter parsing (stdlib only — no yaml dependency)
# ---------------------------------------------------------------------------


def parse_frontmatter(skill_md: Path) -> dict[str, Any]:
    """Extract frontmatter key/value pairs from a SKILL.md file.

    Handles the --- delimited YAML-like frontmatter block. Returns a dict
    of top-level keys. Nested values (like args list items) are stored as
    raw strings for later parsing.
    """
    text = skill_md.read_text()
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    frontmatter_lines: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        frontmatter_lines.append(line)

    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] = []

    for line in frontmatter_lines:
        # Top-level key (not indented)
        if not line.startswith(" ") and not line.startswith("\t") and ":" in line:
            # Save previous list if any
            if current_key and current_list:
                result[current_key] = current_list
                current_list = []
            key, _, value = line.partition(":")
            current_key = key.strip()
            value = value.strip()
            if value:
                result[current_key] = value
            # If no value, might be a list (args:) — will be populated by
            # subsequent indented lines
        elif current_key and (line.startswith("  ") or line.startswith("\t")):
            current_list.append(line)

    # Save final list
    if current_key and current_list:
        result[current_key] = current_list

    return result


def parse_frontmatter_args(frontmatter: dict[str, Any]) -> list[str]:
    """Extract --flag names from the frontmatter args: block.

    Only validates --flag style args. Positional arg names (like pr_number)
    are too generic to grep for meaningfully in prose text.
    """
    args_raw = frontmatter.get("args")
    if not args_raw or not isinstance(args_raw, list):
        return []

    flags: list[str] = []
    for line in args_raw:
        line = line.strip()
        # Match lines like "- name: --dry-run" or "- name: --repo"
        if line.startswith("- name:"):
            name = line.split(":", 1)[1].strip()
            if name.startswith("--"):
                flags.append(name)
        # Also handle "- --dry-run (required): description" inline format
        elif line.startswith("- --"):
            match = RE_FLAG_IN_TEXT.search(line)
            if match:
                flags.append(match.group())

    return flags


def extract_prompt_args(prompt_md: Path) -> set[str]:
    """Find all --flag references in prompt.md."""
    text = prompt_md.read_text()
    return set(RE_FLAG_IN_TEXT.findall(text))


def extract_sub_skill_refs(prompt_md: Path) -> list[str]:
    """Find all Skill(skill='onex:...' and /onex:... references in prompt.md."""
    text = prompt_md.read_text()
    refs: list[str] = []
    for match in RE_SUB_SKILL_REF.finditer(text):
        # Group 1 is from Skill() pattern, group 2 from /onex: pattern
        ref = match.group(1) or match.group(2)
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def extract_sub_skill_dispatches(
    prompt_md: Path,
) -> list[tuple[str, list[str]]]:
    """Find all Skill(skill='onex:X', args='...') and extract the --flags from args."""
    text = prompt_md.read_text()
    dispatches: list[tuple[str, list[str]]] = []
    for match in RE_SUB_SKILL_DISPATCH.finditer(text):
        skill_name = match.group(1)
        args_str = match.group(2)
        flags = RE_FLAG_IN_TEXT.findall(args_str)
        dispatches.append((skill_name, flags))
    return dispatches


def _get_skill_body(skill_md: Path) -> str:
    """Return the SKILL.md content after the frontmatter block."""
    text = skill_md.read_text()
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text

    in_frontmatter = True
    body_start = 0
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            body_start = i + 1
            in_frontmatter = False
            break

    if in_frontmatter:
        return text  # No closing ---, treat whole thing as body
    return "\n".join(lines[body_start:])


# ---------------------------------------------------------------------------
# Check 1: args-parity
# ---------------------------------------------------------------------------


def check_args_parity(skill_dir: Path) -> list[HygieneViolation]:
    """Every --flag arg in SKILL.md frontmatter must be referenced in prompt.md."""
    violations: list[HygieneViolation] = []
    skill_md = skill_dir / "SKILL.md"
    prompt_md = skill_dir / "prompt.md"

    if not skill_md.exists():
        return violations

    frontmatter = parse_frontmatter(skill_md)
    spec_args = parse_frontmatter_args(frontmatter)
    if not spec_args:
        return violations

    if not prompt_md.exists():
        violations.append(
            HygieneViolation(
                path=skill_dir.name,
                check=CHECK_ARGS_PARITY,
                severity=SEVERITY_WARNING,
                message=f"SKILL.md declares {len(spec_args)} args but no prompt.md exists",
            )
        )
        return violations

    prompt_text = prompt_md.read_text()
    for arg in spec_args:
        bare = arg.lstrip("-")
        # Check --flag-name, flag-name, and flag_name forms
        if (
            arg not in prompt_text
            and bare not in prompt_text
            and bare.replace("-", "_") not in prompt_text
        ):
            violations.append(
                HygieneViolation(
                    path=skill_dir.name,
                    check=CHECK_ARGS_PARITY,
                    severity=SEVERITY_ERROR,
                    message=f"Arg '{arg}' declared in SKILL.md but not found in prompt.md",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# Check 2: sub-skill-exists
# ---------------------------------------------------------------------------


def check_sub_skill_exists(
    skill_dir: Path, skills_root: Path
) -> list[HygieneViolation]:
    """Skill(skill='onex:X') references must resolve to real skill dirs."""
    prompt_md = skill_dir / "prompt.md"
    if not prompt_md.exists():
        return []

    violations: list[HygieneViolation] = []
    refs = extract_sub_skill_refs(prompt_md)
    for ref in refs:
        dir_name_underscore = ref.replace("-", "_")
        dir_name_hyphen = ref
        target_underscore = skills_root / dir_name_underscore
        target_hyphen = skills_root / dir_name_hyphen
        found = (
            target_underscore.exists() and (target_underscore / "SKILL.md").exists()
        ) or (target_hyphen.exists() and (target_hyphen / "SKILL.md").exists())
        if not found:
            violations.append(
                HygieneViolation(
                    path=skill_dir.name,
                    check=CHECK_SUB_SKILL_EXISTS,
                    severity=SEVERITY_ERROR,
                    message=(
                        f"Sub-skill 'onex:{ref}' referenced but neither "
                        f"'{dir_name_underscore}/' nor '{dir_name_hyphen}/' "
                        f"found under skills root"
                    ),
                )
            )
    return violations


# ---------------------------------------------------------------------------
# Check 3: sub-skill-args
# ---------------------------------------------------------------------------


def check_sub_skill_args(skill_dir: Path, skills_root: Path) -> list[HygieneViolation]:
    """Dispatched sub-skill args must be compatible with target's SKILL.md args."""
    prompt_md = skill_dir / "prompt.md"
    if not prompt_md.exists():
        return []

    violations: list[HygieneViolation] = []
    dispatches = extract_sub_skill_dispatches(prompt_md)

    for skill_name, dispatched_flags in dispatches:
        if not dispatched_flags:
            continue

        # Resolve target skill dir
        target = None
        for candidate_name in [
            skill_name.replace("-", "_"),
            skill_name,
        ]:
            candidate = skills_root / candidate_name
            if candidate.exists() and (candidate / "SKILL.md").exists():
                target = candidate
                break

        if target is None:
            # sub-skill-exists check will catch this
            continue

        # Parse target's declared args
        target_frontmatter = parse_frontmatter(target / "SKILL.md")
        target_args = set(parse_frontmatter_args(target_frontmatter))

        # Also include bare forms for matching
        target_bare = set()
        for a in target_args:
            target_bare.add(a)
            target_bare.add(a.lstrip("-"))
            target_bare.add(a.lstrip("-").replace("-", "_"))

        for flag in dispatched_flags:
            bare = flag.lstrip("-")
            if (
                flag not in target_bare
                and bare not in target_bare
                and bare.replace("-", "_") not in target_bare
            ):
                violations.append(
                    HygieneViolation(
                        path=skill_dir.name,
                        check=CHECK_SUB_SKILL_ARGS,
                        severity=SEVERITY_WARNING,
                        message=(
                            f"Dispatched arg '{flag}' to 'onex:{skill_name}' "
                            f"but it is not declared in target's SKILL.md args"
                        ),
                    )
                )
    return violations


# ---------------------------------------------------------------------------
# Check 4: duplicate-frontmatter
# ---------------------------------------------------------------------------


def check_duplicate_frontmatter(skill_dir: Path) -> list[HygieneViolation]:
    """No duplicate top-level keys in SKILL.md frontmatter."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return []

    violations: list[HygieneViolation] = []
    lines = skill_md.read_text().splitlines()
    in_frontmatter = False
    seen_keys: dict[str, int] = {}

    for i, line in enumerate(lines, 1):
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            break  # end of frontmatter
        if (
            in_frontmatter
            and ":" in line
            and not line.startswith(" ")
            and not line.startswith("\t")
        ):
            key = line.split(":", 1)[0].strip()
            if key in seen_keys:
                violations.append(
                    HygieneViolation(
                        path=skill_dir.name,
                        check=CHECK_DUPLICATE_FRONTMATTER,
                        severity=SEVERITY_ERROR,
                        message=(
                            f"Duplicate key '{key}' in frontmatter "
                            f"(lines {seen_keys[key]} and {i})"
                        ),
                    )
                )
            else:
                seen_keys[key] = i
    return violations


# ---------------------------------------------------------------------------
# Check 5: spec-prompt-predicates
# ---------------------------------------------------------------------------


def check_spec_prompt_predicates(
    skill_dir: Path,
) -> list[HygieneViolation]:
    """Backtick-quoted predicates in SKILL.md body must appear in prompt.md."""
    skill_md = skill_dir / "SKILL.md"
    prompt_md = skill_dir / "prompt.md"

    if not skill_md.exists() or not prompt_md.exists():
        return []

    violations: list[HygieneViolation] = []
    body = _get_skill_body(skill_md)
    prompt_text = prompt_md.read_text()

    # Find lines containing context keywords
    for line in body.splitlines():
        line_lower = line.lower()
        has_context = any(kw in line_lower for kw in PREDICATE_CONTEXT_KEYWORDS)
        if not has_context:
            continue

        # Extract backtick-quoted predicates from this line
        predicates = RE_BACKTICK_PREDICATE.findall(line)
        for pred in predicates:
            # Skip very short or common words that are likely noise
            if len(pred) < 3:
                continue
            # Check if predicate appears in prompt.md (also check underscore/hyphen variants)
            if (
                pred not in prompt_text
                and pred.replace("_", "-") not in prompt_text
                and pred.replace("-", "_") not in prompt_text
            ):
                violations.append(
                    HygieneViolation(
                        path=skill_dir.name,
                        check=CHECK_SPEC_PROMPT_PREDICATES,
                        severity=SEVERITY_WARNING,
                        message=(
                            f"Predicate '{pred}' found in SKILL.md but not in prompt.md"
                        ),
                    )
                )
    return violations


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def validate_all(skills_root: Path, strict: bool = False) -> list[HygieneViolation]:
    """Run all 5 checks across all skill directories."""
    all_violations: list[HygieneViolation] = []
    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
            continue
        all_violations.extend(check_args_parity(skill_dir))
        all_violations.extend(check_sub_skill_exists(skill_dir, skills_root))
        all_violations.extend(check_sub_skill_args(skill_dir, skills_root))
        all_violations.extend(check_duplicate_frontmatter(skill_dir))
        all_violations.extend(check_spec_prompt_predicates(skill_dir))

    if strict:
        for v in all_violations:
            if v.check in STRICT_PROMOTED_CHECKS and v.severity == SEVERITY_WARNING:
                v.severity = SEVERITY_ERROR
    return all_violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate skill contract parity across a skill tree."
    )
    parser.add_argument(
        "--skills-root",
        type=Path,
        required=True,
        help="Root directory containing skill subdirectories",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Promote selected WARNING checks to ERROR",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    if not args.skills_root.is_dir():
        print(f"ERROR: skills root not found: {args.skills_root}", file=sys.stderr)
        return 2

    violations = validate_all(args.skills_root, strict=args.strict)

    errors = [v for v in violations if v.severity == SEVERITY_ERROR]
    warnings = [v for v in violations if v.severity == SEVERITY_WARNING]

    if args.json_output:
        output = {
            "violation_count": len(violations),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "violations": [v.to_dict() for v in violations],
        }
        print(json.dumps(output, indent=2))
    elif violations:
        print(
            f"Skill contract validation: "
            f"{len(errors)} error(s), {len(warnings)} warning(s)\n"
        )
        for v in violations:
            print(v.format_line())
        print()
    else:
        print("Skill contract validation: all checks passed")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
