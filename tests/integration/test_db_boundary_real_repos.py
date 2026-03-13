# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Integration tests: DB boundary validator against real repos.

Confirms zero cross-service violations in the actual codebase,
providing real evidence for Epic 3 ticket re-verification.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# Base path for sibling repos in omni_home
_OMNI_HOME = Path("/Volumes/PRO-G40/Code/omni_home")

# Worktree path for registry and cwd
_WORKTREE = Path(
    "/Volumes/PRO-G40/Code/omni_worktrees/OMN-4815/onex_change_control",
)

# Registry path for exception filtering
_REGISTRY = _WORKTREE / "registry/db-boundary-exceptions.yaml"

# Services to scan with their repo paths
_SERVICES = [
    ("omniintelligence", _OMNI_HOME / "omniintelligence"),
    ("omnimemory", _OMNI_HOME / "omnimemory"),
    ("omnibase_infra", _OMNI_HOME / "omnibase_infra"),
]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("service", "repo_path"),
    _SERVICES,
    ids=[s[0] for s in _SERVICES],
)
def test_no_cross_service_violations(
    service: str,
    repo_path: Path,
) -> None:
    """Scan a real service repo for DB boundary violations.

    Expects zero violations (exit 0) for all services.
    Skips if the repo is not available locally.
    """
    if not repo_path.exists():
        pytest.skip(f"Repo not available: {repo_path}")

    cmd = [
        "uv",
        "run",
        "check-db-boundary",
        "--repo",
        service,
        "--path",
        str(repo_path),
        "--no-color",
    ]
    if _REGISTRY.exists():
        cmd.extend(["--registry", str(_REGISTRY)])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(_WORKTREE),
        check=False,
    )
    assert result.returncode == 0, (
        f"check-db-boundary failed for {service}:\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


@pytest.mark.integration
def test_exceptions_registry_valid() -> None:
    """Validate the exceptions registry YAML."""
    registry = _WORKTREE / "registry/db-boundary-exceptions.yaml"
    if not registry.exists():
        pytest.skip(f"Registry not found: {registry}")

    result = subprocess.run(
        [  # noqa: S607
            "uv",
            "run",
            "check-db-boundary",
            "--validate-all",
            "--registry",
            str(registry),
            "--no-color",
        ],
        capture_output=True,
        text=True,
        cwd=str(_WORKTREE),
        check=False,
    )
    assert result.returncode == 0, (
        f"Registry validation failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
