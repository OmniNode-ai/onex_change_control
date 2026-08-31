# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""CLAUDE.md update suggestion generator.

For broken or stale references in CLAUDE.md, generates structured
update suggestions including rename detection via git history.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from onex_change_control.models.model_doc_reference import ModelDocReference


@dataclass(frozen=True)
class UpdateSuggestion:
    """A suggested update for a broken or stale reference."""

    doc_path: str
    line_number: int
    old_text: str
    new_text: str | None
    reason: str


def _detect_rename(file_path: str, repo_root: str) -> str | None:
    """Detect if a file was renamed using git log --follow.

    Returns the new path if a rename was detected, None otherwise.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--follow",
                "--diff-filter=R",
                "--name-status",
                "--pretty=format:",
                "-1",
                "--",
                file_path,
            ],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=10,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                parts = line.strip().split("\t")
                _min_rename_parts = 3
                if len(parts) >= _min_rename_parts and parts[0].startswith("R"):
                    return parts[2]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _find_similar_path(file_path: str, repo_root: str) -> str | None:
    """Try to find a similarly named file that might be the new location."""
    path = Path(file_path)
    name = path.name

    try:
        result = subprocess.run(
            ["find", repo_root, "-name", name, "-not", "-path", "*/__pycache__/*"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            candidates = result.stdout.strip().splitlines()
            if len(candidates) == 1:
                # Exactly one match — likely the new location
                return str(Path(candidates[0]).relative_to(repo_root))
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def generate_suggestions(
    broken_references: list[ModelDocReference],
    repo_root: str,
) -> list[UpdateSuggestion]:
    """Generate update suggestions for broken references.

    Args:
        broken_references: References where exists == False.
        repo_root: Repository root for git queries.

    Returns:
        List of structured update suggestions.
    """
    suggestions: list[UpdateSuggestion] = []

    for ref in broken_references:
        raw = ref.raw_text

        # Try rename detection via git
        new_path = _detect_rename(raw, repo_root)
        if new_path:
            suggestions.append(
                UpdateSuggestion(
                    doc_path=ref.doc_path,
                    line_number=ref.line_number,
                    old_text=raw,
                    new_text=new_path,
                    reason=f"File was renamed: {raw} -> {new_path}",
                )
            )
            continue

        # Try finding a similarly named file
        similar = _find_similar_path(raw, repo_root)
        if similar:
            suggestions.append(
                UpdateSuggestion(
                    doc_path=ref.doc_path,
                    line_number=ref.line_number,
                    old_text=raw,
                    new_text=similar,
                    reason=f"Possible move: {raw} -> {similar}",
                )
            )
            continue

        # No rename or move detected — suggest removal
        suggestions.append(
            UpdateSuggestion(
                doc_path=ref.doc_path,
                line_number=ref.line_number,
                old_text=raw,
                new_text=None,
                reason=f"Reference not found — manual review needed: {raw}",
            )
        )

    return suggestions
