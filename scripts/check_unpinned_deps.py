#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""check_unpinned_deps.py — Scan repos for unpinned dependencies in pyproject.toml.

Usage:
    python3 scripts/check_unpinned_deps.py --root /path/to/omni_home

Checks every pyproject.toml found one level deep under --root for dependencies
that lack version constraints (no ==, >=, ~=, <, >, != operators).

Exit 0 = all deps pinned.  Exit 1 = unpinned deps found.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

# PEP 508 version specifier operators
_VERSION_OP_RE = re.compile(r"(~=|==|!=|<=?|>=?)")


def _parse_dep_name(dep: str) -> str:
    """Extract the package name from a PEP 508 dependency string."""
    # Strip extras like "package[extra]>=1.0"
    match = re.match(r"^([A-Za-z0-9_.-]+)", dep)
    return match.group(1) if match else dep


def _has_version_constraint(dep: str) -> bool:
    """Return True if the dependency string contains any version constraint."""
    return bool(_VERSION_OP_RE.search(dep))


def check_pyproject(path: Path) -> list[str]:
    """Return list of unpinned dependency strings from a pyproject.toml."""
    with path.open("rb") as f:
        data = tomllib.load(f)

    violations: list[str] = []

    # Check project.dependencies section
    for dep in data.get("project", {}).get("dependencies", []):
        if not _has_version_constraint(dep):
            violations.append(dep)

    # Check project.optional-dependencies section
    for group_deps in data.get("project", {}).get("optional-dependencies", {}).values():
        for dep in group_deps:
            if not _has_version_constraint(dep):
                violations.append(dep)

    # [dependency-groups] (PEP 735)
    for group_deps in data.get("dependency-groups", {}).values():
        for dep in group_deps:
            # dependency-groups can have include directives (dicts) — skip those
            if isinstance(dep, str) and not _has_version_constraint(dep):
                violations.append(dep)

    return violations


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check for unpinned dependencies in pyproject.toml files."
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Root directory containing cloned repos (e.g. /path/to/omni_home)",
    )
    args = parser.parse_args()

    root: Path = args.root.resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        sys.exit(2)

    total_violations = 0
    repos_checked = 0

    # Scan one level deep: root/<repo>/pyproject.toml
    for pyproject in sorted(root.glob("*/pyproject.toml")):
        repo_name = pyproject.parent.name
        violations = check_pyproject(pyproject)
        repos_checked += 1

        if violations:
            total_violations += len(violations)
            print(f"\n{repo_name}/pyproject.toml — {len(violations)} unpinned dep(s):")
            for dep in violations:
                name = _parse_dep_name(dep)
                print(f"  - {name} (raw: {dep!r})")
        else:
            print(f"  {repo_name}/pyproject.toml — OK")

    print(f"\n{'=' * 50}")
    print(f"Repos checked: {repos_checked}")
    print(f"Total unpinned dependencies: {total_violations}")
    print(f"{'=' * 50}")

    if total_violations > 0:
        sys.exit(1)

    print("All dependencies have version constraints.")


if __name__ == "__main__":
    main()
