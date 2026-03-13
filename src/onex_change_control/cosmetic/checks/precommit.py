# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Pre-commit version check and fix.

Verifies that ``.pre-commit-config.yaml`` pins the correct versions for
tracked repos.  Fix mode uses string replacement (not ``yaml.dump``) to
preserve comments and formatting.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import yaml

from onex_change_control.cosmetic.cli import Violation
from onex_change_control.cosmetic.config import load_spec

if TYPE_CHECKING:
    from pathlib import Path


def _parse_versions(
    config_path: Path,
) -> dict[str, str]:
    """Parse repo→rev mappings from ``.pre-commit-config.yaml``."""
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return {}
    repos: list[dict[str, Any]] = data.get("repos", [])
    result: dict[str, str] = {}
    for repo in repos:
        url = repo.get("repo", "")
        rev = repo.get("rev", "")
        if url and rev and url != "local":
            result[url] = str(rev)
    return result


def _fix_versions(
    config_path: Path,
    expected: dict[str, str],
) -> None:
    """Fix repo versions in-place using string replacement.

    Scans lines sequentially, tracks which ``repo:`` URL was last seen, and
    only replaces the next ``rev:`` line if it belongs to a tracked repo.
    """
    content = config_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    repo_re = re.compile(r"^(\s*-?\s*repo:\s*)(.+)$")
    rev_re = re.compile(r"^(\s*rev:\s*)(.+)$")

    current_repo: str | None = None
    new_lines: list[str] = []

    for line in lines:
        repo_match = repo_re.match(line)
        if repo_match:
            current_repo = repo_match.group(2).strip().strip("'\"")
            new_lines.append(line)
            continue

        rev_match = rev_re.match(line)
        if rev_match and current_repo and current_repo in expected:
            prefix = rev_match.group(1)
            new_lines.append(f"{prefix}{expected[current_repo]}\n")
            current_repo = None
            continue

        new_lines.append(line)

    config_path.write_text("".join(new_lines), encoding="utf-8")


def run_check(
    target: Path,
    spec: dict[str, Any] | None = None,
    *,
    fix: bool = False,
) -> list[Violation]:
    """Run the pre-commit version check (and optional fix) on *target*.

    Args:
        target: Root directory to scan.
        spec: Parsed cosmetic spec dict.  If *None*, loads the default.
        fix: When *True*, update version pins in-place.

    Returns:
        List of violations found.

    """
    if spec is None:
        spec = load_spec()

    config_path = target / ".pre-commit-config.yaml"
    if not config_path.exists():
        return []

    spec_precommit: dict[str, Any] = spec.get("precommit", {})
    expected_versions: dict[str, str] = spec_precommit.get("versions", {})
    if not expected_versions:
        return []

    actual_versions = _parse_versions(config_path)

    violations: list[Violation] = []
    for repo_url, expected_rev in expected_versions.items():
        actual_rev = actual_versions.get(repo_url)
        if actual_rev is None:
            continue
        if actual_rev != expected_rev:
            violations.append(
                Violation(
                    check="precommit",
                    path=".pre-commit-config.yaml",
                    line=0,
                    message=(
                        f"{repo_url}: expected rev {expected_rev}, got {actual_rev}"
                    ),
                    fixable=True,
                )
            )

    if fix and violations:
        _fix_versions(config_path, expected_versions)

    return violations
