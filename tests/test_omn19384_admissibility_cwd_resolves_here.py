# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-19384 -- the admissibility item's declared cwd resolves in this repo's CI.

The autobind producer now mints the admissibility validator with
``cwd: "${OMNI_HOME}/onex_change_control"`` so that product repos can see it
belongs to another tree. The contract-compliance job in this repo must still run
it, so the job sets up a private OMNI_HOME that contains only this checkout.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from onex_change_control.scripts.contract_compliance_check import (
    _resolve_check_cwd,
)

if TYPE_CHECKING:
    import pytest

_DECLARED_CWD = "${OMNI_HOME}/onex_change_control"
_CI = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"


def test_the_declared_cwd_resolves_to_the_change_control_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    omni_home = tmp_path / "omni-home"
    omni_home.mkdir()
    (omni_home / "onex_change_control").symlink_to(checkout)
    monkeypatch.setenv("OMNI_HOME", str(omni_home))

    run_cwd, decline = _resolve_check_cwd(
        _DECLARED_CWD, checkout, 1, "OmniNode-ai/onex_change_control", "OMN-1"
    )

    assert decline is None
    assert run_cwd == checkout.resolve()


def test_without_omni_home_it_is_not_evaluated_never_rerouted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OMNI_HOME", raising=False)

    run_cwd, decline = _resolve_check_cwd(
        _DECLARED_CWD, tmp_path, 1, "OmniNode-ai/onex_change_control", "OMN-1"
    )

    assert run_cwd is None
    assert decline is not None
    assert decline.startswith("NOT-EVALUATED [cwd]")


def test_the_compliance_job_exports_an_omni_home_holding_this_tree() -> None:
    jobs = yaml.safe_load(_CI.read_text(encoding="utf-8"))["jobs"]
    scripts = [
        str(step.get("run", ""))
        for job in jobs.values()
        for step in job.get("steps", [])
        if "run_contract_compliance_check.py" in str(step.get("run", ""))
    ]
    assert scripts, "no step runs the contract compliance check"
    for job in jobs.values():
        steps = job.get("steps", [])
        if not any(
            "run_contract_compliance_check.py" in str(s.get("run", "")) for s in steps
        ):
            continue
        setup = "\n".join(str(s.get("run", "")) for s in steps)
        assert 'ln -s /tmp/onex_change_control "$omni_home/onex_change_control"' in (
            setup
        )
        assert 'echo "OMNI_HOME=$omni_home" >> "$GITHUB_ENV"' in setup
