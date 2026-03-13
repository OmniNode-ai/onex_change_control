# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""SPDX header check and fix for Python source files.

Verifies that every matched ``.py`` file starts with the canonical SPDX
copyright and license header.  Fix mode strips all existing SPDX lines
and legacy ``# Copyright (c) ...`` lines, then prepends the canonical
header (after any shebang line).
"""

from __future__ import annotations

import fnmatch
import re
from typing import TYPE_CHECKING, Any

from onex_change_control.cosmetic.cli import Violation
from onex_change_control.cosmetic.config import load_spec

if TYPE_CHECKING:
    from pathlib import Path

# Marker that tells the checker to skip a file entirely.
_SKIP_MARKER = "spdx-skip"

# Patterns for lines we strip during fix.
_SPDX_LINE_RE = re.compile(r"^#\s*SPDX-", re.IGNORECASE)
_LEGACY_COPYRIGHT_RE = re.compile(r"^#\s*Copyright\s*\(c\)", re.IGNORECASE)


def _is_excluded(rel_path: str, exclude_patterns: list[str]) -> bool:
    """Check whether *rel_path* matches any exclude pattern."""
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        if fnmatch.fnmatch("/" + rel_path, "/" + pattern.lstrip("*")):
            return True
        parts = pattern.replace("**", "").strip("/").split("/")
        parts = [p for p in parts if p and p != "*"]
        if parts:
            rel_parts = rel_path.replace("\\", "/").split("/")
            if all(component in rel_parts for component in parts):
                return True
    return False


def _collect_files(
    target: Path,
    file_patterns: list[str],
    exclude_patterns: list[str],
) -> list[Path]:
    """Return Python files matching *file_patterns* but not *exclude_patterns*."""
    matched: list[Path] = []
    for pattern in file_patterns:
        for path in target.rglob(pattern):
            if not path.is_file():
                continue
            rel = str(path.relative_to(target))
            if _is_excluded(rel, exclude_patterns):
                continue
            matched.append(path)
    return sorted(matched)


def _has_skip_marker(content: str) -> bool:
    """Return *True* if the file contains the ``spdx-skip`` marker."""
    return _SKIP_MARKER in content


def _expected_header(spec_spdx: dict[str, Any]) -> tuple[str, str]:
    """Return the two canonical SPDX header lines."""
    copyright_line = f"# SPDX-FileCopyrightText: {spec_spdx['copyright_text']}"
    license_line = f"# SPDX-License-Identifier: {spec_spdx['license_identifier']}"
    return copyright_line, license_line


def _check_file(
    path: Path,
    spec_spdx: dict[str, Any],
    target: Path,
) -> list[Violation]:
    """Check a single file for correct SPDX header.  Returns violations."""
    content = path.read_text(encoding="utf-8")
    if _has_skip_marker(content):
        return []

    lines = content.splitlines()
    copyright_line, license_line = _expected_header(spec_spdx)

    # Skip shebang if present.
    start = 0
    if lines and lines[0].startswith("#!"):
        start = 1

    rel = str(path.relative_to(target))
    violations: list[Violation] = []

    # Need at least two lines after the shebang.
    if len(lines) < start + 2:
        violations.append(
            Violation(
                check="spdx",
                path=rel,
                line=start + 1,
                message="Missing SPDX header",
                fixable=True,
            )
        )
        return violations

    if lines[start] != copyright_line:
        violations.append(
            Violation(
                check="spdx",
                path=rel,
                line=start + 1,
                message=f"Expected '{copyright_line}', got '{lines[start]}'",
                fixable=True,
            )
        )

    if lines[start + 1] != license_line:
        violations.append(
            Violation(
                check="spdx",
                path=rel,
                line=start + 2,
                message=f"Expected '{license_line}', got '{lines[start + 1]}'",
                fixable=True,
            )
        )

    return violations


def _fix_file(path: Path, spec_spdx: dict[str, Any]) -> None:
    """Strip existing SPDX/legacy copyright lines and prepend canonical header."""
    content = path.read_text(encoding="utf-8")
    if _has_skip_marker(content):
        return

    lines = content.splitlines(keepends=True)
    copyright_line, license_line = _expected_header(spec_spdx)

    # Separate shebang.
    shebang: str | None = None
    if lines and lines[0].startswith("#!"):
        shebang = lines[0]
        lines = lines[1:]

    # Strip ALL existing SPDX and legacy copyright lines.
    cleaned: list[str] = []
    for line in lines:
        stripped = line.rstrip("\n").rstrip("\r")
        if _SPDX_LINE_RE.match(stripped) or _LEGACY_COPYRIGHT_RE.match(stripped):
            continue
        cleaned.append(line)

    # Remove leading blank lines left after stripping.
    while cleaned and cleaned[0].strip() == "":
        cleaned.pop(0)

    # Build the new content.
    parts: list[str] = []
    if shebang is not None:
        parts.append(shebang.rstrip("\n") + "\n")
    parts.append(copyright_line + "\n")
    parts.append(license_line + "\n")
    # Ensure a blank line separates the header from the rest of the file.
    if cleaned:
        parts.append("\n")
    parts.extend(cleaned)

    path.write_text("".join(parts), encoding="utf-8")


def run_check(
    target: Path,
    spec: dict[str, Any] | None = None,
    *,
    fix: bool = False,
) -> list[Violation]:
    """Run the SPDX header check (and optional fix) on *target*.

    Args:
        target: Root directory to scan.
        spec: Parsed cosmetic spec dict.  If *None*, loads the default.
        fix: When *True*, rewrite files to have the canonical header.

    Returns:
        List of violations found (before fix, if fix mode).

    """
    if spec is None:
        spec = load_spec()

    spec_spdx: dict[str, Any] = spec.get("spdx", {})
    file_patterns: list[str] = spec_spdx.get("file_patterns", ["*.py"])
    exclude_patterns: list[str] = spec_spdx.get("exclude_patterns", [])

    files = _collect_files(target, file_patterns, exclude_patterns)

    violations: list[Violation] = []
    for path in files:
        file_violations = _check_file(path, spec_spdx, target)
        violations.extend(file_violations)
        if fix and file_violations:
            _fix_file(path, spec_spdx)

    return violations
