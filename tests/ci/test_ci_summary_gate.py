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

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from scripts.ci.ci_summary_gate import (
    CLASSIFICATION_ONLY,
    EXEMPT_CONTEXTS,
    EXPECTED_EXTERNAL_CONTEXTS,
    EXTERNAL_GOOD_CONCLUSIONS,
    GOOD_CONCLUSIONS,
    SELF_JOB_NAME,
    SKIPPABLE_GATE_JOBS,
    SOFT_ALLOWLIST,
    STRICT_GATE_JOBS,
    _load_check_runs,
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
    # OMN-16260: these four jobs' original standalone files (call-occ-
    # autobind.yml, call-occ-companion-effect.yml, pr-title-check.yml,
    # required-check-skip-guard-caller.yml) were consolidated into
    # guards.yml -- same job ids, same `uses:` targets, same composed
    # check-run names, only the file moved.
    (
        "guards.yml",
        "occ-autobind",
    ): "occ-autobind / Publish occ-autobind command",
    (
        "guards.yml",
        "occ-companion-effect",
    ): "occ-companion-effect / Publish occ-companion-effect command",
    ("guards.yml", "pr-title"): "pr-title / check-title",
    ("docs-validate.yml", "call"): "call / validate-docs",
    (
        "guards.yml",
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
        | frozenset(CLASSIFICATION_ONLY)
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
    admission gate in ci.yml (which is deliberately STRICT, not SKIPPABLE --
    an unadmitted dev PR is meant to fail, not silently pass).
    """

    pattern = re.compile(r"base_ref\s*!=\s*['\"]dev['\"]")
    allowed = {
        # ci.yml: pre-commit's own `if:` (job-level) -- the OMN-15731
        # admission-gate pilot, revised 2026-08-18 to make draft state
        # (`!github.event.pull_request.draft`) the primary signal with
        # `ci:ready` retained as a transition-window fallback. Reviewed and
        # intentional: see STRICT_GATE_JOBS's "Pre-commit" entry comment in
        # ci_summary_gate.py and TestDraftStateGateMigrationOmn15731Revision
        # in test_label_gated_ci_pilot_omn15731.py.
        (
            "ci.yml",
            "if: always() && (github.event_name != 'pull_request' || "
            "github.base_ref != 'dev' || "
            "!github.event.pull_request.draft || "
            "contains(github.event.pull_request"
            ".labels.*.name, 'ci:ready'))",
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


@pytest.mark.parametrize("zone_name", ["zone-filter", "zone-filter / filter"])
def test_failed_zone_filter_cascade_is_not_a_vacuous_success(zone_name: str) -> None:
    """Regression pin for the PR #6435 verifier-reproduced fail-open.

    A FAILED zone-filter cascades every job that `needs:` it to `skipped`.
    All 12 docs_only-tier SKIPPABLE jobs (Type Check, Tests, ...) do, and the
    SKIPPABLE tier tolerates `skipped` unconditionally -- so with zone-filter
    in SOFT_ALLOWLIST the gate returned SUCCESS with zero tests and zero
    type-checks having run. Parametrized over both name shapes GitHub can
    surface for a reusable caller (bare id, and `<caller> / <inner job>`).
    """

    cascaded = frozenset(SKIPPABLE_GATE_JOBS[:12])
    assert {"Type Check", "Tests"} <= cascaded, (
        "docs_only tier reordered -- re-derive the cascaded slice"
    )
    jobs = [_job(n, "success") for n in STRICT_GATE_JOBS]
    jobs += [
        _job(n, "skipped" if n in cascaded else "success") for n in SKIPPABLE_GATE_JOBS
    ]
    jobs.append(_job(zone_name, "failure"))
    code, report = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 1, f"fail-open: zone-filter failure yielded exit {code}\n{report}"
    assert "zone-filter" in report


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


# ---------------------------------------------------------------------------
# OMN-16141: commits/{sha}/check-runs pagination -- a context past the first
# 100 check-runs must resolve as present/terminal, never missing/pending.
#
# Live repro: occ#6618 head d5ed3417204bfe73263805532cd3e38a0d343242 carries
# 135 check-runs (>100, i.e. 2 GitHub API pages at ?per_page=100). ci.yml's
# `ci-summary` job now fetches that endpoint with `gh api ... --paginate
# --slurp | jq '[.[].check_runs[]]'`, which merges every page's `check_runs`
# array into one flat JSON array file -- the shape built and fed through
# `_load_check_runs` below, unchanged from what the workflow step now writes.
# ---------------------------------------------------------------------------


def test_load_check_runs_flattened_multi_page_array_sees_later_page_context(
    tmp_path: Path,
) -> None:
    """A context that only appears past check-run #100 (i.e. only on the
    second GitHub API page) must be seen as present + success, not
    missing/pending, once the pages have been merged into one flat array."""

    page_1 = [
        {
            "name": f"filler-check-{i}",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-17T00:00:00Z",
            "id": i,
        }
        for i in range(100)
    ]
    # The real external contexts land at index >=100 -- exactly where
    # occ#6618's head placed them on GitHub's second page.
    page_2 = [
        {
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-17T00:05:00Z",
            "id": 100 + i,
        }
        for i, name in enumerate(EXPECTED_EXTERNAL_CONTEXTS)
    ]
    flattened = page_1 + page_2
    assert len(flattened) > 100  # sanity: this fixture actually spans 2 pages

    check_runs_file = tmp_path / "check_runs.json"
    check_runs_file.write_text(json.dumps(flattened), encoding="utf-8")

    loaded = _load_check_runs(str(check_runs_file))
    assert loaded is not None
    assert len(loaded) == len(flattened)

    failures, unresolved = evaluate_external_contexts(
        loaded, EXPECTED_EXTERNAL_CONTEXTS
    )
    assert failures == []
    assert unresolved == [], (
        "a context that only appears past the first 100 check-runs must "
        "resolve as present/success, not missing/pending (OMN-16141)"
    )


def test_unmerged_paginate_output_is_the_bug_this_pr_fixes(tmp_path: Path) -> None:
    """Pin for the pre-fix failure mode itself.

    Per `gh help api`: "In --paginate mode ... Each page is a separate JSON
    array or object. Pass --slurp to wrap all pages ... into an outer JSON
    array." `commits/{sha}/check-runs` returns an OBJECT
    (`{total_count, check_runs: [...]}`), so plain `--paginate` (no
    `--slurp`) on a >100-check-run head wrote two back-to-back JSON objects
    -- one per page -- into a single file. `_load_check_runs` cannot parse
    that (`json.loads` raises `JSONDecodeError: Extra data`), catches it, and
    returns `None` -- which `evaluate_external_contexts` treats as *every*
    expected external context being unobserved, forever. This is the exact
    occ#6618 symptom this PR's workflow fix (`--paginate --slurp | jq
    '[.[].check_runs[]]'`) eliminates; this test pins the failure mode so it
    cannot silently regress.
    """

    page_1_obj = json.dumps({"total_count": 135, "check_runs": _all_green_check_runs()})
    page_2_obj = json.dumps({"total_count": 135, "check_runs": _all_green_check_runs()})
    concatenated = page_1_obj + page_2_obj  # what un-slurped --paginate wrote

    check_runs_file = tmp_path / "check_runs_unmerged.json"
    check_runs_file.write_text(concatenated, encoding="utf-8")

    loaded = _load_check_runs(str(check_runs_file))
    assert loaded is None  # fail-closed: concatenated multi-page output is unparseable

    failures, unresolved = evaluate_external_contexts(
        loaded, EXPECTED_EXTERNAL_CONTEXTS
    )
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
# OMN-15487: the schema-purity SKIP-LAUNDERING cascade
# ---------------------------------------------------------------------------
# `schema-purity` (ci.yml) declares `needs: [zone-filter, test,
# contract-compliance]` guarded by `needs.contract-compliance.result ==
# 'success'`, so a FAILING contract-compliance does not merely fail on its
# own -- it cascades `Schema Purity & Naming Check` to `skipped`. Under the
# retired needs-based aggregator that skip was the laundering vector: the
# generic rollup read `skipped` as passing and `contract-compliance` itself
# was absent from `needs:`, so BOTH halves of the cascade were tolerated and
# CI Summary went green over a red contract gate.
#
# The poller closes this at the source (contract-compliance is evaluated
# directly, SKIPPABLE = success-or-skipped only), but nothing pinned the
# CASCADE SHAPE itself. test_pr_6346_contract_compliance_red_now_fails above
# holds every other job green -- including Schema Purity & Naming Check --
# which is not what a real run looks like. These two tests pin the real
# shape, so a future edit that moves "Contract Compliance Check" into
# SOFT_ALLOWLIST or CLASSIFICATION_ONLY (both of which tolerate a present
# failing job far more readily) cannot silently reopen the vector while the
# green-fixture test above keeps passing.


def test_contract_compliance_failure_cascading_schema_purity_skip_fails_closed() -> (
    None
):
    """The real OMN-15487 cascade: contract-compliance FAILS and its
    dependent schema-purity is consequently SKIPPED.

    Both halves must not be tolerated together. Under the retired aggregator
    this exact pair evaluated SUCCESS.
    """

    jobs = _all_green_jobs()
    seen_failure = seen_skip = False
    for j in jobs:
        if j["name"] == "Contract Compliance Check":
            j["conclusion"] = "failure"
            seen_failure = True
        elif j["name"] == "Schema Purity & Naming Check":
            j["conclusion"] = "skipped"
            seen_skip = True
    # Guard the fixture itself: if either job is ever renamed out from under
    # this test, fail here rather than vacuously asserting on a shape that no
    # longer contains the cascade.
    assert seen_failure, "fixture no longer contains 'Contract Compliance Check'"
    assert seen_skip, "fixture no longer contains 'Schema Purity & Naming Check'"

    code, report = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 1, "contract-compliance failure laundered into a tolerated skip"
    assert "Contract Compliance Check" in report


def test_schema_purity_skip_alone_is_not_what_makes_the_cascade_fail() -> None:
    """Falsification control for the test directly above.

    `Schema Purity & Naming Check` is legitimately SKIPPABLE (the docs_only
    fast lane really does skip it), so its skip alone must PASS. This proves
    the cascade test's failure verdict comes from the contract-compliance
    failure being read directly -- not from the skip -- which is precisely
    the property the retired aggregator lacked.
    """

    jobs = _all_green_jobs()
    for j in jobs:
        if j["name"] == "Schema Purity & Naming Check":
            j["conclusion"] = "skipped"
    code, _ = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 0


# ---------------------------------------------------------------------------
# OMN-15487 AC4: required-checks.yaml's CI Summary rationale must be TRUE
# ---------------------------------------------------------------------------


def test_ci_summary_rationale_describes_the_poller_not_a_needs_aggregator() -> None:
    """`.github/required-checks.yaml` is the enforcement-parity manifest --
    the surface an auditor reads to learn WHY a context is required and what
    it actually covers. Its `CI Summary` row justified REQUIRED status with
    "Fail-closed `if: always()` aggregator over ci.yml sub-jobs".

    That sentence was already false when written (OMN-15487: 7 of 33 ci.yml
    jobs were absent from `needs:` entirely) and is false again for the
    opposite reason now that OMN-16007 landed: `ci-summary` has NO `needs:`
    at all, and its coverage is no longer limited to `ci.yml` -- the L4 layer
    asserts cross-workflow contexts too. A rationale that misdescribes the
    mechanism in BOTH directions is worse than no rationale: it is what let
    the original gap survive review.

    Pin the rationale to the mechanism so it cannot drift back.
    """

    manifest = yaml.safe_load(
        (REPO_ROOT / ".github" / "required-checks.yaml").read_text(encoding="utf-8")
    )
    rows = [c for c in manifest["gates"] if c["name"] == "CI Summary"]
    assert len(rows) == 1, "expected exactly one 'CI Summary' row"
    rationale = rows[0]["rationale"]

    assert "ci_summary_gate.py" in rationale, (
        "CI Summary's rationale must name the poller script that actually "
        "produces the verdict"
    )
    # The retired mechanism's vocabulary must not reappear. `needs:` is the
    # specific claim OMN-15487 falsified, and the gate now treats a `needs:`
    # on ci-summary as a regression to the bug class in its own right.
    assert "aggregator" not in rationale.lower(), (
        "'aggregator' describes the retired needs-gated shape, not the poller"
    )
    assert "needs" not in rationale.lower(), (
        "ci-summary has no `needs:`; describing one re-asserts the OMN-15487 falsehood"
    )


def test_no_manifest_comment_claims_occ_lacks_a_ci_summary_poller() -> None:
    """The manifest's header block justified promoting `verify / verify` and
    `occ-preflight / eligibility` to direct required contexts with: "`CI
    Summary` cannot cover them: OCC has no `scripts/ci/ci_summary_gate.py`
    poller".

    OMN-16007 landed exactly that file, and both contexts are now asserted by
    its L4 layer. The promotion remains correct (belt-and-braces, and the
    gate says so itself), but the stated REASON is now false -- and a false
    reason in the parity manifest is what an auditor would rely on when
    deciding whether the promotion can be reverted.
    """

    text = (REPO_ROOT / ".github" / "required-checks.yaml").read_text(encoding="utf-8")
    assert (REPO_ROOT / "scripts" / "ci" / "ci_summary_gate.py").is_file(), (
        "this pin only makes sense while the poller exists"
    )
    assert "OCC has no" not in text, (
        "manifest still asserts OCC lacks a ci_summary_gate.py poller; "
        "the file exists on dev as of OMN-16007"
    )
    assert "plain\n# `needs:` aggregator" not in text.replace("\n#", "\n# ")


# ---------------------------------------------------------------------------
# Falsification control -- every EXPECTED_EXTERNAL_CONTEXTS entry is load-bearing
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# OMN-16285: evidence-only-predicate fast lane
# ---------------------------------------------------------------------------

# The exact 5 jobs OMN-16285 moved from STRICT_GATE_JOBS to
# SKIPPABLE_GATE_JOBS, gated in ci.yml on `evidence-only-predicate`'s output
# rather than `zone-filter`'s `docs_only`. Each scans a surface (src/,
# .github/workflows/, .pre-commit-config.yaml, or a grants/allowlists diff)
# the exact-allowlist predicate proves is unchanged on an evidence-only diff.
EVIDENCE_PREDICATE_TIER: frozenset[str] = frozenset(
    {
        "No Divergent Automation PRs (OMN-14778)",
        "no-noncanonical-lifecycle-classes",
        "Precommit Parity Gate",
        "Evidence Admissibility Predicate Parity",
        "check-bot-authored-authz-guard",
    }
)


def test_evidence_predicate_tier_is_a_subset_of_skippable_not_strict() -> None:
    """Anti-drift pin: if a future edit moves one of these jobs back to
    STRICT (or removes it from SKIPPABLE entirely) this must fail loudly
    rather than silently changing which jobs the predicate governs."""

    assert frozenset(SKIPPABLE_GATE_JOBS) >= EVIDENCE_PREDICATE_TIER
    assert EVIDENCE_PREDICATE_TIER.isdisjoint(STRICT_GATE_JOBS)


def test_evidence_only_predicate_job_is_classification_only_not_a_gate() -> None:
    """The predicate job itself is a structural classifier, not a validator
    -- it must never appear in STRICT/SKIPPABLE (a `skipped` predicate run
    that gated ITSELF would be a self-referential vacuous pass), and must be
    registered CLASSIFICATION_ONLY so the default-deny sweep, not a gate
    tier, is what catches its own failure."""

    name = "Evidence-Only Diff Predicate"
    assert name not in STRICT_GATE_JOBS
    assert name not in SKIPPABLE_GATE_JOBS
    assert name not in SOFT_ALLOWLIST
    assert name in CLASSIFICATION_ONLY


def test_evidence_predicate_tier_all_skipped_with_predicate_success_is_success() -> (
    None
):
    """The intended evidence-only-diff shape: the predicate job itself ran
    and succeeded (asserting evidence_only=true), every job gated on its
    output reports `skipped`, and every unconditional/content-validating
    gate still reports `success`. This must evaluate SUCCESS -- proving the
    slim path actually goes green, not just that the strict tier still
    fails closed."""

    jobs = [
        _job(n, "skipped" if n in EVIDENCE_PREDICATE_TIER else "success")
        for n in STRICT_GATE_JOBS + SKIPPABLE_GATE_JOBS
    ]
    jobs.append(_job("Evidence-Only Diff Predicate", "success"))
    code, report = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 0, f"evidence-only slim path did not go green:\n{report}"


def test_failed_evidence_only_predicate_cascade_is_not_a_vacuous_success() -> None:
    """Regression pin, same class as the zone-filter cascade test above
    (PR #6435). A FAILED evidence-only-predicate job cascades every job
    that `needs:` it to `skipped` (the 5-job EVIDENCE_PREDICATE_TIER, all
    SKIPPABLE, which tolerates `skipped` unconditionally in isolation) --
    the predicate job's OWN failure, caught by the default-deny sweep
    because it is CLASSIFICATION_ONLY and not soft-allowlisted, is what
    keeps this fail-closed instead of a vacuous SUCCESS."""

    jobs = [
        _job(n, "skipped" if n in EVIDENCE_PREDICATE_TIER else "success")
        for n in STRICT_GATE_JOBS + SKIPPABLE_GATE_JOBS
    ]
    jobs.append(_job("Evidence-Only Diff Predicate", "failure"))
    code, report = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 1, (
        f"fail-open: evidence-only-predicate failure yielded exit {code}\n{report}"
    )
    assert "Evidence-Only Diff Predicate" in report


def test_evidence_predicate_tier_job_failure_still_fails_closed() -> None:
    """A non-evidence-only diff (predicate succeeded, asserted
    evidence_only=false) runs the full tier for real -- a genuine failure in
    one of those 5 jobs must fail the gate exactly like any other
    SKIPPABLE-tier failure (`success`/`skipped` are good; `failure` is not)."""

    victim = next(iter(EVIDENCE_PREDICATE_TIER))
    jobs = _all_green_jobs()
    jobs.append(_job("Evidence-Only Diff Predicate", "success"))
    for j in jobs:
        if j["name"] == victim:
            j["conclusion"] = "failure"
    code, report = evaluate(
        jobs,
        check_runs=_all_green_check_runs(),
        external_contexts=EXPECTED_EXTERNAL_CONTEXTS,
    )
    assert code == 1
    assert victim in report


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


# ---------------------------------------------------------------------------
# OMN-16095 -- CI Summary poller hang on a >100-check-run head, and a single
# transient GitHub API read killing the job instead of retrying.
#
# The actual `commits/{sha}/check-runs` and `actions/runs/{id}/jobs` HTTP
# fetches are NOT in this module -- ci_summary_gate.py only ever sees
# whatever `--check-runs-file`/`--jobs-file` hand it (this was the whole
# finding of OMN-16095's filing: source review of this file alone could
# neither confirm nor rule out unpaginated fetch, because the fetch isn't
# here). The real fetch + retry logic lives in ci.yml's "Poll job list +
# PR-head check-runs until a terminal verdict" step, as the
# `fetch_paginated` bash function. These tests extract that function
# straight out of the live workflow file (brace-matched, not a hand-copied
# string) and execute it against a stub `gh`, so a future edit to the real
# step is what these tests exercise -- not a frozen copy that can silently
# drift from the source of truth.
# ---------------------------------------------------------------------------

CI_SUMMARY_JOB_NAME = "ci-summary"
CI_SUMMARY_POLL_STEP_NAME = (
    "Poll job list + PR-head check-runs until a terminal verdict"
)


def _ci_summary_poll_script() -> str:
    doc = yaml.safe_load((WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8"))
    for step in doc["jobs"][CI_SUMMARY_JOB_NAME]["steps"]:
        if step.get("name") == CI_SUMMARY_POLL_STEP_NAME:
            run = step["run"]
            assert isinstance(run, str)
            return run
    msg = (
        f"{CI_SUMMARY_POLL_STEP_NAME!r} step not found in ci.yml's "
        f"{CI_SUMMARY_JOB_NAME!r} job"
    )
    raise AssertionError(msg)


def _extract_fetch_paginated_function() -> str:
    script = _ci_summary_poll_script()
    start = script.index("fetch_paginated() {")
    end_match = re.search(r"\n\}\n", script[start:])
    assert end_match, "fetch_paginated() closing brace not found in ci.yml"
    return script[start : start + end_match.end()]


def _run_fetch_paginated(
    tmp_path: Path,
    gh_stub_script: str,
    *,
    merge_expr: str = "{check_runs: (map(.check_runs) | add)}",
    fallback: str = '{"check_runs": []}',
    url: str = "repos/x/y/commits/abc/check-runs?per_page=100",
) -> tuple[int, Path, str]:
    """Run the real `fetch_paginated` bash function (extracted from ci.yml)
    against a stub `gh` on PATH, exactly the way the poll loop calls it.

    Returns ``(fetch_exit_code, out_file, stdout+stderr)``.
    """

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_path = bin_dir / "gh"
    gh_path.write_text(gh_stub_script, encoding="utf-8")
    gh_path.chmod(0o755)

    out_file = tmp_path / "out.json"
    driver = textwrap.dedent(f"""\
        set -euo pipefail
        {_extract_fetch_paginated_function()}
        sleep() {{ :; }}  # skip real backoff delay in tests
        set +e
        fetch_paginated "{url}" '{merge_expr}' "{out_file}" '{fallback}'
        code=$?
        set -e
        echo "FETCH_EXIT=$code"
        """)

    bash = shutil.which("bash")
    assert bash is not None, "bash not found on PATH"
    real_path = shutil.which("jq")
    assert real_path is not None, "jq not found on PATH (required by fetch_paginated)"
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:{Path(real_path).parent}",
        "HOME": str(tmp_path),
    }
    proc = subprocess.run(
        [bash, "-c", driver],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    combined = proc.stdout + proc.stderr
    match = re.search(r"FETCH_EXIT=(\d+)", combined)
    assert match, f"driver did not report FETCH_EXIT; output:\n{combined}"
    return int(match.group(1)), out_file, combined


def test_poll_step_paginates_check_runs_and_jobs_with_slurp_merge() -> None:
    """Anti-regression pin for the truncation defect: `gh api --paginate`
    alone, piped straight to a file, prints one JSON document PER PAGE for
    these OBJECT-rooted endpoints -- concatenating them past 100 entries is
    invalid JSON that `_load_check_runs` silently swallows as `None`. The
    fix must combine `--slurp` with a jq merge over every page's array."""

    script = _ci_summary_poll_script()
    assert "--paginate --slurp" in script, (
        "poll step must call `gh api --paginate --slurp` (bare --paginate "
        "on an object-rooted endpoint does not merge pages)"
    )
    assert "map(.check_runs) | add" in script, (
        "poll step must merge every page's check_runs array before it "
        "reaches ci_summary_gate.py"
    )
    assert "map(.jobs) | add" in script, (
        "poll step must merge every page's jobs array before it reaches "
        "ci_summary_gate.py"
    )


def test_poll_step_retries_are_bounded_and_guarded() -> None:
    """Anti-regression pin for the transient-EOF defect: both fetch call
    sites must be retried (not a bare `gh api` under `set -e`) and must not
    propagate a retry-exhausted failure as an uncaught script exit."""

    script = _ci_summary_poll_script()
    assert "max_attempts=3" in script, "retry must be bounded at 3 attempts"

    # Each `fetch_paginated \` CALL SITE (not the `fetch_paginated() {`
    # function definition) is its own multi-line statement ending in
    # `|| true`. Isolate each block so the "must be guarded" check pins the
    # actual call, not just any `|| true` occurring anywhere in the script.
    call_blocks = re.findall(r"fetch_paginated \\\n(?:.*\n)*?.*\|\| true", script)
    assert len(call_blocks) == 2, (
        f"expected exactly 2 guarded fetch_paginated call sites (jobs + "
        f"check-runs), found {len(call_blocks)}:\n{script}"
    )
    assert any("jobs?per_page" in b for b in call_blocks), (
        "the jobs-list fetch call site must be guarded (`|| true`) so "
        "retry-exhaustion falls through to the fallback file instead of "
        "killing the job under `set -euo pipefail`"
    )
    assert any("check-runs?per_page" in b for b in call_blocks), (
        "the check-runs fetch call site must be guarded (`|| true`) for the same reason"
    )


def test_fetch_paginated_retrieves_all_105_check_runs_no_truncation(
    tmp_path: Path,
) -> None:
    """Direct proof of OMN-16095 AC 2: feed the real fetch path a mocked
    105-entry (2-page) check-runs response and prove all 105 come through,
    with no truncation at the 100-per-page boundary."""

    gh_stub = textwrap.dedent("""\
        #!/usr/bin/env bash
        set -euo pipefail
        python3 - <<'PY'
        import json
        def page(n, offset):
            return {
                "total_count": 105,
                "check_runs": [
                    {
                        "name": f"ctx-{i}",
                        "id": i,
                        "started_at": "2026-01-01T00:00:00Z",
                        "status": "completed",
                        "conclusion": "success",
                    }
                    for i in range(offset, offset + n)
                ],
            }
        print(json.dumps([page(100, 0), page(5, 100)]))
        PY
        """)
    exit_code, out_file, output = _run_fetch_paginated(tmp_path, gh_stub)
    assert exit_code == 0, f"fetch_paginated should succeed on page 1:\n{output}"
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(payload["check_runs"]) == 105, (
        "pagination truncated the merged check-runs list: "
        f"got {len(payload['check_runs'])}, expected 105"
    )

    # Bridge back through the real python loader/evaluator to prove the
    # merged file is usable end-to-end, not just structurally 105-long.
    loaded = _load_check_runs(str(out_file))
    assert loaded is not None
    assert len(loaded) == 105
    expected = tuple(f"ctx-{i}" for i in (0, 50, 99, 100, 104))
    latest = latest_check_run_by_name(loaded)
    for name in expected:
        assert latest[name].status == "completed"
        assert latest[name].conclusion == "success"


def test_fetch_paginated_retries_transient_failure_then_succeeds(
    tmp_path: Path,
) -> None:
    """Direct proof of OMN-16095 defect 2's fix: a transient `gh api`
    failure (e.g. "unexpected EOF") must consume a retry, not kill the
    job -- the 3rd attempt succeeding must still produce a good result."""

    counter_file = tmp_path / "gh_call_count"
    gh_stub = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        n=0
        [[ -f "{counter_file}" ]] && n=$(cat "{counter_file}")
        n=$((n + 1))
        echo "$n" > "{counter_file}"
        if [[ "$n" -lt 3 ]]; then
          echo "gh: Get https://api.github.com/...: unexpected EOF" >&2
          exit 1
        fi
        python3 - <<'PY'
        import json
        run = {{
            "name": "ctx-ok",
            "id": 1,
            "started_at": "2026-01-01T00:00:00Z",
            "status": "completed",
            "conclusion": "success",
        }}
        print(json.dumps([{{"total_count": 1, "check_runs": [run]}}]))
        PY
        """)
    exit_code, out_file, output = _run_fetch_paginated(tmp_path, gh_stub)
    assert exit_code == 0, (
        f"a failure that resolves within max_attempts must still succeed:\n{output}"
    )
    assert counter_file.read_text(encoding="utf-8").strip() == "3", (
        f"expected exactly 3 gh invocations (2 failures + 1 success), got: {output}"
    )
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["check_runs"][0]["name"] == "ctx-ok"


def test_fetch_paginated_retry_exhaustion_fails_closed_not_crashed(
    tmp_path: Path,
) -> None:
    """Direct proof of AC 6 (fail-closed preserved): when `gh api` fails on
    every attempt, `fetch_paginated` must NOT crash the poll script -- it
    must fall through to the fallback payload, which the real
    ci_summary_gate.py loader/evaluator must then read as PENDING (every
    context unresolved), never a spurious SUCCESS and never an exception."""

    gh_stub = textwrap.dedent("""\
        #!/usr/bin/env bash
        echo "gh: Get https://api.github.com/...: unexpected EOF" >&2
        exit 1
        """)
    exit_code, out_file, output = _run_fetch_paginated(tmp_path, gh_stub)
    assert exit_code == 1, (
        f"retry-exhaustion must report failure to the caller:\n{output}"
    )
    assert out_file.exists(), (
        "retry-exhaustion must still write the fallback payload file"
    )
    assert "attempt 1/3" in output
    assert "attempt 2/3" in output
    assert "attempt 3/3" in output
    assert "retry-exhausted" in output

    # Bridge into the real python gate: the fallback file must resolve to
    # "every external context unresolved" (PENDING), matching the documented
    # None-equivalent contract -- never a crash, never green.
    loaded = _load_check_runs(str(out_file))
    assert loaded == []
    failures, unresolved = evaluate_external_contexts(
        loaded, EXPECTED_EXTERNAL_CONTEXTS
    )
    assert failures == []
    assert sorted(unresolved) == sorted(EXPECTED_EXTERNAL_CONTEXTS)
