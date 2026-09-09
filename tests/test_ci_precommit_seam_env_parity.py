# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Every pre-commit invocation in CI carries the seam hook's base. OMN-18056

`check-seam-contract-coverage` selects on
``^src/onex_change_control/(kafka|models/model_|enums/enum_interface)`` and
refuses to guess a comparison base. On a CI checkout HEAD is detached, so
without ``ONEX_SEAM_CONTRACT_BASE`` / ``_HEAD_REF`` / ``_TICKET_ID`` it exits 2
with "HEAD is detached, so a local hook cannot infer the intended comparison
target".

Measured on job 102373700095: step 4 (`Run full pre-commit`, which sets the
three) SUCCEEDED and step 5 (`Run dev PR changed-file pre-commit`, which did
not) FAILED — same commit, same hook, same repository state. The only
difference was the environment. Every dev PR touching a ``model_*.py`` therefore
failed the required `Pre-commit` job on a missing input rather than on a
seam-coverage finding, and it looked like a real gate failure.

The parity is asserted here rather than left to review, because the two steps
sit ~20 lines apart in one file and drifted anyway.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

_REQUIRED_ENV = (
    "ONEX_SEAM_CONTRACT_BASE",
    "ONEX_SEAM_CONTRACT_HEAD_REF",
    "ONEX_SEAM_CONTRACT_TICKET_ID",
)

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _precommit_steps() -> list[dict[str, Any]]:
    """Every step in ci.yml whose `run:` invokes `pre-commit run`."""
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = []
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            run = step.get("run")
            if isinstance(run, str) and "pre-commit run" in run:
                steps.append(step)
    return steps


def test_the_probe_finds_the_steps_at_all() -> None:
    """Positive control: an empty step list would make every assertion vacuous."""
    steps = _precommit_steps()
    assert len(steps) >= 3, f"expected the pre-commit job's steps, found {len(steps)}"


@pytest.mark.parametrize("variable", _REQUIRED_ENV)
def test_every_precommit_step_supplies_the_seam_base(variable: str) -> None:
    missing = [
        step.get("name", "<unnamed>")
        for step in _precommit_steps()
        # A hook-id-scoped invocation (`pre-commit run <id> --all-files`) runs
        # exactly one named hook and cannot select the seam hook.
        if "pre-commit run --" in step["run"]
        and variable not in (step.get("env") or {})
    ]
    assert not missing, (
        f"{variable} is not set on: {missing}. The seam hook selects on "
        "src/onex_change_control/models/model_*, and without this the step "
        "fails on a detached HEAD rather than on a finding."
    )
