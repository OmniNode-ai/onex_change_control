# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Pre-commit hook: require all TODO/FIXME/HACK comments to reference a Linear ticket.

Usage:
    check-todo-format [files...]   (via entry point)
    python -m onex_change_control.scripts.check_todo_format [files...]

Exit code 0 = clean. Exit code 1 = violations found.

Valid format:  # TODO(OMN-1234): description
Invalid:       # TODO: description   (no ticket reference)

Centralised in onex_change_control so downstream repos consume
a single canonical copy via the ``check-todo-format`` entry point.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Any TODO/FIXME/HACK marker NOT immediately followed by (OMN-<digits>):
# This single pattern detects bare markers even when a valid marker is also present.
INVALID_MARKER = re.compile(r"\b(?:TODO|FIXME|HACK)\b(?!\(OMN-\d+\):)")

# Exemption marker: allows legacy TODOs to survive with a stated reason.
EXEMPT = re.compile(r"#\s*TODO_FORMAT_EXEMPT:\s*\S")

# Path segments that are excluded from scanning.
# Use bare directory names so both absolute (/tests/) and relative (tests/) paths match.
EXCLUDED_SEGMENTS: frozenset[str] = frozenset(
    {"tests", "docs", "examples", "fixtures", "vendored"}
)

# Basenames that are excluded (this script itself, for example).
EXCLUDED_BASENAMES: frozenset[str] = frozenset({"check_todo_format.py"})


def _update_block_comment(stripped: str, *, in_block: bool) -> bool:
    """Return updated ``in_block_comment`` state for a single line."""
    if in_block:
        return "*/" not in stripped
    return "/*" in stripped and (
        "*/" not in stripped or stripped.index("/*") > stripped.index("*/")
    )


def _update_docstring(stripped: str, *, in_docstring: bool) -> bool:
    """Return updated ``in_docstring`` state for a single line."""
    for delim in ('"""', "'''"):
        count = stripped.count(delim)
        if count == 1:
            in_docstring = not in_docstring
        # count >= 2 means open+close on same line; state unchanged.
    return in_docstring


def _line_has_comment(line: str) -> bool:
    """Return True if the line contains a Python comment outside of strings.

    Simple heuristic: find ``#`` that is not inside a quoted string.
    """
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            i += 2  # skip escaped character
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return True
        i += 1
    return False


def _extract_comment(line: str) -> str | None:
    """Extract the comment portion of a line (everything from ``#`` onward).

    Returns None if there is no comment outside of string literals.
    """
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[i:]
        i += 1
    return None


def _scan_lines(text: str, path: str) -> list[str]:
    """Scan *text* line-by-line and return violation messages."""
    violations: list[str] = []
    in_docstring = False
    in_block_comment = False

    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()

        # Track JS/TS/CSS block comments: /* ... */
        in_block_comment = _update_block_comment(stripped, in_block=in_block_comment)
        if in_block_comment or (stripped.startswith("*/") and not in_block_comment):
            continue

        # Track triple-quote docstrings (simple heuristic).
        in_docstring = _update_docstring(stripped, in_docstring=in_docstring)
        if in_docstring:
            continue

        # Extract comment portion only (ignore string contents).
        comment = _extract_comment(line)
        if comment is None:
            continue

        # Check the comment text for exemption (not full line, to avoid
        # false matches when TODO_FORMAT_EXEMPT appears in string literals).
        if EXEMPT.search(comment):
            continue

        # Flag any TODO/FIXME/HACK not followed by (OMN-XXXX): -- catches
        # bare markers even when a valid marker is also present on the line.
        if INVALID_MARKER.search(comment):
            violations.append(
                f"{path}:{lineno}: bare TODO/FIXME/HACK without ticket reference"
                " -- use format: # TODO(OMN-XXXX): description"
            )

    return violations


def check_file(path: str) -> list[str]:
    """Return violations for a single file."""
    basename = Path(path).name

    # Skip excluded basenames.
    if basename in EXCLUDED_BASENAMES:
        return []

    # Skip excluded path segments (works for both absolute and relative paths).
    path_parts = Path(path).parts
    if any(segment in path_parts for segment in EXCLUDED_SEGMENTS):
        return []

    # Only scan Python files.
    if not path.endswith(".py"):
        return []

    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    return _scan_lines(text, path)


def main() -> int:
    """Entry point for check-todo-format."""
    files = sys.argv[1:]
    all_violations: list[str] = []
    for path in files:
        all_violations.extend(check_file(path))
    if all_violations:
        for v in all_violations:
            print(v)
        print(
            f"\n{len(all_violations)} violation(s)."
            " Use format: # TODO(OMN-XXXX): description"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
