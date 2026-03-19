# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Pre-commit hook: reject hardcoded ONEX topic strings in unapproved files.

Usage:
    check-hardcoded-topics [files...]   (via entry point)
    python -m onex_change_control.scripts.check_hardcoded_topics [files...]

Exit code 0 = clean. Exit code 1 = violations found.

Centralised in onex_change_control (OMN-5256) so downstream repos consume
a single canonical copy via the ``check-hardcoded-topics`` entry point.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Match quoted topic literals: "onex.evt.*" or "onex.cmd.*"
TOPIC_LITERAL = re.compile(r"""["']onex\.(evt|cmd)\.""")

# Basenames of files that are allowed to define topic constants.
APPROVED_BASENAMES: frozenset[str] = frozenset(
    {
        "platform_topic_suffixes.py",
        "topics.py",
        "topics.ts",
        "contract.yaml",
        "handler_contract.yaml",
        "topics.yaml",
        "contract_topic_extractor.py",
        "check_topic_drift.py",
        "topic_constants.py",
        "constants_topic_taxonomy.py",
        "topic_naming_baseline.txt",
        "governance_emitter.py",
    }
)

# Comment prefixes to skip (stripped lines starting with these).
_COMMENT_PREFIXES = ("#", "//", "*", "/*")


def _is_test_file(path: str) -> bool:
    """Return True if *path* looks like a test file."""
    basename = Path(path).name
    return (
        "/tests/" in path
        or basename.startswith("test_")
        or basename.endswith(("_test.py", ".test.ts", ".test.js"))
    )


def _is_comment_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(_COMMENT_PREFIXES)


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
            # Inside block or on the closing */ line — skip.
            continue

        # Track triple-quote docstrings (simple heuristic).
        in_docstring = _update_docstring(stripped, in_docstring=in_docstring)
        if in_docstring:
            continue

        if _is_comment_line(line):
            continue

        if TOPIC_LITERAL.search(line):
            violations.append(
                f"{path}:{lineno}: hardcoded topic string"
                " -- use a constant from the canonical topic registry"
            )
    return violations


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


def check_file(path: str) -> list[str]:
    """Return violations for a single file."""
    basename = Path(path).name
    if basename in APPROVED_BASENAMES or _is_test_file(path):
        return []

    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    return _scan_lines(text, path)


def main() -> int:
    """Entry point for check-hardcoded-topics."""
    files = sys.argv[1:]
    all_violations: list[str] = []
    for path in files:
        all_violations.extend(check_file(path))
    if all_violations:
        for v in all_violations:
            print(v)  # noqa: T201
        print(  # noqa: T201
            f"\n{len(all_violations)} violation(s)."
            " Move topic strings to an approved constant file."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
