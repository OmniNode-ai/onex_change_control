# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15768: 12 more `ci.yml` jobs must actually run on dev-targeting PRs.

Same class as OMN-15755 (`test`/`type-check`): each of these 12 jobs carried
an undocumented ``github.base_ref != 'dev'`` clause AND'd onto its
``docs_only`` guard, so every one silently skipped on essentially all real
OCC traffic (dev is this repo's default branch) while ``ci-summary``'s
generic rollup (``contains(needs.*.result, 'failure') || contains(
needs.*.result, 'cancelled')``) does not catch ``skipped`` — so CI Summary
read green with none of these checks having run.

9 of the 12 were already in ``ci-summary``'s ``needs:`` (guarded only by the
generic rollup); 3 (``ai-slop-check``, ``no-new-os-environ``,
``url-authority-gate``) were not in ``needs:`` at all — zero rollup coverage
of any kind. The fix: remove the `base_ref != 'dev'` clause from all 12,
add the 3 missing ones to `needs:`, and add an explicit strict success-only
check in `ci-summary` for all 12, mirroring the OMN-15755/OMN-15731 pattern.

Two things are proven here, and they are different in kind:

* **The strict registration behaves as claimed** — by RENDERING the real
  ``ci-summary`` shell block out of the shipped ``ci.yml`` and EXECUTING it
  under bash for each job, for each of its result states.
* **The anti-removal control fires**: stripping only the new strict block
  (leaving the jobs in `needs:`) restores exactly the bypass — a skipped
  job on a non-docs-only PR still reports "All required jobs passed".
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

_TARGET_JOBS = [
    "schema-purity",
    "no-localhost-fallbacks",
    "imperative-contract-guard",
    "context-integrity-contracts",
    "migration-conflicts",
    "migration-inventory",
    "kafka-boundary-parity",
    "cross-repo-null-contract",
    "seam-contract-coverage",
    "ai-slop-check",
    "no-new-os-environ",
    "url-authority-gate",
]

_NEEDS_RESULT_RE = re.compile(r"\$\{\{\s*needs\.([A-Za-z0-9_\-]+)\.result\s*\}\}")
_DOCS_ONLY_RE = re.compile(r"\$\{\{\s*needs\.zone-filter\.outputs\.docs_only\s*\}\}")
_CONTAINS_ROLLUP_RE = re.compile(
    r"\$\{\{\s*contains\(needs\.\*\.result,\s*'failure'\)\s*\|\|\s*"
    r"contains\(needs\.\*\.result,\s*'cancelled'\)\s*\}\}"
)
# The OMN-15768 strict block: an outer `if [[ docs_only != "true" ]]; then`
# wrapping one nested `if` per job, ending right before the generic rollup's
# own `if [[`. Matched non-greedily up to that anchor (rather than the first
# `fi`) for the same reason as OMN-15731's block: it is itself
# outer-if-wrapping-nested-ifs.
_STRICT_BLOCK_RE = re.compile(
    r"\n *# OMN-15768: strict fail-closed pattern.*?"
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
    job_id: str, job_result: str, docs_only: str = "false"
) -> subprocess.CompletedProcess[str]:
    """Execute the real ci-summary block with every other gate green."""
    results = dict.fromkeys(_all_needs(), "success")
    results[job_id] = job_result
    rendered = _render(_summary_script(), results, docs_only)
    return subprocess.run(
        ["bash", "-c", rendered], capture_output=True, text=True, check=False
    )


class TestStrictRegistrationIsExecutable:
    """Registration is the mechanism. Proven by running it, not by grepping."""

    def test_all_success_passes(self) -> None:
        results = dict.fromkeys(_all_needs(), "success")
        rendered = _render(_summary_script(), results, docs_only="false")
        completed = subprocess.run(
            ["bash", "-c", rendered], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "All required jobs passed" in completed.stdout

    @pytest.mark.parametrize("job_id", _TARGET_JOBS)
    @pytest.mark.parametrize("result", ["skipped", "failure", "cancelled"])
    def test_job_non_success_fails_closed_off_docs_only(
        self, job_id: str, result: str
    ) -> None:
        """``skipped`` is the one that matters: it is what OMN-15768 found live.

        ``failure``/``cancelled`` are already caught by the generic rollup for
        the 9 jobs that were already in `needs:`; ``skipped`` is caught ONLY
        by the explicit strict block added here. For the 3 jobs that were not
        in `needs:` at all, ALL three results matter equally — none were
        caught by anything before this fix.
        """
        completed = _run_summary(job_id, result, docs_only="false")
        assert completed.returncode == 1, completed.stdout + completed.stderr
        assert "OMN-15768" in completed.stdout
        assert job_id in completed.stdout

    def test_skip_on_docs_only_true_is_the_legitimate_fast_lane(self) -> None:
        """The OMN-14098 evidence-only fast lane must still pass CI Summary."""
        results = dict.fromkeys(_TARGET_JOBS, "skipped")
        for need in _all_needs():
            results.setdefault(need, "success")
        rendered = _render(_summary_script(), results, docs_only="true")
        completed = subprocess.run(
            ["bash", "-c", rendered], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "All required jobs passed" in completed.stdout

    def test_removing_the_strict_block_restores_the_bypass(self) -> None:
        """RED-before, against the real file: the pre-fix state of this repo.

        Stripping only the new strict block (leaving the 12 jobs in
        `needs:`) means a skip on a non-docs-only PR is reported as passing
        by the generic rollup alone -- this is the control that makes the
        assertions above non-vacuous.
        """
        script = _summary_script()
        stripped = _STRICT_BLOCK_RE.sub("\n", script)
        assert stripped != script, "the OMN-15768 strict block was not found to strip"

        results = dict.fromkeys(_all_needs(), "success")
        for job in _TARGET_JOBS:
            results[job] = "skipped"
        rendered = _render(stripped, results, docs_only="false")
        completed = subprocess.run(
            ["bash", "-c", rendered], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, (
            "expected the UNREGISTERED shape to pass on 12 skipped jobs "
            "outside the docs-only lane -- if it fails, this control proves "
            "nothing about the registration"
        )

    def test_all_12_jobs_are_in_ci_summary_needs(self) -> None:
        needs = _all_needs()
        missing = [job for job in _TARGET_JOBS if job not in needs]
        assert not missing, f"jobs missing from ci-summary needs: {missing}"


class TestJobsHaveNoDevBaseRefCarveOut:
    """AC: the `github.base_ref != 'dev'` skip vector is gone from all 12."""

    @pytest.mark.parametrize("job_id", _TARGET_JOBS)
    def test_job_if_condition_does_not_special_case_dev(self, job_id: str) -> None:
        job = _ci_yaml()["jobs"][job_id]
        condition = job.get("if", "")
        assert "base_ref" not in condition, (
            f"{job_id}'s if: still special-cases a base branch: {condition!r} "
            "-- this is exactly the dev-PR skip vector OMN-15768 closes"
        )
