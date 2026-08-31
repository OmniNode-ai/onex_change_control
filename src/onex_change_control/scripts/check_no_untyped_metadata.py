# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Pre-commit hook: fail if metadata fields use dict[str, Any] without ONEX_EXCLUDE.

Usage:
    check-no-untyped-metadata [files...]   (via entry point)
    python -m onex_change_control.scripts.check_no_untyped_metadata [files...]

Exit code 0 = clean. Exit code 1 = violations found.

Centralized in onex_change_control (OMN-5135) so downstream repos consume
a single canonical copy via the ``check-no-untyped-metadata`` entry point.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERN = re.compile(
    r"metadata\s*:\s*(?:Optional\[)?dict\[str,\s*(?:Any|object)\]",
)
EXCLUDE_COMMENT = "ONEX_EXCLUDE:"


def check_file(path: str) -> list[str]:
    """Return violations for a single file."""
    violations: list[str] = []
    with Path(path).open() as f:
        for lineno, line in enumerate(f, 1):
            if PATTERN.search(line) and EXCLUDE_COMMENT not in line:
                violations.append(
                    f"{path}:{lineno}: untyped metadata dict"
                    " -- use TypedDict or add ONEX_EXCLUDE comment"
                )
    return violations


def main() -> int:
    """Entry point for check-no-untyped-metadata."""
    files = sys.argv[1:]
    all_violations: list[str] = []
    for path in files:
        if path.endswith(".py"):
            all_violations.extend(check_file(path))
    if all_violations:
        for v in all_violations:
            print(v)
        print(
            f"\n{len(all_violations)} violation(s)."
            " Replace dict[str, Any] with TypedDict."
        )
        print("If intentional, add: # ONEX_EXCLUDE: dict_str_any - <reason>")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
