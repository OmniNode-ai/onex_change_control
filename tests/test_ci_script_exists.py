# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Regression guard: verify the contract compliance CI script exists and is executable.

Other repos clone onex_change_control and invoke
  scripts/ci/run_contract_compliance_check.py
directly in their CI pipelines.  If that file is moved, renamed, or deleted,
those pipelines silently fail.  This test ensures the path contract is
enforced within onex_change_control itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Root of the onex_change_control repo (two levels up from tests/)
REPO_ROOT = Path(__file__).parent.parent.resolve()
CI_SCRIPT = REPO_ROOT / "scripts" / "ci" / "run_contract_compliance_check.py"


@pytest.mark.unit
def test_ci_compliance_script_exists() -> None:
    """scripts/ci/run_contract_compliance_check.py must exist at the canonical path."""
    assert CI_SCRIPT.exists(), (
        f"Contract compliance CI script not found at expected path: {CI_SCRIPT}\n"
        "Other repos depend on this path. Do not move or rename this file without "
        "updating all downstream CI workflows."
    )


@pytest.mark.unit
def test_ci_compliance_script_is_executable() -> None:
    """scripts/ci/run_contract_compliance_check.py must be executable."""
    assert CI_SCRIPT.exists(), f"Script not found: {CI_SCRIPT}"
    assert os.access(CI_SCRIPT, os.X_OK), (
        f"Contract compliance CI script is not executable: {CI_SCRIPT}\n"
        "Run: chmod +x scripts/ci/run_contract_compliance_check.py"
    )


@pytest.mark.unit
def test_ci_compliance_script_help_exits_cleanly() -> None:
    """scripts/ci/run_contract_compliance_check.py --help must exit 0."""
    assert CI_SCRIPT.exists(), f"Script not found: {CI_SCRIPT}"
    result = subprocess.run(
        [sys.executable, str(CI_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"Script --help returned non-zero exit code {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "--pr" in result.stdout, "Expected --pr argument in --help output"
    assert "--repo" in result.stdout, "Expected --repo argument in --help output"
    assert "--contracts-dir" in result.stdout, (
        "Expected --contracts-dir argument in --help output"
    )
