# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""CLAUDE.md cross-reference checker.

Targeted validator for CLAUDE.md files that checks whether instructions
(commands, paths, conventions, table entries) match actual repo state.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from onex_change_control.models.model_doc_cross_ref_check import ModelDocCrossRefCheck


def _check_paths_in_line(
    line: str, line_number: int, repo_root: str
) -> list[ModelDocCrossRefCheck]:
    """Check file/directory paths referenced in a CLAUDE.md line."""
    results: list[ModelDocCrossRefCheck] = []

    # Match backtick-enclosed paths that look like file references
    path_pattern = re.compile(
        r"`([^`]*(?:src/|tests/|scripts/|docs/|\.github/|plugins/)[^`]*)`"
    )
    for match in path_pattern.finditer(line):
        raw_path = match.group(1)
        # Skip template placeholders
        if "<" in raw_path and ">" in raw_path:
            continue

        candidate = Path(repo_root) / raw_path
        exists = candidate.exists()

        results.append(
            ModelDocCrossRefCheck(
                instruction=raw_path,
                line_number=line_number,
                check_type="path",
                verified=exists,
                evidence=f"Path {'exists' if exists else 'NOT FOUND'}: {candidate}",
            )
        )

    return results


def _check_commands_in_code_block(
    lines: list[str],
    block_start: int,
    block_end: int,
    repo_root: str,
) -> list[ModelDocCrossRefCheck]:
    """Check shell commands in a code block for valid references."""
    results: list[ModelDocCrossRefCheck] = []

    for idx in range(block_start, block_end):
        line = lines[idx].strip().lstrip("$ ").lstrip("# ")

        # Check for script paths in commands
        script_pattern = re.compile(
            r"(?:bash\s+|python3?\s+|uv\s+run\s+python\s+)(\S+)"
        )
        for match in script_pattern.finditer(line):
            script_path = match.group(1)
            if "/" not in script_path:
                continue
            candidate = Path(repo_root) / script_path
            # Also try absolute path
            if not candidate.exists() and Path(script_path).is_absolute():
                candidate = Path(script_path)
            exists = candidate.exists()
            results.append(
                ModelDocCrossRefCheck(
                    instruction=line,
                    line_number=idx + 1,
                    check_type="command",
                    verified=exists,
                    evidence=(
                        f"Script {'exists' if exists else 'NOT FOUND'}: {script_path}"
                    ),
                )
            )

    return results


def _check_table_entries(
    line: str, line_number: int, repo_root: str
) -> list[ModelDocCrossRefCheck]:
    """Check table entries that reference directories or files."""
    results: list[ModelDocCrossRefCheck] = []

    # Match table cells with directory-like entries
    cells = [c.strip() for c in line.split("|") if c.strip()]
    for cell in cells:
        # Check if cell looks like a directory path (ends with /)
        dir_pattern = re.compile(r"`([a-zA-Z0-9_/-]+/)`")
        for match in dir_pattern.finditer(cell):
            dir_path = match.group(1)
            candidate = Path(repo_root) / dir_path
            if candidate.exists():
                results.append(
                    ModelDocCrossRefCheck(
                        instruction=cell,
                        line_number=line_number,
                        check_type="table",
                        verified=True,
                        evidence=f"Directory exists: {candidate}",
                    )
                )
            else:
                results.append(
                    ModelDocCrossRefCheck(
                        instruction=cell,
                        line_number=line_number,
                        check_type="table",
                        verified=False,
                        evidence=f"Directory NOT FOUND: {candidate}",
                    )
                )

    return results


def _check_conventions(lines: list[str], repo_root: str) -> list[ModelDocCrossRefCheck]:
    """Spot-check conventions against recent git history."""
    results: list[ModelDocCrossRefCheck] = []

    for idx, line in enumerate(lines):
        lower = line.lower()

        # Check "never use pip" convention
        if "never use" in lower and "pip" in lower:
            try:
                result = subprocess.run(
                    [
                        "git",
                        "log",
                        "--oneline",
                        "--since=30 days ago",
                        "--all",
                        "-S",
                        "pip install",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=repo_root,
                    timeout=10,
                    check=False,
                )
                violations = (
                    result.stdout.strip().splitlines() if result.stdout.strip() else []
                )
                results.append(
                    ModelDocCrossRefCheck(
                        instruction=line.strip(),
                        line_number=idx + 1,
                        check_type="convention",
                        verified=len(violations) == 0,
                        evidence=(
                            "No 'pip install' found in last 30 days"
                            if not violations
                            else f"Found {len(violations)} commits with 'pip install'"
                        ),
                    )
                )
            except (subprocess.TimeoutExpired, OSError):
                pass

        # Check "always use uv run" convention
        if "always use" in lower and "uv run" in lower:
            try:
                result = subprocess.run(
                    [
                        "git",
                        "log",
                        "--oneline",
                        "--since=30 days ago",
                        "--all",
                        "-S",
                        "uv run",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=repo_root,
                    timeout=10,
                    check=False,
                )
                usage_count = (
                    len(result.stdout.strip().splitlines())
                    if result.stdout.strip()
                    else 0
                )
                results.append(
                    ModelDocCrossRefCheck(
                        instruction=line.strip(),
                        line_number=idx + 1,
                        check_type="convention",
                        verified=usage_count > 0,
                        evidence=(
                            f"Found {usage_count} commits referencing"
                            " 'uv run' in last 30 days"
                        ),
                    )
                )
            except (subprocess.TimeoutExpired, OSError):
                pass

    return results


def check_claude_md(
    claude_md_path: str,
    repo_root: str,
) -> list[ModelDocCrossRefCheck]:
    """Run all cross-reference checks against a CLAUDE.md file.

    Args:
        claude_md_path: Path to the CLAUDE.md file.
        repo_root: Root directory of the repository.

    Returns:
        List of cross-reference check results.
    """
    path = Path(claude_md_path)
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").splitlines()
    results: list[ModelDocCrossRefCheck] = []

    in_code_block = False
    code_block_start = 0

    for idx, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code_block:
                # End of code block -- check commands
                results.extend(
                    _check_commands_in_code_block(
                        lines, code_block_start, idx, repo_root
                    )
                )
            else:
                code_block_start = idx + 1
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        # Check paths in regular lines
        results.extend(_check_paths_in_line(line, idx + 1, repo_root))

        # Check table entries
        if "|" in line:
            results.extend(_check_table_entries(line, idx + 1, repo_root))

    # Check conventions
    results.extend(_check_conventions(lines, repo_root))

    return results
