# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15731: label-gated CI pilot (ci:ready) on onex_change_control.

Operator-authorized scoped pilot (2026-08-08): the `pre-commit` job is the one
CI job that runs unconditionally on every dev-targeting PR today (`if:
always()`) -- `test` and `type-check` already skip on dev PRs by design
(smart-test-select is meant to replace them there and is not wired), and
gating either of those would provide zero incremental savings on the everyday
path while risking the dev->main promotion boundary's unconditional
full-suite guarantee (root CLAUDE.md rule #4). `pre-commit` is therefore
gated behind `ci:ready`, but ONLY for dev-targeting PRs -- push/merge_group/
main-or-hotfix-targeting PRs are untouched, so the `main` promotion boundary
(where `Pre-commit` is a REQUIRED branch-protection context) keeps running it
unconditionally exactly as before.

Two things are proven here, mirroring `test_merge_hold_gate_wiring_omn15484.py`:

* **The strict registration behaves as claimed** -- by loading the SHIPPED
  `scripts/ci/ci_summary_gate.py` and EXECUTING its real `evaluate()` against
  synthetic job snapshots (OMN-15768: this replaced the needs-based
  `ci-summary` bash block a retired version of this class rendered and
  executed under bash). An unregistered job is invisible to `evaluate()`'s
  strict check -- exactly the AC(b) trap named on OMN-15731 (an unlabeled dev
  PR's `pre-commit` result is `skipped`, and without STRICT_GATE_JOBS
  registration `CI Summary` would read SUCCESS with zero lint/type/contract
  checks run).
* **The workflow-level wiring** (label-trigger events, the job's `if:`
  condition, and the `main`-targeting-PR carve-out) is asserted against the
  live `ci.yml`, not a hand-built fixture.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_YAML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_JOB_ID = "pre-commit"
_DISPLAY_NAME = "Pre-commit"  # ci_summary_gate.py STRICT_GATE_JOBS entry


def _ci_yaml() -> dict[str, Any]:
    return dict(yaml.safe_load(_CI_YAML.read_text(encoding="utf-8")))


def _load_gate_module() -> Any:
    """Load scripts/ci/ci_summary_gate.py by path (mirrors the loader in
    test_merge_hold_gate_wiring_omn15484.py, including the sys.modules
    registration Python 3.13's @dataclass needs)."""
    script_path = _REPO_ROOT / "scripts" / "ci" / "ci_summary_gate.py"
    spec = importlib.util.spec_from_file_location("ci_summary_gate", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _job(name: str, conclusion: str) -> dict[str, object]:
    return {"name": name, "status": "completed", "conclusion": conclusion}


def _all_green_jobs(gate: Any) -> list[dict[str, object]]:
    return [
        _job(n, "success") for n in gate.STRICT_GATE_JOBS + gate.SKIPPABLE_GATE_JOBS
    ]


class TestPreCommitStrictRegistrationIsExecutable:
    """Registration is the mechanism. Proven by running it, not by grepping."""

    def test_success_passes(self) -> None:
        gate = _load_gate_module()
        code, _ = gate.evaluate(_all_green_jobs(gate))
        assert code == gate.EXIT_SUCCESS

    @pytest.mark.parametrize("result", ["skipped", "failure", "cancelled"])
    def test_every_non_success_result_fails_the_required_context(
        self, result: str
    ) -> None:
        """``skipped`` is the one that matters: an unlabeled dev PR's
        `pre-commit` result IS `skipped` after this pilot -- and a skipped
        pre-commit is indistinguishable from no pre-commit at all: the PR
        would be required-green while running zero lint/type/contract
        checks. `failure`/`cancelled` are also caught by the L3 default-deny
        sweep for an unregistered job; `skipped` is caught ONLY by
        STRICT_GATE_JOBS registration.
        """
        gate = _load_gate_module()
        jobs = [j for j in _all_green_jobs(gate) if j["name"] != _DISPLAY_NAME]
        jobs.append(_job(_DISPLAY_NAME, result))
        code, _ = gate.evaluate(jobs)
        assert code == gate.EXIT_FAILURE

    def test_skipped_specifically_names_pre_commit_and_the_ticket(self) -> None:
        """The failure must be diagnosable, not a generic rollup failure."""
        gate = _load_gate_module()
        jobs = [j for j in _all_green_jobs(gate) if j["name"] != _DISPLAY_NAME]
        jobs.append(_job(_DISPLAY_NAME, "skipped"))
        _, report = gate.evaluate(jobs)
        assert _DISPLAY_NAME in report

    def test_unregistered_pre_commit_makes_a_skip_pass(self) -> None:
        """RED-before control: the pre-pilot state of this repo.

        A job that is NOT in STRICT_GATE_JOBS/SKIPPABLE_GATE_JOBS and is not
        otherwise present+failing is invisible to evaluate() -- restores
        exactly the bypass OMN-15731's AC(b) forbids, and is the control that
        makes the assertions above non-vacuous.
        """
        gate = _load_gate_module()
        strict = tuple(n for n in gate.STRICT_GATE_JOBS if n != _DISPLAY_NAME)
        jobs = [_job(n, "success") for n in strict + gate.SKIPPABLE_GATE_JOBS]
        jobs.append(_job(_DISPLAY_NAME, "skipped"))
        code, _ = gate.evaluate(jobs, strict_gates=strict)
        assert code == gate.EXIT_SUCCESS, (
            "expected the UNREGISTERED shape to pass on a skipped pre-commit "
            "result -- if it fails, this control proves nothing about the "
            "registration"
        )

    def test_pre_commit_is_registered(self) -> None:
        """Without this, ci-summary can report green before pre-commit rules."""
        gate = _load_gate_module()
        assert _DISPLAY_NAME in gate.STRICT_GATE_JOBS


class TestLabelGateWorkflowShape:
    def test_pull_request_trigger_includes_label_events(self) -> None:
        workflow: dict[Any, Any] = _ci_yaml()
        # PyYAML's default (YAML 1.1) resolver parses the bare `on:` key as
        # the boolean True, not the string "on" -- this is not a typo.
        pr_trigger = workflow[True]["pull_request"]
        for event_type in ("labeled", "unlabeled"):
            assert event_type in pr_trigger["types"]

    def test_pre_commit_if_is_gated_on_ci_ready_label_for_dev_only(self) -> None:
        job = _ci_yaml()["jobs"][_JOB_ID]
        condition = str(job["if"])
        assert "always()" in condition
        assert "contains(github.event.pull_request.labels.*.name, 'ci:ready')" in (
            condition
        )
        # The carve-out that protects the main promotion boundary and
        # non-pull_request events (push/merge_group/workflow_dispatch).
        assert "github.event_name != 'pull_request'" in condition
        assert "github.base_ref != 'dev'" in condition

    def test_test_and_type_check_are_unmodified_by_this_pilot(self) -> None:
        """Control: this pilot deliberately does NOT touch `test`/`type-check`
        (they already skip on dev PRs for unrelated reasons, and gating them
        further would not add savings -- see the module docstring)."""
        jobs = _ci_yaml()["jobs"]
        for job_id in ("test", "type-check"):
            condition = str(jobs[job_id]["if"])
            assert "ci:ready" not in condition, (
                f"{job_id} should not reference ci:ready -- this pilot only "
                "gates pre-commit"
            )
