#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Validate that __init__.py files do not contain hardcoded __version__ strings.

Versions should come from pyproject.toml via importlib.metadata, never from
hardcoded string literals in source code.  This hook prevents reintroduction
of the anti-pattern after OMN-3831 removed existing instances.

Adapted from omnibase_core/scripts/validation/validate-string-versions.py.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def _is_inside_except_handler(node: ast.AST, tree: ast.Module) -> bool:
    """Return True if *node* is inside an ``except`` handler (fallback pattern)."""
    for parent in ast.walk(tree):
        if isinstance(parent, ast.ExceptHandler):
            for child in ast.walk(parent):
                if child is node:
                    return True
    return False


def _has_hardcoded_version(path: Path) -> list[tuple[int, str]]:
    """Return list of (line_number, source_line) for __version__ assignments.

    Assignments inside ``except`` blocks are allowed because they represent
    fallback values (e.g. ``except PackageNotFoundError: __version__ = ...``).
    """
    violations: list[tuple[int, str]] = []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    if isinstance(node.value, (ast.Constant, ast.JoinedStr)):
                        if _is_inside_except_handler(node, tree):
                            continue
                        lineno = node.lineno
                        lines = source.splitlines()
                        src_line = lines[lineno - 1] if lineno <= len(lines) else ""
                        violations.append((lineno, src_line.strip()))
    return violations


def main() -> int:
    """Check files passed by pre-commit for hardcoded __version__."""
    # Only check __init__.py files (pre-commit passes filenames as args)
    init_files = [
        Path(f) for f in sys.argv[1:] if Path(f).name == "__init__.py"
    ]

    found = 0
    for path in init_files:
        for lineno, src_line in _has_hardcoded_version(path):
            print(
                f"{path}:{lineno}: hardcoded __version__ found: {src_line}\n"
                f"  Use importlib.metadata.version(\"package-name\") instead."
            )
            found += 1

    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
