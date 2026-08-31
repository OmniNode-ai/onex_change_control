# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression tests for the validate-contract composite action."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTION_PATH = REPO_ROOT / ".github" / "actions" / "validate-contract" / "action.yml"


def _load_action() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(ACTION_PATH.read_text()))


def test_validate_contract_validator_install_retries_egress_failures() -> None:
    """The validator install should tolerate transient GitHub/TLS fetch errors."""
    action = _load_action()
    steps = action["runs"]["steps"]

    setup_python_step = next(
        step for step in steps if step.get("name") == "Set up Python"
    )
    setup_uv_step = next(step for step in steps if step.get("name") == "Install uv")
    install_step = next(
        step
        for step in steps
        if step.get("name") == "Install onex_change_control validators"
    )

    assert setup_python_step["uses"] == "actions/setup-python@v6"
    assert setup_uv_step["uses"] == "astral-sh/setup-uv@v7"
    assert install_step["env"]["UV_HTTP_TIMEOUT"] == "600"
    assert install_step["env"]["UV_SYNC_ATTEMPTS"] == "5"
    assert install_step["env"]["UV_SYNC_RETRY_DELAY_SECONDS"] == "10"
    assert install_step["env"]["UV_CONCURRENT_DOWNLOADS"] == "2"

    run_script = install_step["run"]
    assert "git config --global http.version HTTP/1.1" in run_script
    assert "until uv sync --no-group dev; do" in run_script
    assert (
        'echo "::warning::uv sync attempt ${attempt}/${UV_SYNC_ATTEMPTS} failed'
        in run_script
    )
    assert 'echo "::error::uv sync failed after ${attempt} attempt(s)"' in run_script
