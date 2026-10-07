# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""README badge check and fix.

Scans the first 30 lines of ``README.md`` for required badge patterns.
Fix mode inserts missing badges after the first ``# Title`` line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from onex_change_control.cosmetic.cli import Violation
from onex_change_control.cosmetic.config import load_spec

if TYPE_CHECKING:
    from pathlib import Path

# Standard badge templates keyed by lowercase pattern match.
_BADGE_TEMPLATES: dict[str, str] = {
    "mit": (
        "[![License: MIT]"
        "(https://img.shields.io/badge/License-MIT-yellow.svg)]"
        "(LICENSE)"
    ),
    "ruff": (
        "[![Ruff]"
        "(https://img.shields.io/endpoint?"
        "url=https://raw.githubusercontent.com/astral-sh/ruff/"
        "main/assets/badge/v2.json)]"
        "(https://github.com/astral-sh/ruff)"
    ),
    "mypy": (
        "[![Checked with mypy]"
        "(https://www.mypy-lang.org/static/mypy_badge.svg)]"
        "(http://mypy-lang.org/)"
    ),
    "pre-commit": (
        "[![pre-commit]"
        "(https://img.shields.io/badge/pre--commit-enabled-"
        "brightgreen?logo=pre-commit)]"
        "(https://github.com/pre-commit/pre-commit)"
    ),
}


def run_check(
    target: Path,
    spec: dict[str, Any] | None = None,
    *,
    fix: bool = False,
) -> list[Violation]:
    """Run the README badge check (and optional fix) on *target*.

    Args:
        target: Root directory to scan.
        spec: Parsed cosmetic spec dict.  If *None*, loads the default.
        fix: When *True*, insert missing badges into README.md.

    Returns:
        List of violations found.

    """
    if spec is None:
        spec = load_spec()

    readme_path = target / "README.md"
    if not readme_path.exists():
        return []

    content = readme_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    header_region = "\n".join(lines[:30]).lower()

    spec_readme: dict[str, Any] = spec.get("readme", {})
    required_badges: list[dict[str, str]] = spec_readme.get("required_badges", [])

    violations: list[Violation] = []
    missing_patterns: list[str] = []

    for badge in required_badges:
        pattern = badge["pattern"].lower()
        if pattern not in header_region:
            violations.append(
                Violation(
                    check="readme",
                    path="README.md",
                    line=0,
                    message=(
                        f"Missing required badge: {badge.get('description', pattern)}"
                    ),
                    fixable=True,
                )
            )
            missing_patterns.append(pattern)

    if fix and missing_patterns:
        _fix_badges(readme_path, lines, missing_patterns)

    return violations


def _fix_badges(
    readme_path: Path,
    lines: list[str],
    missing_patterns: list[str],
) -> None:
    """Insert missing badges after the first H1 line."""
    # Find the first H1 line.
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            insert_idx = i + 1
            break

    # Build badge lines to insert.
    badge_lines: list[str] = []
    for pattern in missing_patterns:
        template = _BADGE_TEMPLATES.get(pattern)
        if template:
            badge_lines.append(template)

    if not badge_lines:
        return

    # Insert after H1 with a blank line separator.
    new_lines = list(lines)
    insertion = ["", *badge_lines, ""]
    new_lines[insert_idx:insert_idx] = insertion

    readme_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
