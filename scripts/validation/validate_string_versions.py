#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Validate version fields across Python and YAML files.

Checks:
1. Python __init__.py files must not contain hardcoded __version__ strings.
2. YAML files must not use string-shaped version fields (e.g. ``schema_version: "1.0.0"``).

POLICY — Ticket contract exemption (OMN-9593):
    Files matching ``contracts/OMN-*.yaml`` are governance artifacts validated
    by ``ModelTicketContract.schema_version`` (a ``str`` field checked against
    ``SEMVER_PATTERN``).  Their ``schema_version`` is intentionally string-shaped
    because ticket contracts are authored by humans in YAML, consumed by the
    validate-yaml CLI, and never deserialized into a ModelSemVer at runtime.
    The field_validator on ``ModelTicketContract`` provides equivalent SemVer
    enforcement, so the generic string-version ban does not apply.

Adapted from omnibase_core/scripts/validation/validate-string-versions.py.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_SEMVER_RE = re.compile(r"^\"?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\"?$")

# Governance artifacts whose schema_version is intentionally string-shaped
# and validated by ModelTicketContract.field_validator instead.
TICKET_CONTRACT_GLOB = re.compile(r"contracts/OMN-\d+\.yaml$")


def _is_ticket_contract(path: Path) -> bool:
    return bool(TICKET_CONTRACT_GLOB.search(str(path)))


# ---------------------------------------------------------------------------
# Python __init__.py checks
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# YAML string-version checks
# ---------------------------------------------------------------------------

_VERSION_FIELD_NAMES = {"version", "contract_version", "node_version", "protocol_version"}


def _has_string_version_in_yaml(path: Path) -> list[tuple[int, str]]:
    """Return (line_number, message) for YAML files with string version fields.

    Skips files matching the ticket-contract exemption (OMN-9593).
    """
    if _is_ticket_contract(path):
        return []

    violations: list[tuple[int, str]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return violations

    for line_num, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        colon = stripped.find(":")
        if colon == -1:
            continue
        field = stripped[:colon].strip()
        value = stripped[colon + 1 :].strip()
        if field not in _VERSION_FIELD_NAMES and "version" not in field.lower():
            continue
        if _SEMVER_RE.match(value):
            violations.append(
                (
                    line_num,
                    f"string version '{value}' — use ModelSemVer mapping "
                    f"{{major: X, minor: Y, patch: Z}}",
                )
            )
    return violations


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Check files passed by pre-commit for version anti-patterns."""
    found = 0

    paths = [Path(f) for f in sys.argv[1:]]

    # Python __init__.py check
    for path in paths:
        if path.name != "__init__.py" or not path.suffix == ".py":
            continue
        for lineno, src_line in _has_hardcoded_version(path):
            print(
                f"{path}:{lineno}: hardcoded __version__ found: {src_line}\n"
                f'  Use importlib.metadata.version("package-name") instead.'
            )
            found += 1

    # YAML string-version check
    for path in paths:
        if path.suffix not in (".yaml", ".yml"):
            continue
        for lineno, msg in _has_string_version_in_yaml(path):
            print(f"{path}:{lineno}: {msg}")
            found += 1

    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
