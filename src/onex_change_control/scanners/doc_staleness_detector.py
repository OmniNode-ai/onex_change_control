# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Staleness detection for documentation files.

Compares doc modification dates against referenced code modification dates
to detect docs that have fallen out of sync with the code they describe.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from onex_change_control.enums.enum_doc_staleness_verdict import EnumDocStalenessVerdict
from onex_change_control.models.model_doc_freshness_result import (
    ModelDocFreshnessResult,
)

if TYPE_CHECKING:
    from onex_change_control.models.model_doc_reference import ModelDocReference

_STALENESS_THRESHOLD = 0.3
_STALE_DAYS_THRESHOLD = 30


def get_git_last_modified(file_path: str, repo_root: str) -> datetime | None:
    """Get the last git commit date for a file.

    Args:
        file_path: Path to the file (absolute or relative to repo_root).
        repo_root: Root directory of the git repository.

    Returns:
        datetime of last commit, or None if not tracked.
    """
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI", "--", file_path],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=10,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return datetime.fromisoformat(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return None


def get_recently_changed_files(repo_root: str, days: int = 30) -> set[str]:
    """Get all files changed in the last N days via a single git command.

    This is more efficient than querying per-file for large repos.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--name-only",
                "--pretty=format:",
                f"--since={days} days ago",
            ],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            return {line.strip() for line in result.stdout.splitlines() if line.strip()}
    except (subprocess.TimeoutExpired, OSError):
        pass
    return set()


def detect_stale_references(
    references: list[ModelDocReference],
    doc_last_modified: datetime,
    repo_root: str,
    recently_changed: set[str] | None = None,
) -> list[ModelDocReference]:
    """Identify references whose targets were modified after the doc.

    Args:
        references: Resolved references with exists field populated.
        doc_last_modified: When the doc was last modified.
        repo_root: Repository root for git queries.
        recently_changed: Pre-computed set of recently changed files.

    Returns:
        List of stale references (target changed after doc).
    """
    stale: list[ModelDocReference] = []

    for ref in references:
        if ref.exists is not True or ref.resolved_target is None:
            continue

        # If we have a pre-computed list, use that for efficiency
        if recently_changed is not None:
            # Check if the resolved target (relative to repo) is in changed set
            try:
                rel_path = str(Path(ref.resolved_target).relative_to(repo_root))
                if rel_path in recently_changed:
                    code_modified = get_git_last_modified(
                        ref.resolved_target, repo_root
                    )
                    if code_modified and code_modified > doc_last_modified:
                        stale.append(ref)
            except ValueError:
                # Path not relative to repo_root, skip
                continue
        else:
            code_modified = get_git_last_modified(ref.resolved_target, repo_root)
            if code_modified and code_modified > doc_last_modified:
                stale.append(ref)

    return stale


def compute_staleness_score(
    total_references: int,
    broken_count: int,
    stale_count: int,
) -> float:
    """Compute staleness score from broken and stale reference counts.

    Score = 0.6 * broken_ratio + 0.4 * stale_ratio

    Args:
        total_references: Total number of checkable references.
        broken_count: Number of broken references.
        stale_count: Number of stale references.

    Returns:
        Score from 0.0 (fresh) to 1.0 (completely stale).
    """
    if total_references == 0:
        return 0.0
    broken_ratio = broken_count / total_references
    stale_ratio = stale_count / total_references
    return min(1.0, 0.6 * broken_ratio + 0.4 * stale_ratio)


def assign_verdict(
    staleness_score: float,
    broken_count: int,
    total_references: int,
    doc_age_days: float,
) -> EnumDocStalenessVerdict:
    """Assign a freshness verdict based on score and reference counts.

    Args:
        staleness_score: Computed staleness score (0.0-1.0).
        broken_count: Number of broken references.
        total_references: Total checkable references.
        doc_age_days: Age of the doc in days.

    Returns:
        The appropriate verdict.
    """
    if total_references == 0:
        return EnumDocStalenessVerdict.UNKNOWN
    if broken_count > 0:
        return EnumDocStalenessVerdict.BROKEN
    if staleness_score > _STALENESS_THRESHOLD and doc_age_days > _STALE_DAYS_THRESHOLD:
        return EnumDocStalenessVerdict.STALE
    return EnumDocStalenessVerdict.FRESH


def build_freshness_result(
    doc_path: str,
    repo: str,
    repo_root: str,
    resolved_references: list[ModelDocReference],
    recently_changed: set[str] | None = None,
) -> ModelDocFreshnessResult:
    """Build a complete freshness result for a single document.

    Args:
        doc_path: Path to the .md file.
        repo: Repository name.
        repo_root: Repository root directory.
        resolved_references: References with exists field populated.
        recently_changed: Pre-computed set of recently changed files.

    Returns:
        Complete freshness result with verdict.
    """
    doc_last_modified = get_git_last_modified(doc_path, repo_root)
    if doc_last_modified is None:
        doc_last_modified = datetime.now(tz=UTC)

    # Separate broken references
    broken_refs = [r for r in resolved_references if r.exists is False]

    # Detect stale references
    stale_refs = detect_stale_references(
        resolved_references, doc_last_modified, repo_root, recently_changed
    )

    # Find most recent code change
    checkable = [
        r for r in resolved_references if r.exists is True and r.resolved_target
    ]
    code_dates: list[datetime] = []
    for ref in checkable:
        assert ref.resolved_target is not None
        dt = get_git_last_modified(ref.resolved_target, repo_root)
        if dt:
            code_dates.append(dt)
    referenced_code_last_modified = max(code_dates) if code_dates else None

    # Count checkable references (not URLs)
    checkable_count = len([r for r in resolved_references if r.exists is not None])

    score = compute_staleness_score(checkable_count, len(broken_refs), len(stale_refs))

    doc_age_days = (datetime.now(tz=UTC) - doc_last_modified).total_seconds() / 86400

    verdict = assign_verdict(score, len(broken_refs), checkable_count, doc_age_days)

    return ModelDocFreshnessResult(
        doc_path=doc_path,
        repo=repo,
        doc_last_modified=doc_last_modified,
        references=resolved_references,
        broken_references=broken_refs,
        stale_references=stale_refs,
        staleness_score=score,
        verdict=verdict,
        referenced_code_last_modified=referenced_code_last_modified,
    )
