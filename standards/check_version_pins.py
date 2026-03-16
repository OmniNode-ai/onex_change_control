#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""check_version_pins.py - Validate dependency pins against version-matrix.yaml.

Reads the version matrix, parses the target repo's pyproject.toml, and
exits non-zero if any pinned dependency version is behind the expected
version.

Usage::

    python check_version_pins.py --repo omnibase_core \\
        --root /path/to/onex_change_control

    python check_version_pins.py --repo omnibase_infra \\
        --root /path/to/onex_change_control \\
        --repo-path /path/to/omnibase_infra

    python check_version_pins.py --repo omnibase_infra \\
        --root /path/to/onex_change_control \\
        --check-dockerfile \\
        --repo-path /path/to/omnibase_infra

Exit codes:
    0 - all pins match (or repo not managed / no expected pins)
    1 - one or more pins are behind
    2 - configuration error (missing files, bad arguments)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from packaging.version import Version

logger = logging.getLogger(__name__)

# Avoid external dependencies beyond what is standard in ONEX repos.
# This script requires PyYAML and packaging (both available in all
# ONEX repos).
try:
    import yaml
except ImportError:
    # Fallback: try ruamel.yaml (available in some repos)
    try:
        from ruamel.yaml import YAML

        def _load_yaml(path: Path) -> Any:
            return YAML(typ="safe").load(path)

    except ImportError:
        logger.exception(
            "Neither PyYAML nor ruamel.yaml is available. "
            "Install one of them to run this script.",
        )
        sys.exit(2)
    else:
        _yaml_loader = _load_yaml
else:

    def _yaml_loader(path: Path) -> Any:
        with path.open() as f:
            return yaml.safe_load(f)


def load_matrix(root: Path) -> dict[str, Any]:
    """Load version-matrix.yaml from the standards dir."""
    matrix_path = root / "standards" / "version-matrix.yaml"
    if not matrix_path.exists():
        logger.error("version-matrix.yaml not found at %s", matrix_path)
        sys.exit(2)
    result: dict[str, Any] = _yaml_loader(matrix_path)
    return result


def extract_pins_from_pyproject(
    pyproject_path: Path,
) -> dict[str, tuple[str, str]]:
    """Extract pinned dependency versions from pyproject.toml.

    Scans the entire file for patterns like::

        "package-name==1.2.3"
        'package-name>=1.2.3'
        package-name~=1.2.3

    Returns a dict of {package_name: (operator, version)}.
    """
    if not pyproject_path.exists():
        logger.error("pyproject.toml not found at %s", pyproject_path)
        sys.exit(2)

    content = pyproject_path.read_text()
    pin_pattern = re.compile(r'["\']?([\w-]+)(==|>=|~=)([\d.]+)["\']?')
    pins: dict[str, tuple[str, str]] = {}
    for match in pin_pattern.finditer(content):
        pkg_name = match.group(1)
        operator = match.group(2)
        pkg_version = match.group(3)
        # Only track ONEX packages (omnibase-*, omninode-*)
        if pkg_name.startswith(("omnibase-", "omninode-")):
            pins[pkg_name] = (operator, pkg_version)
    return pins


def extract_pins_from_dockerfile(
    dockerfile_path: Path,
) -> dict[str, tuple[str, str]]:
    """Extract pinned package versions from Dockerfile.runtime.

    Returns a dict of {package_name: (operator, version)}.
    """
    if not dockerfile_path.exists():
        logger.error(
            "Dockerfile.runtime not found at %s",
            dockerfile_path,
        )
        sys.exit(2)

    content = dockerfile_path.read_text()
    pin_pattern = re.compile(r'["\']?([\w-]+)(==|>=|~=)([\d.]+)["\']?')
    pins: dict[str, tuple[str, str]] = {}
    for match in pin_pattern.finditer(content):
        pkg_name = match.group(1)
        operator = match.group(2)
        pkg_version = match.group(3)
        if pkg_name.startswith(("omnibase-", "omninode-")):
            pins[pkg_name] = (operator, pkg_version)
    return pins


