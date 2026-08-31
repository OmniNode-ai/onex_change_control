# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression guard against triplicate CI script copies."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
CI_ROOTS = (
    REPO_ROOT / ".github",
    REPO_ROOT / "scripts",
    REPO_ROOT / "workflows",
)
CI_SCRIPT_SUFFIXES = frozenset({".py", ".sh", ".yaml", ".yml"})


def _ci_script_files() -> list[Path]:
    """Collect non-empty CI script files from canonical CI roots."""
    files: list[Path] = []
    for root in CI_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if (
                path.is_file()
                and path.suffix in CI_SCRIPT_SUFFIXES
                and path.name != "__init__.py"
                and path.stat().st_size > 0
            ):
                files.append(path)
    return sorted(files)


@pytest.mark.unit
def test_no_triplicate_identical_ci_scripts() -> None:
    """Accidental CI script clones should not reappear as three identical files."""
    by_digest: dict[str, list[Path]] = defaultdict(list)
    for path in _ci_script_files():
        by_digest[hashlib.sha256(path.read_bytes()).hexdigest()].append(path)

    triplicates = [paths for paths in by_digest.values() if len(paths) >= 3]

    assert triplicates == [], "\n".join(
        "Duplicate CI script group:\n"
        + "\n".join(f"  - {path.relative_to(REPO_ROOT)}" for path in paths)
        for paths in triplicates
    )
