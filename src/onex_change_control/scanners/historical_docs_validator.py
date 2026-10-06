# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Historical docs archive validator.

Validates docs in archive directories (docs/history/, docs/ideas/, docs/patent/)
with different staleness semantics: broken references are expected and not flagged,
but recently modified docs in archive are flagged as violations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from onex_change_control.scanners.doc_staleness_detector import get_git_last_modified

_DEFAULT_ARCHIVE_DIRS = ("docs/history/", "docs/ideas/", "docs/patent/")
_FROZEN_THRESHOLD_DAYS = 30


@dataclass(frozen=True)
class ArchiveValidationResult:
    """Result of validating a document against archive policies."""

    doc_path: str
    is_in_archive: bool
    has_archive_marker: bool
    recently_modified: bool
    is_archival_candidate: bool
    finding: str


def _has_archive_marker(doc_path: str) -> bool:
    """Check if a doc has a Historical or Archived marker."""
    path = Path(doc_path)
    if not path.exists():
        return False

    # Check first 20 lines for markers
    lines = path.read_text(encoding="utf-8").splitlines()[:20]
    for line in lines:
        lower = line.lower()
        if "historical" in lower or "archived" in lower or "frozen" in lower:
            return True
    return False


def _is_recently_modified(doc_path: str, repo_root: str) -> bool:
    """Check if a doc was modified in the last 30 days."""
    last_modified = get_git_last_modified(doc_path, repo_root)
    if last_modified is None:
        return False
    age_days = (datetime.now(tz=UTC) - last_modified).total_seconds() / 86400
    return age_days < _FROZEN_THRESHOLD_DAYS


def validate_archive_docs(
    repo_root: str,
    archive_dirs: tuple[str, ...] = _DEFAULT_ARCHIVE_DIRS,
) -> list[ArchiveValidationResult]:
    """Validate docs in archive directories.

    Checks:
    1. Archive docs should NOT be recently modified (frozen).
    2. Archive docs should have a Historical/Archived marker.

    Args:
        repo_root: Repository root directory.
        archive_dirs: Directories treated as archives.

    Returns:
        List of validation results for archive docs.
    """
    results: list[ArchiveValidationResult] = []
    root = Path(repo_root)

    for archive_dir in archive_dirs:
        archive_path = root / archive_dir
        if not archive_path.exists():
            continue

        for md_file in archive_path.rglob("*.md"):
            doc_path = str(md_file)
            has_marker = _has_archive_marker(doc_path)
            recently_mod = _is_recently_modified(doc_path, repo_root)

            finding = ""
            if recently_mod:
                finding = (
                    f"Archive doc modified in last"
                    f" {_FROZEN_THRESHOLD_DAYS} days (should be frozen)"
                )
            elif not has_marker:
                finding = "Archive doc missing Historical/Archived marker"

            results.append(
                ArchiveValidationResult(
                    doc_path=doc_path,
                    is_in_archive=True,
                    has_archive_marker=has_marker,
                    recently_modified=recently_mod,
                    is_archival_candidate=False,
                    finding=finding,
                )
            )

    return results


def find_archival_candidates(
    broken_doc_paths: list[str],
    archive_dirs: tuple[str, ...] = _DEFAULT_ARCHIVE_DIRS,
) -> list[ArchiveValidationResult]:
    """Find docs outside archive that reference only deleted/archived code.

    These are candidates for moving to docs/history/.

    Args:
        broken_doc_paths: Docs with BROKEN verdict (all references broken).
        archive_dirs: Directories already treated as archives.

    Returns:
        List of archival candidates.
    """
    results: list[ArchiveValidationResult] = []

    for doc_path in broken_doc_paths:
        # Skip docs already in archive
        is_in_archive = any(archive_dir in doc_path for archive_dir in archive_dirs)
        if is_in_archive:
            continue

        results.append(
            ArchiveValidationResult(
                doc_path=doc_path,
                is_in_archive=False,
                has_archive_marker=False,
                recently_modified=False,
                is_archival_candidate=True,
                finding=(
                    "All references broken -- candidate for archival to docs/history/"
                ),
            )
        )

    return results
