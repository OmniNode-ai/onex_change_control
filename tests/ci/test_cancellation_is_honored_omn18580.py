# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Cancellation ratchet: a superseded run must not hold its concurrency group.

OMN-18580. Every workflow in this repository declares ``cancel-in-progress:
true``, so a newer run supersedes an older one. GitHub honours that by
cancelling the older run -- but a job whose ``if:`` evaluates ``always()`` is
documented to run *even when the workflow is cancelled*. Such a job keeps the
superseded run in ``in_progress``, and GitHub will not start the successor
while the group is held: the successor sits at ``status=pending`` with **zero
jobs created** for exactly as long as the surviving job takes.

Measured on three OCC autobind companion PRs on 2026-09-17, all three with the
same shape -- the successor's first job starts three to four seconds after the
superseded run's ``Pre-commit`` job finishes:

* ``OCC#10006``: superseded ``Pre-commit`` 13:40:20Z to 13:49:27Z; successor
  run 35228547486 starts its first job at 13:49:31Z.
* ``OCC#10038``: superseded ``Pre-commit`` 16:20:09Z to 16:34:26Z; successor
  run 35245940491 starts its first job at 16:34:30Z.
* ``OCC#10039``: superseded ``Pre-commit`` 16:23:14Z to 16:39:24Z; successor
  run 35246259384 starts its first job at 16:39:28Z.

Every other job on each superseded run was cancelled within two seconds, so
the surviving ``always()`` job is the entire delay. The org fleet reported
nine idle runners of thirty during the ``#10038`` window, so capacity was
never involved.

``!cancelled()`` is the remedy and it is behaviour-preserving everywhere it
matters: for any run that is *not* cancelled the two expressions are
identical, so a job that ran before still runs, with the same result, on
success, failure and skip of everything it ``needs``.

**The limit that shapes the rest of this file.** A job whose ``if:`` goes
false reports ``skipped``, and GitHub branch protection counts a skipped
REQUIRED check as PASSING -- whereas a cancelled job reports ``cancelled``
and blocks. So applying the remedy to a required context would trade a stall
for a silent-pass bypass, which is the worse defect. Two consequences, both
pinned below:

* ``ci-summary`` keeps ``always()``. It is the required context on both
  branches, and it is not the stall anyway: it concluded in 17s, 18s and 17s
  on the three superseded runs above.
* ``pre-commit``'s guard is scoped to the dev arm, because
  ``.github/required-checks.yaml`` records ``Pre-commit`` as required on
  ``main``. On dev it cannot silent-pass either -- ``CI Summary`` carries it
  in ``STRICT_GATE_JOBS`` and fails closed on a ``skipped`` conclusion.

Scope is deliberately **job-level** ``if:`` only. A step-level ``always()``
inside a job that is being cancelled is the ordinary cleanup idiom and is
bounded by the cancellation grace period; it cannot hold a run open for the
full duration of a fresh job the way a job-level one can.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
_REQUIRED_CHECKS = _REPO_ROOT / ".github" / "required-checks.yaml"

# Jobs allowed to keep a job-level `always()`, each with the reason. A job
# earns a place here only by being a REQUIRED context, which
# test_every_always_exemption_is_a_required_context proves from the manifest
# rather than taking on trust.
_ALWAYS_EXEMPT: dict[tuple[str, str], str] = {
    (
        "ci.yml",
        "ci-summary",
    ): "required on both branches; a skipped required check counts as passing",
}

# Jobs rewritten to `!cancelled()`. Pinned by name so the fix cannot be
# "delete the condition": each must still run regardless of what its `needs:`
# reported, and losing that would silently drop a gate.
_MUST_RUN_REGARDLESS: dict[str, tuple[str, ...]] = {
    "ci.yml": ("pre-commit", "schema-purity"),
    "product-readiness-shadow.yml": ("reason-graph",),
}


def _workflow_files() -> list[Path]:
    files = sorted(_WORKFLOWS.glob("*.yml")) + sorted(_WORKFLOWS.glob("*.yaml"))
    assert files, f"no workflow files found under {_WORKFLOWS}"
    return files


def _load(path: Path) -> dict[Any, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path.name} did not parse as a mapping"
    return loaded


def _jobs(workflow: dict[Any, Any]) -> dict[str, dict[str, Any]]:
    jobs = workflow.get("jobs") or {}
    return {k: v for k, v in jobs.items() if isinstance(v, dict)}


def _required_job_paths() -> set[tuple[str, str]]:
    """(workflow filename, job id) for every REQUIRED gate in the manifest."""

    manifest = _load(_REQUIRED_CHECKS)
    pairs: set[tuple[str, str]] = set()
    for gate in manifest.get("gates") or []:
        if not isinstance(gate, dict) or gate.get("mode") != "REQUIRED":
            continue
        workflow = gate.get("workflow")
        job_path = gate.get("job_path")
        if not workflow or not isinstance(job_path, list) or not job_path:
            # Rows whose producer is a cross-repo reusable carry no local
            # workflow/job. They cannot be a local job-level `always()`.
            continue
        pairs.add((str(workflow), str(job_path[0])))
    assert pairs, "required-checks manifest yielded no locally-produced gates"
    return pairs


