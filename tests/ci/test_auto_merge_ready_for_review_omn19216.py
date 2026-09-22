# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-19216: auto-merge.yml arms on ``ready_for_review``, safely.

A PR opened as a draft was never armed by its ready flip, because the
pull_request trigger listed ``opened`` only. The trigger now lists
``ready_for_review`` too. These tests pin the three properties that make that
safe: arming stays behind the allowed-author gate, the author is read from the
PR rather than from whoever flipped it, and an already-armed PR is not armed a
second time.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "auto-merge.yml"
)
_GATE = re.compile(r"steps\.resolve\.outputs\.arm == 'true'")


def _load() -> dict[Any, Any]:
    data = yaml.safe_load(_WORKFLOW.read_text())
    assert isinstance(data, dict)
    return data


def _steps() -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = _load()["jobs"]["auto-merge"]["steps"]
    return steps


def _enable_step() -> tuple[int, dict[str, Any]]:
    for i, step in enumerate(_steps()):
        if step.get("name") == "Enable auto-merge":
            return i, step
    pytest.fail("no 'Enable auto-merge' step")


def test_pull_request_trigger_includes_ready_for_review() -> None:
    on = _load().get(True) or _load().get("on")
    assert isinstance(on, dict)
    types = on["pull_request"]["types"]
    assert "opened" in types
    assert "ready_for_review" in types


def test_arming_stays_behind_the_allowed_author_gate() -> None:
    _, step = _enable_step()
    assert _GATE.search(str(step.get("if", "")))


def test_author_is_read_from_the_pr_not_the_sender() -> None:
    resolve = next(s for s in _steps() if s.get("id") == "resolve")
    env = resolve.get("env", {})
    assert (
        env.get("PR_AUTHOR_FROM_PAYLOAD")
        == "${{ github.event.pull_request.user.login }}"
    )
    assert "github.event.sender" not in str(resolve)


def test_hold_gate_precedes_arming_when_present() -> None:
    ids = [s.get("id") for s in _steps()]
    idx, step = _enable_step()
    assert ids.index("hold_gate") < idx
    assert "steps.hold_gate.outputs.hold != 'true'" in str(step.get("if", ""))


def test_already_armed_pr_is_not_armed_again() -> None:
    _, step = _enable_step()
    run = str(step["run"])
    guard = run.find(".autoMergeRequest != null")
    arm = run.find("gh pr merge")
    assert guard != -1, "Enable step must read autoMergeRequest first"
    assert guard < arm, "the already-armed read must precede the arming call"
    assert "exit 0" in run[guard:arm]


def test_one_arming_run_per_pr() -> None:
    conc = _load().get("concurrency")
    assert isinstance(conc, dict)
    assert "github.event.pull_request.number" in str(conc["group"])
