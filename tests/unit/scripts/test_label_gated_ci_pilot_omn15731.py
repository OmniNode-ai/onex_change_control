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

* **The strict registration behaves as claimed** -- by RENDERING the real
  `ci-summary` shell block out of the shipped `ci.yml` and EXECUTING it under
  bash for each possible `pre-commit` result. OCC's `ci-summary` generic
  rollup is `contains(needs.*.result, 'failure') || contains(needs.*.result,
  'cancelled')`, which passes on a SKIPPED need -- exactly the AC(b) trap
  named on OMN-15731 (an unlabeled dev PR's `pre-commit` result is
  `skipped`, and without the new strict block that reads `CI Summary` =
  SUCCESS with zero lint/type/contract checks run).
* **The workflow-level wiring** (label-trigger events, the job's `if:`
  condition, and the `main`-targeting-PR carve-out) is asserted against the
  live `ci.yml`, not a hand-built fixture.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_YAML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_JOB_ID = "pre-commit"
_SUMMARY_JOB_ID = "ci-summary"

_NEEDS_RESULT_RE = re.compile(r"\$\{\{\s*needs\.([A-Za-z0-9_\-]+)\.result\s*\}\}")
_CONTAINS_ROLLUP_RE = re.compile(
    r"\$\{\{\s*contains\(needs\.\*\.result,\s*'failure'\)\s*\|\|\s*"
    r"contains\(needs\.\*\.result,\s*'cancelled'\)\s*\}\}"
)


def _ci_yaml() -> dict[str, Any]:
    return dict(yaml.safe_load(_CI_YAML.read_text(encoding="utf-8")))


def _render_ci_summary_script(results: dict[str, str]) -> str:
    """Render the SHIPPED ci-summary step script with concrete job results.

    Same technique as ``test_merge_hold_gate_wiring_omn15484.py``: GitHub
    evaluates ``${{ ... }}`` before bash ever sees the script, so
    reproducing the real behaviour means substituting first and running
    the result.
    """
    summary = _ci_yaml()["jobs"][_SUMMARY_JOB_ID]
    script = "\n".join(
        step["run"] for step in summary["steps"] if isinstance(step.get("run"), str)
    )
    rollup = "true" if {"failure", "cancelled"} & set(results.values()) else "false"
    script = _CONTAINS_ROLLUP_RE.sub(rollup, script)
    return _NEEDS_RESULT_RE.sub(lambda m: results.get(m.group(1), ""), script)


def _all_needs() -> list[str]:
    needs = _ci_yaml()["jobs"][_SUMMARY_JOB_ID]["needs"]
    return [needs] if isinstance(needs, str) else list(needs)


def _run_summary(pre_commit_result: str) -> subprocess.CompletedProcess[str]:
    """Execute the real ci-summary block with every other gate green."""
    results = dict.fromkeys(_all_needs(), "success")
    results[_JOB_ID] = pre_commit_result
    return subprocess.run(
        ["bash", "-c", _render_ci_summary_script(results)],
        capture_output=True,
        text=True,
        check=False,
    )


class TestPreCommitStrictRegistrationIsExecutable:
    """Registration is the mechanism. Proven by running it, not by grepping."""

    def test_success_passes(self) -> None:
        completed = _run_summary("success")
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "All required jobs passed" in completed.stdout

    @pytest.mark.parametrize("result", ["skipped", "failure", "cancelled"])
    def test_every_non_success_result_fails_the_required_context(
        self, result: str
    ) -> None:
        """``skipped`` is the one that matters: an unlabeled dev PR's
        `pre-commit` result IS `skipped` after this pilot -- and a skipped
        pre-commit is indistinguishable from no pre-commit at all: the PR
        would be required-green while running zero lint/type/contract
        checks. `failure`/`cancelled` are already caught by the generic
        rollup; `skipped` is caught ONLY by the new explicit strict check.
        """
        completed = _run_summary(result)
        assert completed.returncode == 1, completed.stdout + completed.stderr

    def test_skipped_specifically_names_pre_commit_and_the_ticket(self) -> None:
        """The failure must be diagnosable, not a generic rollup failure."""
        completed = _run_summary("skipped")
        assert _JOB_ID in completed.stdout
        assert "OMN-15731" in completed.stdout

    def test_removing_the_strict_check_makes_a_skip_pass(self) -> None:
        """RED-before, against the real file: the pre-pilot state of this repo.

        Deleting only the explicit check -- leaving the job present and in
        ``needs:`` -- restores exactly the bypass OMN-15731's AC(b)
        forbids, and the generic rollup still reports success. This is the
        control that makes the assertions above non-vacuous.
        """
        summary = _ci_yaml()["jobs"][_SUMMARY_JOB_ID]
        script = "\n".join(
            step["run"] for step in summary["steps"] if isinstance(step.get("run"), str)
        )
        strict_block_re = re.compile(
            r"\n *if \[\[ \"\$\{\{ needs\." + _JOB_ID + r"\.result \}\}\".*?\n *fi",
            re.DOTALL,
        )
        stripped = strict_block_re.sub("", script)
        assert stripped != script, "the strict check was not found to strip"

        results = dict.fromkeys(_all_needs(), "success")
        results[_JOB_ID] = "skipped"
        rollup = "true" if {"failure", "cancelled"} & set(results.values()) else "false"
        rendered = _NEEDS_RESULT_RE.sub(
            lambda m: results.get(m.group(1), ""),
            _CONTAINS_ROLLUP_RE.sub(rollup, stripped),
        )
        completed = subprocess.run(
            ["bash", "-c", rendered], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, (
            "expected the UNREGISTERED shape to pass on a skipped pre-commit "
            "result -- if it fails, this control proves nothing about the "
            "registration"
        )

    def test_pre_commit_is_in_ci_summary_needs(self) -> None:
        """Without this, ci-summary can report green before pre-commit rules."""
        assert _JOB_ID in _all_needs()


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