@pytest.mark.unit
def test_no_job_level_always_defeats_cancellation() -> None:
    """No job-level ``if:`` may use ``always()`` outside the exempt set."""

    offenders: list[str] = []
    for path in _workflow_files():
        for job_id, job in _jobs(_load(path)).items():
            condition = job.get("if")
            if condition is None:
                continue
            if "always()" not in str(condition):
                continue
            if (path.name, job_id) in _ALWAYS_EXEMPT:
                continue
            offenders.append(f"{path.name}::{job_id}")

    assert offenders == [], (
        "job-level always() survives workflow cancellation and holds the "
        "concurrency group open, so the superseded run blocks its own "
        "successor (OMN-18580). Use !cancelled() -- identical on every run "
        "that is not cancelled. Offenders: " + ", ".join(sorted(offenders))
    )


@pytest.mark.unit
def test_every_always_exemption_is_a_required_context() -> None:
    """Fail-closed direction: an exemption is earned, not asserted.

    The only defensible reason to keep ``always()`` is that the job produces
    a REQUIRED context, where skipping would read as passing. This resolves
    that from ``.github/required-checks.yaml`` so the exempt set cannot grow
    by someone adding a name to it.
    """

    required = _required_job_paths()
    unearned = sorted(
        f"{filename}::{job_id}"
        for (filename, job_id) in _ALWAYS_EXEMPT
        if (filename, job_id) not in required
    )
    assert unearned == [], (
        "these jobs keep always() but produce no REQUIRED context in "
        ".github/required-checks.yaml, so they have no reason to survive "
        "their own cancellation: " + ", ".join(unearned)
    )


@pytest.mark.unit
def test_the_jobs_that_must_run_regardless_still_do() -> None:
    """Positive control: the condition was rewritten, not deleted."""

    for filename, job_ids in _MUST_RUN_REGARDLESS.items():
        workflow = _load(_WORKFLOWS / filename)
        jobs = _jobs(workflow)
        for job_id in job_ids:
            assert job_id in jobs, f"{filename}: job {job_id} disappeared"
            condition = str(jobs[job_id].get("if") or "")
            assert "!cancelled()" in condition, (
                f"{filename}::{job_id} must keep running regardless of what "
                f"its needs reported; it carries if: {condition!r}"
            )


@pytest.mark.unit
def test_pre_commit_keeps_its_main_arm_outside_the_cancellation_guard() -> None:
    """``Pre-commit`` is REQUIRED on ``main``; its guard must not reach there.

    The non-dev arm has to sit OUTSIDE the ``!cancelled()`` term. Inside it,
    a cancelled run on a main-targeting PR would skip a required context,
    and GitHub would read that skip as a pass.
    """

    required = _required_job_paths()
    assert ("ci.yml", "pre-commit") in required, (
        "this test's premise moved: Pre-commit is no longer a REQUIRED gate "
        "in the manifest, so re-derive whether the dev scoping is still needed"
    )

    condition = " ".join(
        str(_jobs(_load(_WORKFLOWS / "ci.yml"))["pre-commit"]["if"]).split()
    )
    non_dev_arm = "(github.event_name != 'pull_request' || github.base_ref != 'dev')"
    assert non_dev_arm in condition, (
        f"pre-commit's non-dev arm must be its own top-level disjunct; "
        f"condition is {condition!r}"
    )
    assert condition.index(non_dev_arm) < condition.index("!cancelled()"), (
        "the non-dev arm must precede the cancellation guard as a separate "
        f"disjunct, not sit inside it; condition is {condition!r}"
    )
    # The OMN-15731 admission arms survive untouched.
    assert "!github.event.pull_request.draft" in condition
    assert "contains(github.event.pull_request.labels.*.name, 'ci:ready')" in condition


@pytest.mark.unit
def test_cancel_in_progress_groups_stay_pull_request_scoped() -> None:
    """A cancelling group must never key on a bare branch push ref.

    Cancelling a merge-triggered run on ``dev`` or ``main`` is a different
    defect from the one this file closes -- it would drop the CI evidence for
    a commit that has already landed. Every group that cancels must therefore
    be keyed on the pull-request number, or on ``github.ref``, which on a
    ``pull_request`` event is the per-PR ``refs/pull/<n>/merge``.
    """

    for path in _workflow_files():
        concurrency = _load(path).get("concurrency")
        if not isinstance(concurrency, dict):
            continue
        if not concurrency.get("cancel-in-progress"):
            continue
        group = str(concurrency.get("group") or "")
        assert "github.event.pull_request.number" in group or "github.ref" in group, (
            f"{path.name}: cancelling concurrency group is not PR-scoped: {group!r}"
        )
