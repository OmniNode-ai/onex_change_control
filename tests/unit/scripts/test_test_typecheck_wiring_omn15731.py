# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15731: `test` and `type-check` must actually run on dev-targeting PRs.

`smart-test-select` computed a changed-file-based test selection
(``scripts/ci/detect_test_paths.py``) but its outputs
(``selected_paths``/``split_count``/``matrix``/``is_full_suite``) were never
consumed by any job in ``ci.yml`` — no job carried
``needs: smart-test-select``. Independently, the ``test`` and ``type-check``
jobs each carried a ``github.base_ref != 'dev'`` clause on their own ``if:``
(added by OMN-14098 / PR #3665, 2026-07-07), so both were unconditionally
skipped on every PR targeting ``dev``. Because ``dev`` is this repo's default
branch, that meant essentially all OCC traffic merged with zero unit tests
and zero type-checking, and ``ci-summary``'s generic rollup
(``contains(needs.*.result, 'failure') || contains(needs.*.result,
'cancelled')``) does not catch a `skipped` result, so both jobs read as
passing.

The fix removes the `base_ref != 'dev'` carve-out (both jobs now run on
every non-docs-only PR, mirroring the pre-OMN-14098 shape) and deletes the
orphaned ``smart-test-select`` job rather than wiring it — OCC's suite is
small enough that full-suite-on-dev matches the CLAUDE.md rule-4 fail-closed
default. It also adds an explicit strict success-only check in
``ci-summary`` mirroring the OMN-15484 pattern, since ``test``/``type-check``
being LEGITIMATELY skipped on docs-only PRs means a bare "must be success"
check would break that fast lane — the check here must distinguish the two
skip reasons.

Two things are proven here, and they are different in kind:

* **The strict registration behaves as claimed** — by RENDERING the real
  ``ci-summary`` shell block out of the shipped ``ci.yml`` and EXECUTING it
  under bash for each combination of job result and docs_only value.
* **The anti-removal control fires**: stripping only the new strict block
  (leaving `test`/`type-check` in `needs:`) restores exactly the bypass —
  a skipped `test`/`type-check` on a non-docs-only PR still reports
  "All required jobs passed".
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_YAML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_SUMMARY_JOB_ID = "ci-summary"

_NEEDS_RESULT_RE = re.compile(r"\$\{\{\s*needs\.([A-Za-z0-9_\-]+)\.result\s*\}\}")
_DOCS_ONLY_RE = re.compile(r"\$\{\{\s*needs\.zone-filter\.outputs\.docs_only\s*\}\}")
_CONTAINS_ROLLUP_RE = re.compile(
    r"\$\{\{\s*contains\(needs\.\*\.result,\s*'failure'\)\s*\|\|\s*"
    r"contains\(needs\.\*\.result,\s*'cancelled'\)\s*\}\}"
)
# The shipped strict block for test+type-check, matched so the removal
# control below can strip exactly it and nothing else. Non-greedy up to the
# generic rollup's own `if [[` (rather than up to the first `fi`) because the
# block is itself an outer `if` wrapping two nested `if`s -- a naive
# `.*?\n *fi\n` would stop at the first nested `fi`, not the outer one.
_STRICT_BLOCK_RE = re.compile(
    r"\n *if \[\[ \"\$\{\{ needs\.zone-filter\.outputs\.docs_only \}\}\" "
    r"!= \"true\" \]\]; then"
    r".*?\n *fi\n(?=\s*if \[\[ \"\$\{\{ contains\(needs\.\*\.result)",
    re.DOTALL,
)


def _ci_yaml() -> dict[str, Any]:
    return dict(yaml.safe_load(_CI_YAML.read_text(encoding="utf-8")))


def _summary_script() -> str:
    summary = _ci_yaml()["jobs"][_SUMMARY_JOB_ID]
    return "\n".join(
        step["run"] for step in summary["steps"] if isinstance(step.get("run"), str)
    )


def _all_needs() -> list[str]:
    needs = _ci_yaml()["jobs"][_SUMMARY_JOB_ID]["needs"]
    return [needs] if isinstance(needs, str) else list(needs)


def _render(script: str, results: dict[str, str], docs_only: str) -> str:
    """Reproduce GitHub's `${{ ... }}` substitution, then hand off to bash."""
    rollup = "true" if {"failure", "cancelled"} & set(results.values()) else "false"
    script = _CONTAINS_ROLLUP_RE.sub(rollup, script)
    script = _DOCS_ONLY_RE.sub(docs_only, script)
    return _NEEDS_RESULT_RE.sub(lambda m: results.get(m.group(1), ""), script)


def _run_summary(
    test_result: str, type_check_result: str, docs_only: str = "false"
) -> subprocess.CompletedProcess[str]:
    """Execute the real ci-summary block with every other gate green."""
    results = dict.fromkeys(_all_needs(), "success")
    results["test"] = test_result
    results["type-check"] = type_check_result
    rendered = _render(_summary_script(), results, docs_only)
    return subprocess.run(
        ["bash", "-c", rendered], capture_output=True, text=True, check=False
    )


class TestStrictRegistrationIsExecutable:
    """Registration is the mechanism. Proven by running it, not by grepping."""

    def test_both_success_passes(self) -> None:
        completed = _run_summary("success", "success")
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "All required jobs passed" in completed.stdout

    @pytest.mark.parametrize("result", ["skipped", "failure", "cancelled"])
    def test_test_job_non_success_fails_closed_off_docs_only(self, result: str) -> None:
        """``skipped`` is the one that matters: it is what OMN-15731 found live.

        ``failure``/``cancelled`` are already caught by the generic rollup;
        ``skipped`` is caught ONLY by the explicit strict block added here.
        """
        completed = _run_summary(result, "success", docs_only="false")
        assert completed.returncode == 1, completed.stdout + completed.stderr
        assert "OMN-15731" in completed.stdout

    @pytest.mark.parametrize("result", ["skipped", "failure", "cancelled"])
    def test_type_check_job_non_success_fails_closed_off_docs_only(
        self, result: str
    ) -> None:
        completed = _run_summary("success", result, docs_only="false")
        assert completed.returncode == 1, completed.stdout + completed.stderr
        assert "OMN-15731" in completed.stdout

    def test_skip_on_docs_only_true_is_the_legitimate_fast_lane(self) -> None:
        """The OMN-14098 evidence-only fast lane must still pass CI Summary."""
        completed = _run_summary("skipped", "skipped", docs_only="true")
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "All required jobs passed" in completed.stdout

    def test_removing_the_strict_block_restores_the_bypass(self) -> None:
        """RED-before, against the real file: the pre-fix state of this repo.

        Stripping only the new strict block (leaving `test`/`type-check` in
        `needs:`) means a skip on a non-docs-only PR is reported as passing
        by the generic rollup alone -- this is the control that makes the
        assertions above non-vacuous.
        """
        script = _summary_script()
        stripped = _STRICT_BLOCK_RE.sub("\n", script)
        assert stripped != script, "the strict block was not found to strip"

        results = dict.fromkeys(_all_needs(), "success")
        results["test"] = "skipped"
        results["type-check"] = "skipped"
        rendered = _render(stripped, results, docs_only="false")
        completed = subprocess.run(
            ["bash", "-c", rendered], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, (
            "expected the UNREGISTERED shape to pass on skipped test/type-check "
            "outside the docs-only lane -- if it fails, this control proves "
            "nothing about the registration"
        )

    def test_test_and_type_check_are_in_ci_summary_needs(self) -> None:
        assert "test" in _all_needs()
        assert "type-check" in _all_needs()


class TestJobsHaveNoDevBaseRefCarveOut:
    """AC: the `github.base_ref != 'dev'` skip vector is gone from both jobs."""

    @pytest.mark.parametrize("job_id", ["test", "type-check"])
    def test_job_if_condition_does_not_special_case_dev(self, job_id: str) -> None:
        job = _ci_yaml()["jobs"][job_id]
        condition = job.get("if", "")
        assert "base_ref" not in condition, (
            f"{job_id}'s if: still special-cases a base branch: {condition!r} "
            "-- this is exactly the dev-PR skip vector OMN-15731 closes"
        )
        assert condition == "needs.zone-filter.outputs.docs_only != 'true'"

    def test_smart_test_select_job_no_longer_exists(self) -> None:
        """Never wired to `test`; a decoy job is worse than no job."""
        assert "smart-test-select" not in _ci_yaml()["jobs"]