def check_pins(
    actual_pins: dict[str, tuple[str, str]],
    expected_pins: dict[str, str],
    context: str,
) -> list[str]:
    """Compare actual pins against expected.

    For all operators (==, >=, ~=): the actual version floor must be at
    least as high as the expected minimum from the version matrix.

    Returns list of error messages.
    """
    errors: list[str] = []
    for pkg, expected_version in expected_pins.items():
        actual = actual_pins.get(pkg)
        if actual is None:
            errors.append(
                f"  {context}: {pkg} not found (expected >={expected_version})",
            )
        else:
            operator, actual_version = actual
            if Version(actual_version) >= Version(expected_version):
                pass  # compliant
            else:
                errors.append(
                    f"  {context}: {pkg}{operator}{actual_version}"
                    f" (expected >={expected_version})",
                )
    return errors


def _write_output(
    *,
    repo: str,
    all_errors: list[str],
    actual_pins: dict[str, tuple[str, str]],
    expected_pins: dict[str, str],
    json_output: bool,
) -> None:
    """Write results to stdout."""
    # Serialize actual_pins for JSON: flatten tuples to "op+version" strings
    actual_pins_flat = {pkg: f"{op}{ver}" for pkg, (op, ver) in actual_pins.items()}
    if json_output:
        result = {
            "repo": repo,
            "status": "fail" if all_errors else "pass",
            "errors": all_errors,
            "actual_pins": actual_pins_flat,
            "expected_pins": expected_pins,
        }
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    elif all_errors:
        sys.stdout.write(
            f"FAIL: {repo} has {len(all_errors)} pin drift(s):\n",
        )
        for error in all_errors:
            sys.stdout.write(error + "\n")
    elif not expected_pins:
        sys.stdout.write(
            f"PASS: {repo} — no expected pins defined.\n",
        )
    else:
        sys.stdout.write(
            f"PASS: {repo} -- all pins match version-matrix.yaml\n",
        )


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Validate dependency pins against version-matrix.yaml",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Repository name to check (e.g., omnibase_core)",
    )
    parser.add_argument(
        "--root",
        required=True,
        type=Path,
        help="Path to onex_change_control root",
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=None,
        help="Path to the target repository root",
    )
    parser.add_argument(
        "--check-dockerfile",
        action="store_true",
        help="Also validate Dockerfile.runtime pins",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    matrix = load_matrix(args.root)

    # Validate repo exists in matrix
    repos = matrix.get("repos", {})
    if args.repo not in repos:
        sys.stdout.write(
            f"INFO: repo '{args.repo}' is not managed by version-matrix.yaml"
            " — skipping.\n",
        )
        return 0

    repo_config = repos[args.repo]
    expected_pins = repo_config.get("expected_pins", {})

    # Resolve repo path
    repo_path = args.repo_path if args.repo_path else args.root.parent / args.repo

    # Check pyproject.toml pins
    pyproject_path = repo_path / repo_config.get("pyproject_path", "pyproject.toml")
    actual_pins = extract_pins_from_pyproject(pyproject_path)

    all_errors: list[str] = []
    if expected_pins:
        errors = check_pins(
            actual_pins,
            expected_pins,
            f"{args.repo}/pyproject.toml",
        )
        all_errors.extend(errors)

    # Check Dockerfile.runtime pins if requested
    if args.check_dockerfile:
        dockerfile_config = matrix.get("dockerfile_runtime", {})
        dockerfile_path_rel = dockerfile_config.get("path", "docker/Dockerfile.runtime")
        dockerfile_expected = dockerfile_config.get("expected_pins", {})

        if dockerfile_expected:
            dockerfile_path = repo_path / dockerfile_path_rel
            dockerfile_pins = extract_pins_from_dockerfile(
                dockerfile_path,
            )
            errors = check_pins(
                dockerfile_pins,
                dockerfile_expected,
                f"{args.repo}/Dockerfile.runtime",
            )
            all_errors.extend(errors)

    _write_output(
        repo=args.repo,
        all_errors=all_errors,
        actual_pins=actual_pins,
        expected_pins=expected_pins,
        json_output=args.json_output,
    )

    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
