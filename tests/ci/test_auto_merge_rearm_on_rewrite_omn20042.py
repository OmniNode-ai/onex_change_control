# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-20042: auto-merge.yml re-arms a PR whose head was rewritten.

A window companion force-pushed during the ``opened`` run's 900s eligibility
wait loses the arm for good: the wait sees the eligibility run cancelled, the
run fails, and no later ``pull_request`` event re-ran it (18 of 53 window
arming runs failed; OCC#11957 sat CLEAN and unarmed 48 minutes). The trigger
now lists ``synchronize`` and ``reopened`` too. These tests pin the trigger and
the properties that make it safe: per-PR concurrency with cancel-in-progress,
the 900s companion budget on ``pull_request`` events, and arming behind both
the allowed-author gate and the hold gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "auto-merge.yml"
)
_ARM_GATE = "steps.resolve.outputs.arm == 'true'"
_HOLD_GATE = "steps.hold_gate.outputs.hold != 'true'"


def _load() -> dict[Any, Any]:
    data = yaml.safe_load(_WORKFLOW.read_text())
    assert isinstance(data, dict)
    return data


def _steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _load()["jobs"]["auto-merge"]["steps"]
    return steps


def test_pull_request_trigger_lists_all_four_types() -> None:
    on = _load().get(True) or _load().get("on")
    assert isinstance(on, dict)
    assert set(on["pull_request"]["types"]) == {
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    }


def test_concurrency_is_per_pr_and_cancels_in_progress() -> None:
    concurrency = _load()["concurrency"]
    assert "github.event.pull_request.number" in str(concurrency["group"])
    assert concurrency["cancel-in-progress"] is True


def test_companion_budget_still_grants_900_on_pull_request_events() -> None:
    budgets = [
        str(v) for s in _steps() for v in s.get("env", {}).values() if "'900'" in str(v)
    ]
    assert len(budgets) == 1
    expr = budgets[0]
    assert "github.event_name == 'pull_request'" in expr
    assert "steps.resolve.outputs.companion == 'true'" in expr


def test_arming_stays_behind_arm_gate_and_hold_gate() -> None:
    steps = _steps()
    ids = [s.get("id") for s in steps]
    enable = [s for s in steps if s.get("name") == "Enable auto-merge"]
    assert len(enable) == 1
    cond = str(enable[0].get("if", ""))
    assert _ARM_GATE in cond
    assert _HOLD_GATE in cond
    assert ids.index("hold_gate") < steps.index(enable[0])
