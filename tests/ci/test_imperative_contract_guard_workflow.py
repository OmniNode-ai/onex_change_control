# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression coverage for the reusable imperative contract guard workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "imperative-contract-guard.yml"


def _load_yaml(path: Path) -> dict[Any, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_imperative_contract_guard_pins_uv_and_retries_sync() -> None:
    workflow = _load_yaml(WORKFLOW)
    inputs = workflow[True]["workflow_call"]["inputs"]

    assert inputs["uv-version"]["default"] == "0.8.3"

    steps = workflow["jobs"]["imperative-contract-guard"]["steps"]
    setup_uv_step = next(
        step for step in steps if step.get("uses") == "astral-sh/setup-uv@v7"
    )
    assert setup_uv_step["with"]["version"] == "${{ inputs['uv-version'] }}"

    guard_step = next(
        step for step in steps if step.get("name") == "Run imperative contract guard"
    )
    assert guard_step["env"]["UV_HTTP_TIMEOUT"] == "600"
    assert guard_step["env"]["UV_SYNC_ATTEMPTS"] == "3"
    assert guard_step["env"]["UV_SYNC_RETRY_DELAY_SECONDS"] == "10"

    run_script = guard_step["run"]
    assert "git config --global http.version HTTP/1.1" in run_script
    assert "until uv sync --locked --all-extras; do" in run_script
    assert (
        'echo "::warning::uv sync attempt ${attempt}/${UV_SYNC_ATTEMPTS} failed'
        in run_script
    )
    assert 'echo "::error::uv sync failed after ${attempt} attempt(s)"' in run_script
