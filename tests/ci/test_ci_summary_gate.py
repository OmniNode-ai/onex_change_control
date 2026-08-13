# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for scripts/ci/ci_summary_gate.py (OMN-15768 enforce-everything wave).

Behavioral core (ported from the omnibase_infra reference implementation,
tests/ci/test_ci_summary_gate.py) plus OCC-specific pins:

* completeness: every job reachable from a `pull_request` trigger against
  `dev`, across every workflow file, is classified into exactly one of
  STRICT / SKIPPABLE / SOFT_ALLOWLIST(self) / EXPECTED_EXTERNAL_CONTEXTS /
  EXEMPT_CONTEXTS -- a newly added, unclassified job fails this test.
* red-replay: a fixture shaped like the real OCC#6346 payload (Contract
  Compliance Check = failure, everything else success) must evaluate FAILURE
  under the new gate -- it evaluated SUCCESS under the old needs-based
  aggregator because `contract-compliance` was absent from its `needs:`.
* falsification control: deleting any one EXPECTED_EXTERNAL_CONTEXTS entry
  must flip a fixture from FAILURE to SUCCESS, proving each entry is
  load-bearing (not decorative).
* anti-regression pin for the whole OMN-15768 bug class: no workflow file
  under `.github/workflows/` may carry a `github.base_ref != 'dev'` clause
  outside the one reviewed, still-intentional exception (`pre-commit`'s
  ci:ready label gate).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from scripts.ci.ci_summary_gate import (
    EXEMPT_CONTEXTS,
    EXPECTED_EXTERNAL_CONTEXTS,
    EXTERNAL_GOOD_CONCLUSIONS,
    GOOD_CONCLUSIONS,
    SELF_JOB_NAME,
    SKIPPABLE_GATE_JOBS,
    SOFT_ALLOWLIST,
    STRICT_GATE_JOBS,
    dedup_latest,
    evaluate,
    evaluate_external_contexts,
    latest_check_run_by_name,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# ``uses:``-based reusable-workflow caller jobs in this repo carry no local
# `name:` field (the composed GitHub check-run name is `<caller job id> /
# <inner job/context name>`, resolved either by the reusable's own `name:`
# echoing that literal string, or -- for merge-hold-gate -- by the `with:
# context_name:` input the reusable is documented to consume). Mirrors
# required-checks.yaml's `job_path` Shape B/C documentation for the same jobs.
COMPOSED_NAME_OVERRIDES: dict[tuple[str, str], str] = {
    ("ci.yml", "merge-hold-gate"): "merge-hold-gate / evaluate",
    (
        "call-occ-autobind.yml",
        "occ-autobind",
    ): "occ-autobind / Publish occ-autobind command",
    (
        "call-occ-companion-effect.yml",
        "occ-companion-effect",
    ): "occ-companion-effect / Publish occ-companion-effect command",
    ("pr-title-check.yml", "pr-title"): "pr-title / check-title",
    ("docs-validate.yml", "call"): "call / validate-docs",
    (
        "required-check-skip-guard-caller.yml",
        "required-check-skip-guard",
    ): "required-check-skip-guard / check-skip-vectors",
}


def _iter_pr_triggered_jobs() -> list[tuple[str, str, str]]:
    """Yield ``(workflow_file, job_id, context_name)`` for every job reachable
    from a `pull_request` event targeting `dev`.

    A workflow file is in scope iff its `on:` block carries a `pull_request`
    key AND (it has no `branches:` filter, OR `dev` is a member of that
    filter). Every job dict in such a file is in scope -- there is no
    per-job `paths:`/`if:`-based exclusion at this layer; that is exactly
    what STRICT vs SKIPPABLE vs SOFT_ALLOWLIST vs EXEMPT_CONTEXTS classifies.
    """

    out: list[tuple[str, str, str]] = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        with path.open(encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        # YAML 1.1 parses the bare `on:` key as the boolean True.
        on = doc.get("on", doc.get(True))
        if not isinstance(on, dict) or "pull_request" not in on:
            continue
        pr_trigger = on["pull_request"]
        branches = pr_trigger.get("branches") if isinstance(pr_trigger, dict) else None
        if branches is not None and "dev" not in branches:
            continue
        for job_id, job in (doc.get("jobs") or {}).items():
            override = COMPOSED_NAME_OVERRIDES.get((path.name, job_id))
            context = override if override is not None else job.get("name", job_id)
            out.append((path.name, job_id, context))
    return out


def _classified_names() -> frozenset[str]:
    return (
        frozenset(STRICT_GATE_JOBS)
        | frozenset(SKIPPABLE_GATE_JOBS)
        | frozenset(SOFT_ALLOWLIST)
        | frozenset(EXPECTED_EXTERNAL_CONTEXTS)
        | frozenset(EXEMPT_CONTEXTS)
        | {SELF_JOB_NAME}
    )


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


def test_every_pr_triggered_job_is_classified() -> None:
    classified = _classified_names()
    unclassified = [
        (wf, job_id, ctx)
        for wf, job_id, ctx in _iter_pr_triggered_jobs()
        if ctx not in classified
    ]
    assert not unclassified, (
        "Job(s) reachable from a pull_request-to-dev trigger are not "
        "classified into STRICT_GATE_JOBS / SKIPPABLE_GATE_JOBS / "
        "SOFT_ALLOWLIST / EXPECTED_EXTERNAL_CONTEXTS / EXEMPT_CONTEXTS: "
        f"{unclassified}"
    )


def test_every_strict_and_skippable_name_resolves_to_a_live_ci_yml_job() -> None:
    """STRICT/SKIPPABLE names must match a real `name:` (or fallback id) in
    ci.yml -- catches a renamed job silently orphaning its gate entry."""

    live_names = {
        job.get("name", job_id): job_id
        for job_id, job in (
            yaml.safe_load((WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8"))[
                "jobs"
            ]
        ).items()
    }
    # merge-hold-gate has no local `name:` -- resolve via the override table.
    live_names[COMPOSED_NAME_OVERRIDES[("ci.yml", "merge-hold-gate")]] = (
        "merge-hold-gate"
    )

    for name in STRICT_GATE_JOBS + SKIPPABLE_GATE_JOBS:
        assert name in live_names, f"{name!r} does not match any live ci.yml job name"


def test_no_asserted_workflow_has_a_pull_request_paths_filter() -> None:
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        with path.open(encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        on = doc.get("on", doc.get(True))
        if not isinstance(on, dict):
            continue
        pr_trigger = on.get("pull_request")
        if not isinstance(pr_trigger, dict):
            continue
        msg = (
            f"{path.name} carries a pull_request paths filter -- convert to "
            "always-fires + in-job short-circuit before asserting any of "
            "its jobs (documented wedge trap)."
        )
        assert "paths" not in pr_trigger, msg
        assert "paths-ignore" not in pr_trigger, msg


def test_no_job_carries_a_base_ref_dev_exemption() -> None:
    """Anti-regression pin for the whole OMN-15768 bug class.

    Regex-scans every workflow file for the `base_ref != 'dev'` skip-vector
    shape. The ONLY reviewed, still-intentional survivor is `pre-commit`'s
    ci:ready label gate in ci.yml (which is deliberately STRICT, not
    SKIPPABLE -- an unlabeled dev PR is meant to fail, not silently pass).
    """

    pattern = re.compile(r"base_ref\s*!=\s*['\"]dev['\"]")
    allowed = {
        # ci.yml lines 60 and 84: pre-commit's own `if:` (job-level) and its
        # "Run full pre-commit" step -- the OMN-15731 ci:ready label pilot.
        # Reviewed and intentional: see STRICT_GATE_JOBS's "Pre-commit" entry
        # comment in ci_summary_gate.py.
        (
            "ci.yml",
            "if: always() && (github.event_name != 'pull_request' || "
            "github.base_ref != 'dev' || contains(github.event.pull_request"
            ".labels.*.name, 'ci:ready'))",
        ),
        (
            "ci.yml",
            "if: needs.zone-filter.outputs.docs_only != 'true' && "
            "(github.event_name != 'pull_request' || github.base_ref != 'dev')",
        ),
    }
    violations: list[str] = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                # Explanatory prose (e.g. this PR's own "used to carry a
                # base_ref != 'dev' clause, now removed" comments) is not
                # live YAML and cannot skip anything.
                continue
            if pattern.search(line) and (path.name, stripped) not in allowed:
                violations.append(f"{path.name}:{lineno}: {stripped}")
    assert not violations, (
        "Unreviewed `base_ref != 'dev'` skip-vector clause(s) found -- this "
        "is the exact OMN-15768 bug class (silent skip on every dev PR while "
        "reading as passing). Either remove the clause or add it to the "
        "`allowed` set above with a reviewed reason:\n" + "\n".join(violations)
    )


def test_every_exempt_context_has_a_nonempty_reason() -> None:
    for name, reason in EXEMPT_CONTEXTS.items():
        assert isinstance(reason, str), (
            f"EXEMPT_CONTEXTS[{name!r}] reason must be a string"
        )
        assert len(reason.strip()) > 20, (
            f"EXEMPT_CONTEXTS[{name!r}] must carry a real, non-trivial reason"
        )


def test_strict_skippable_external_and_exempt_are_disjoint() -> None:
    sets = {
        "STRICT_GATE_JOBS": frozenset(STRICT_GATE_JOBS),
        "SKIPPABLE_GATE_JOBS": frozenset(SKIPPABLE_GATE_JOBS),
        "EXPECTED_EXTERNAL_CONTEXTS": frozenset(EXPECTED_EXTERNAL_CONTEXTS),
        "EXEMPT_CONTEXTS": frozenset(EXEMPT_CONTEXTS),
    }
    names = list(sets)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            overlap = sets[a] & sets[b]
            assert not overlap, f"{a} and {b} overlap: {overlap}"


# ---------------------------------------------------------------------------
# Behavioral core
# ---------------------------------------------------------------------------


def _job(
    name: str, conclusion: str | None, status: str = "completed", attempt: int = 1
) -> dict[str, object]:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "run_attempt": attempt,
    }


def _all_green_jobs() -> list[dict[str, object]]:
    jobs = [_job(n, "success") for n in STRICT_GATE_JOBS]
    jobs += [_job(n, "success") for n in SKIPPABLE_GATE_JOBS]
    return jobs


def _all_green_check_runs() -> list[dict[str, object]]:
    return [
        {
            "name": n,
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-13T00:00:00Z",
            "id": i,
        }
        for i, n in enumerate(EXPECTED_EXTERNAL_CONTEXTS)
    ]


def test_strict_success_only_all_green_passes() -> None:
    code, _ = evaluate(
        _all_green_jobs(),
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 0


def test_strict_gate_skipped_fails_closed() -> None:
    victim = STRICT_GATE_JOBS[0]
    jobs = _all_green_jobs()
    for j in jobs:
        if j["name"] == victim:
            j["conclusion"] = "skipped"
    code, report = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 1
    assert victim in report


def test_strict_gate_cancelled_fails_closed() -> None:
    victim = STRICT_GATE_JOBS[0]
    jobs = _all_green_jobs()
    for j in jobs:
        if j["name"] == victim:
            j["conclusion"] = "cancelled"
    code, _ = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 1


def test_skippable_gate_skipped_passes() -> None:
    victim = SKIPPABLE_GATE_JOBS[0]
    jobs = _all_green_jobs()
    for j in jobs:
        if j["name"] == victim:
            j["conclusion"] = "skipped"
    code, _ = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 0


def test_skippable_gate_failure_fails_closed() -> None:
    victim = SKIPPABLE_GATE_JOBS[0]
    jobs = _all_green_jobs()
    for j in jobs:
        if j["name"] == victim:
            j["conclusion"] = "failure"
    code, _ = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 1


def test_missing_gate_is_pending_never_vacuous_success() -> None:
    jobs = [j for j in _all_green_jobs() if j["name"] != STRICT_GATE_JOBS[0]]
    code, _ = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 2


def test_empty_run_is_pending() -> None:
    code, _ = evaluate([], check_runs=[], external_contexts=EXPECTED_EXTERNAL_CONTEXTS)
    assert code == 2


def test_default_deny_sweep_catches_an_unclassified_failing_job() -> None:
    jobs = _all_green_jobs()
    jobs.append(_job("Some New Unclassified Validator", "failure"))
    code, report = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 1
    assert "Some New Unclassified Validator" in report


def test_default_deny_sweep_tolerates_allowlisted_job() -> None:
    jobs = _all_green_jobs()
    jobs.append(_job(next(iter(SOFT_ALLOWLIST)), "failure"))
    code, _ = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 0


def test_self_job_excluded_from_sweep() -> None:
    jobs = _all_green_jobs()
    jobs.append(_job(SELF_JOB_NAME, "failure"))
    code, _ = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 0


def test_latest_attempt_wins_over_stale_failure() -> None:
    victim = STRICT_GATE_JOBS[0]
    # Every other job stays at the default attempt=1 (all-green). The victim
    # carries a stale attempt-1 failure AND a later attempt-2 success; no
    # `run_attempt` filter is passed, so dedup_latest's "higher attempt always
    # overwrites" rule (regardless of list order) must resolve to the
    # attempt-2 success, not the stale failure.
    jobs = [j for j in _all_green_jobs() if j["name"] != victim]
    jobs.append(_job(victim, "failure", attempt=1))
    jobs.append(_job(victim, "success", attempt=2))
    code, _ = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 0


def test_duplicate_same_attempt_keeps_most_blocking_state() -> None:
    victim = STRICT_GATE_JOBS[0]
    jobs = [
        {
            "name": victim,
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 1,
        },
        {
            "name": victim,
            "status": "completed",
            "conclusion": "failure",
            "run_attempt": 1,
        },
    ]
    latest = dedup_latest(jobs)
    assert latest[victim].conclusion == "failure"


def test_external_context_absent_is_pending_never_success() -> None:
    failures, unresolved = evaluate_external_contexts([], EXPECTED_EXTERNAL_CONTEXTS)
    assert failures == []
    assert set(unresolved) == set(EXPECTED_EXTERNAL_CONTEXTS)


def test_external_context_skipped_fails_closed() -> None:
    victim = EXPECTED_EXTERNAL_CONTEXTS[0]
    runs = _all_green_check_runs()
    for r in runs:
        if r["name"] == victim:
            r["conclusion"] = "skipped"
    failures, unresolved = evaluate_external_contexts(runs, EXPECTED_EXTERNAL_CONTEXTS)
    assert victim in failures
    assert unresolved == []


def test_external_context_none_check_runs_is_every_context_unresolved() -> None:
    failures, unresolved = evaluate_external_contexts(None, EXPECTED_EXTERNAL_CONTEXTS)
    assert failures == []
    assert set(unresolved) == set(EXPECTED_EXTERNAL_CONTEXTS)


def test_latest_check_run_by_started_at_and_id() -> None:
    runs = [
        {
            "name": "X",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-08-13T00:00:00Z",
            "id": 1,
        },
        {
            "name": "X",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-13T00:05:00Z",
            "id": 2,
        },
    ]
    latest = latest_check_run_by_name(runs)
    assert latest["X"].conclusion == "success"


def test_good_conclusions_shape() -> None:
    assert {"success", "skipped"} == GOOD_CONCLUSIONS
    assert {"success"} == EXTERNAL_GOOD_CONCLUSIONS


# ---------------------------------------------------------------------------
# RED-REPLAY: OCC#6346 (merged 2026-08-11, merge commit 5399a37f)
# ---------------------------------------------------------------------------


def test_pr_6346_contract_compliance_red_now_fails() -> None:
    """Real shape: Contract Compliance Check = failure, every other in-run
    job green, on a real dev-targeting PR (not docs_only, not push).

    Under the OLD needs-based aggregator this evaluated CI Summary = SUCCESS
    because `contract-compliance` was absent from `needs:` entirely. Under
    this gate, `Contract Compliance Check` is in SKIPPABLE_GATE_JOBS (success
    or skipped only) so a `failure` conclusion fails closed.
    """

    jobs = _all_green_jobs()
    for j in jobs:
        if j["name"] == "Contract Compliance Check":
            j["conclusion"] = "failure"
    code, report = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 1
    assert "Contract Compliance Check" in report


# ---------------------------------------------------------------------------
# Falsification control -- every EXPECTED_EXTERNAL_CONTEXTS entry is load-bearing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dropped_context", EXPECTED_EXTERNAL_CONTEXTS)
def test_falsification_each_external_context_is_load_bearing(
    dropped_context: str,
) -> None:
    """Deleting one EXPECTED_EXTERNAL_CONTEXTS entry from the asserted set
    flips a fixture (that context red, everything else green) from FAILURE
    to SUCCESS -- proving the entry actually gates something."""

    runs = _all_green_check_runs()
    for r in runs:
        if r["name"] == dropped_context:
            r["conclusion"] = "failure"

    code_with, _ = evaluate(
        _all_green_jobs(), check_runs=runs, external_contexts=EXPECTED_EXTERNAL_CONTEXTS
    )
    assert code_with == 1, (
        f"{dropped_context!r} red did not fail the gate with it asserted"
    )

    narrowed = tuple(c for c in EXPECTED_EXTERNAL_CONTEXTS if c != dropped_context)
    code_without, _ = evaluate(
        _all_green_jobs(), check_runs=runs, external_contexts=narrowed
    )
    assert code_without == 0, (
        f"removing {dropped_context!r} from the asserted set did not flip the "
        "gate to SUCCESS -- it was not actually load-bearing"
    )
