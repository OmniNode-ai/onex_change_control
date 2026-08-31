# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Validate that no localhost/service-endpoint fallbacks exist in src/ Python files.

Scans src/**/*.py for patterns like:
  - os.environ.get("...", "localhost...")
  - os.environ.get("...", "http://localhost...")
  - os.environ.get("...", "bolt://localhost...")
  - os.environ.get("...", "redis://localhost...")
  - os.environ.get("...", "postgresql://localhost...")

Exits non-zero if any violations are found.
Skips test files (tests/, __tests__/).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

VIOLATION_PATTERNS = [
    re.compile(
        r'os\.environ\.get\([^)]*,\s*"(localhost|http://localhost|bolt://localhost|redis://localhost|postgresql://localhost)'
    ),
    re.compile(
        r'os\.getenv\([^)]*,\s*"(localhost|http://localhost|bolt://localhost|redis://localhost|postgresql://localhost)'
    ),
]

SKIP_DIRS = {"tests", "__tests__", "test", "__pycache__"}


def scan(root: Path) -> list[tuple[str, int, str]]:
    violations: list[tuple[str, int, str]] = []
    for py_file in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in py_file.parts):
            continue
        try:
            lines = py_file.read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, start=1):
            for pattern in VIOLATION_PATTERNS:
                if pattern.search(line):
                    violations.append((str(py_file), lineno, line.strip()))
                    break
    return violations


def main() -> int:
    src_dir = Path(__file__).resolve().parent.parent.parent / "src"
    if not src_dir.is_dir():
        print(f"ERROR: src directory not found at {src_dir}", file=sys.stderr)
        return 1

    violations = scan(src_dir)
    if violations:
        print(f"Found {len(violations)} localhost fallback(s):\n")
        for filepath, lineno, line in violations:
            print(f"  {filepath}:{lineno}: {line}")
        print("\nAll localhost/service-endpoint fallbacks must be removed from src/.")
        print("Use os.environ[KEY] (fail-fast) or skip emission when var is unset.")
        return 1

    print("OK: No localhost fallbacks found in src/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
