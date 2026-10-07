# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""sync_version_matrix.py — Update version-matrix.yaml from actual git tags.

Scans each ONEX repo under --root for its latest semver tag (vX.Y.Z),
then updates the version-matrix.yaml so that expected_pins reflect the
actual published state.

Usage::

    python scripts/sync_version_matrix.py --root /path/to/omni_home

    # Dry-run (default): show what would change
    python scripts/sync_version_matrix.py --root /path/to/omni_home

    # Write changes
    python scripts/sync_version_matrix.py --root /path/to/omni_home --write

Exit codes:
    0 - matrix is up to date (or --write succeeded)
    1 - matrix is stale (dry-run mode)
    2 - configuration error
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

# Mapping from pypi package name to repo directory name
_PACKAGE_TO_REPO: dict[str, str] = {
    "omnibase-core": "omnibase_core",
    "omnibase-spi": "omnibase_spi",
    "omnibase-infra": "omnibase_infra",
    "omninode-intelligence": "omniintelligence",
    "omninode-claude": "omniclaude",
    "omninode-memory": "omnimemory",
}

_SEMVER_TAG_RE = re.compile(r"^v?(\d+\.\d+\.\d+)$")


def get_latest_tag(repo_path: Path) -> str | None:
    """Get the latest semver tag from a git repo."""
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo_path), "tag", "--sort=-v:refname"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    for line in result.stdout.strip().splitlines():
        tag = line.strip()
        match = _SEMVER_TAG_RE.match(tag)
        if match:
            return match.group(1)
    return None


def load_matrix(matrix_path: Path) -> dict[str, Any]:
    """Load the version matrix YAML."""
    with matrix_path.open() as f:
        data: dict[str, Any] = yaml.safe_load(f)
    return data


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Sync version-matrix.yaml with actual git tags",
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Root directory containing cloned repos",
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=None,
        help="Path to version-matrix.yaml",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write updated matrix (default: dry-run)",
    )
    return parser.parse_args()


def _update_package_versions(
    packages: dict[str, Any],
    root: Path,
) -> list[str]:
    """Update package versions from git tags. Returns list of changes."""
    changes: list[str] = []
    for pkg_name, pkg_info in packages.items():
        repo_dir = _PACKAGE_TO_REPO.get(pkg_name)
        if not repo_dir:
            continue
        repo_path = root / repo_dir
        if not repo_path.is_dir():
            print(f"  SKIP {pkg_name}: repo not found")
            continue

        latest = get_latest_tag(repo_path)
        if latest is None:
            print(f"  SKIP {pkg_name}: no semver tags")
            continue

        current = pkg_info.get("version", "")
        if current != latest:
            changes.append(
                f"  packages.{pkg_name}: {current} -> {latest}",
            )
            pkg_info["version"] = latest
    return changes


def _update_expected_pins(
    pins: dict[str, str],
    latest_versions: dict[str, str],
    prefix: str,
) -> list[str]:
    """Update expected_pins dict. Returns list of changes."""
    changes: list[str] = []
    for dep_name, expected_version in list(pins.items()):
        if dep_name not in latest_versions:
            continue
        latest = latest_versions[dep_name]
        if expected_version != latest:
            changes.append(
                f"  {prefix}.{dep_name}: {expected_version} -> {latest}",
            )
            pins[dep_name] = latest
    return changes


def main() -> int:
    """Entry point."""
    args = _parse_args()

    root: Path = args.root.resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        return 2

    matrix_path: Path = (
        args.matrix
        if args.matrix
        else Path(__file__).resolve().parent.parent
        / "standards"
        / "version-matrix.yaml"
    )
    if not matrix_path.exists():
        print(f"ERROR: {matrix_path} not found", file=sys.stderr)
        return 2

    matrix = load_matrix(matrix_path)
    packages = matrix.get("packages", {})

    changes = _update_package_versions(packages, root)

    # Build lookup: package_name -> latest version
    latest_versions: dict[str, str] = {
        pkg: info.get("version", "") for pkg, info in packages.items()
    }

    for repo_name, repo_config in matrix.get("repos", {}).items():
        pins = repo_config.get("expected_pins", {})
        changes.extend(
            _update_expected_pins(
                pins,
                latest_versions,
                f"repos.{repo_name}.expected_pins",
            ),
        )

    dockerfile_config = matrix.get("dockerfile_runtime", {})
    dockerfile_pins = dockerfile_config.get("expected_pins", {})
    changes.extend(
        _update_expected_pins(
            dockerfile_pins,
            latest_versions,
            "dockerfile_runtime.expected_pins",
        ),
    )

    if not changes:
        print("version-matrix.yaml is up to date.")
        return 0

    print(f"Found {len(changes)} stale version(s):")
    for change in changes:
        print(change)

    if args.write:
        with matrix_path.open("w") as f:
            yaml.dump(matrix, f, default_flow_style=False, sort_keys=False)
        print(f"\nWrote updated matrix to {matrix_path}")
        return 0

    print("\nRun with --write to update the file.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
