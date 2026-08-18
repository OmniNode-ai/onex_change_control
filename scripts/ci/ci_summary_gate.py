# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Fail-closed verdict for the ``CI Summary`` required-context poller.

OMN-15768 enforce-everything fan-out.

Why this exists
---------------
``onex_change_control`` (OCC) dev requires exactly four direct contexts
(``CI Summary``, ``verify / verify``, ``occ-preflight / eligibility``,
``required-check-skip-guard / check-skip-vectors``). ``CI Summary`` itself used
to be a plain ``needs:``-gated aggregator over ~27 ``ci.yml`` jobs, with a
two-tier pass/fail body: 13 named strict blocks plus a generic rollup that only
tested ``contains(needs.*.result, 'failure') || contains(needs.*.result,
'cancelled')`` — which does **not** catch ``skipped``. Two independent defects
followed from that shape:

1. **Needs-graph omission.** ``contract-compliance`` — the job at the center of
   OCC#6346 (merged 2026-08-11, merge commit ``5399a37f``, with ``Contract
   Compliance Check`` = **failure** and ``CI Summary`` = **success** on the same
   head SHA) was never listed in ``ci-summary``'s ``needs:`` at all. A job
   absent from ``needs:`` is invisible to a ``needs:``-based aggregator by
   construction — no amount of "the generic rollup would have caught it"
   reasoning applies, because the rollup only inspects ``needs.*.result``.
   ``substance-floor`` and ``divergent-automation-pr-guard`` carried the same
   omission despite being unconditional (``if:``-less) jobs.
2. **Skip-as-pass.** 12 jobs (``schema-purity``, ``no-localhost-fallbacks``,
   ``imperative-contract-guard``, ``context-integrity-contracts``,
   ``migration-conflicts``, ``migration-inventory``, ``kafka-boundary-parity``,
   ``cross-repo-null-contract``, ``seam-contract-coverage``,
   ``url-authority-gate``, ``no-new-os-environ``, ``ai-slop-check``) carried an
   undocumented ``(github.event_name != 'pull_request' || github.base_ref !=
   'dev')`` clause that made them skip on *every* dev-targeting PR — the branch
   every real OCC merge lands on — while the generic rollup read a ``skipped``
   result as passing (OMN-15768).

This module replaces the ``needs:``-gated aggregator with a NO-``needs``,
GitHub-hosted poller: its check-run instantiates immediately (so the required
context can never be absent under runner-fleet saturation), and it calls this
module in a loop against the current run's job list until a terminal verdict
is reached (or a bounded deadline fires -> fail-closed).

Verdict policy — DEFAULT-DENY, FAIL-CLOSED, four layers
--------------------------------------------------------
1. **L1 STRICT_GATE_JOBS.** Every in-run (``ci.yml``) job that is
   unconditional (no legitimate skip path) must be *present*, *completed*, and
   conclude ``success``. ``skipped``/``failure``/``cancelled`` all fail.
2. **L2 SKIPPABLE_GATE_JOBS.** Jobs that carry a gate-re-derivable skip
   precondition (the ``docs_only`` evidence-only fast lane, or a legitimate
   ``push``-only skip) must be *present*, *completed*, and conclude ``success``
   **or** ``skipped``. The precondition itself is enforced by the job's own
   ``if:`` in ``ci.yml`` (verified against source at authoring time, pinned by
   ``test_every_pr_triggered_job_is_classified`` and the red-replay tests
   below) — this module does not re-parse ``ci.yml`` at runtime.
3. **L3 default-deny sweep.** Any *other* job present in this run's job list,
   completed, and not ``success``/``skipped`` fails the gate — unless it is the
   poller itself or an explicit, reason-carrying :data:`SOFT_ALLOWLIST` entry.
4. **L4 EXPECTED_EXTERNAL_CONTEXTS.** Checks 1-3 only see ``actions/runs/
   {RUN_ID}/jobs`` — this workflow run's own job list. A job living in ANY
   OTHER workflow file (``dep-provenance-gate.yml``, ``call-receipt-gate.yml``,
   ``main-target-guard.yml``, ...) is structurally invisible to it. This layer
   asserts each named context against the PR head's ``commits/{sha}/
   check-runs``: present + completed + ``success`` (``skipped`` fails closed
   here — every listed context was verified to run unconditionally on ordinary
   dev PRs, see the per-entry comments below); missing/still-running is
   PENDING, converted to FAILURE at the caller's deadline; an unfetchable
   check-runs list is treated as every context unobserved, never green.

Exit codes: ``0`` success, ``1`` failure, ``2`` pending.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# The poller's own job -- excluded to avoid self-deadlock.
SELF_JOB_NAME = "CI Summary"

# ---------------------------------------------------------------------------
# L1 -- unconditional ci.yml jobs. Verified against .github/workflows/ci.yml
# at authoring time (2026-08-13): none of these carry an `if:` that can
# legitimately produce anything but `success` on a pull_request/merge_group
# run. A `skipped` here is always the OMN-15768 hole reopening, never a
# deliberate opt-out.
# ---------------------------------------------------------------------------
STRICT_GATE_JOBS: tuple[str, ...] = (
    # OMN-15731 label-gated CI pilot: `pre-commit` legitimately SKIPS for an
    # unlabeled dev-targeting PR (missing `ci:ready`) -- but that skip is
    # DELIBERATELY treated as blocking (the pilot's whole point is to force
    # the label), so this stays STRICT, not SKIPPABLE. Matches the exact
    # behavior of the needs-based aggregator it replaces.
    "Pre-commit",
    # Deliberately unconditional (no needs/if) per their own ci.yml headers --
    # a defense that only runs when its own trigger files change is a defense
    # that never runs (OMN-14416 lesson), so both scan the whole tree/corpus
    # on every PR.
    "Contract Substance Floor (OMN-14409)",
    "No Divergent Automation PRs (OMN-14778)",
    "Validate Contract YAML (OMN-8808)",
    "Receipt Honesty Gate",
    "OCC Append-Only Gate",
    "no-noncanonical-lifecycle-classes",
    "validate-prod-promotion-grants",
    "check-platform-leads-review-tripwire",
    "check-bot-authored-authz-guard",
    "Precommit Parity Gate",
    "Evidence Admissibility Predicate Parity",
    "Contract Corpus Ratchets (OMN-15411)",
    "yamlfmt Contamination Ratchet (OMN-15479)",
    "Supersession Binding Ratchet (OMN-15459)",
    # Reusable-workflow caller; the job's own `name:` is set to this literal
    # composed string (Shape A), matching how GitHub surfaces the check-run.
    "merge-hold-gate / evaluate",
    "Contract Shape v1 (OMN-15669)",
)

# ---------------------------------------------------------------------------
# L2 -- jobs with a legitimate, gate-re-derivable skip path. Each precondition
# is enforced by the job's own `if:` in ci.yml (docs_only evidence-only fast
# lane per OMN-14098, or a `push`-only skip); this module trusts that `if:`
# rather than re-parsing it, and is pinned against drift by
# test_every_pr_triggered_job_is_classified + test_no_job_carries_a_base_ref_
# dev_exemption below.
# ---------------------------------------------------------------------------
SKIPPABLE_GATE_JOBS: tuple[str, ...] = (
    # docs_only (OMN-14098) evidence-only fast lane -- skip legal iff the
    # zone-filter classified the PR as pure contracts/+drift/dod_receipts/
    # evidence. OMN-15731 removed the `base_ref != 'dev'` clause that used to
    # make these skip on EVERY dev PR regardless of content.
    "Type Check",
    "Tests",
    "Schema Purity & Naming Check",
    "No localhost env-var fallbacks in src/ (OMN-10737)",
    "Imperative Contract Guard",
    "AI-Slop Pattern Check (strict, PR diff)",
    "Context Integrity Contract Compliance",
    "Migration Conflict Check",
    "Migration Inventory Sync",
    "Kafka Boundary Parity",
    "Python↔TypeScript Null Contract",
    "Seam Contract Coverage",
    # Legitimate `push`-only skip (`if: github.event_name == 'pull_request' ||
    # github.event_name == 'merge_group'`).
    "Contract Sync Gate (Layer 3)",
    # THE OCC#6346 FIX. Was absent from the old aggregator's `needs:` entirely
    # -- a red Contract Compliance Check could merge with CI Summary green.
    # Same push-only skip precondition as Contract Sync Gate above.
    "Contract Compliance Check",
)

# Every job the completeness anchor must observe present+good for SUCCESS.
GATE_JOBS: tuple[str, ...] = STRICT_GATE_JOBS + SKIPPABLE_GATE_JOBS

# ---------------------------------------------------------------------------
# L3 -- default-deny sweep allowlist. Jobs already non-gating in ci.yml BY
# DESIGN (continue-on-error advisory steps, or structural reusable-caller
# jobs). Matching is prefix-aware (see `_is_allowlisted`) so a reusable
# caller's inner jobs (surfaced as "<caller name> / <inner job>") are covered
# by the caller entry.
# ---------------------------------------------------------------------------
SOFT_ALLOWLIST: frozenset[str] = frozenset(
    {
        # ci.yml:384-411 -- `continue-on-error: true` on the one substantive
        # step, with an inline comment: "non-blocking report; pre-commit hook
        # is the blocking surface." The job can never conclude anything but
        # `success` once it runs, so gating on it would add nothing; the
        # pre-commit `url-authority` hook is the real enforcement layer.
        "URL Authority Gate (OMN-13563)",
        # ci.yml:413-438 -- same continue-on-error shape and the same inline
        # rationale ("non-blocking report; pre-commit hook is the blocking
        # surface"), enforced instead by the `no-new-os-environ` pre-commit
        # hook.
        "No New os.environ Reads (OMN-13563)",
    }
)

# ---------------------------------------------------------------------------
# L3b -- CLASSIFICATION-ONLY. Accounted for by the completeness test, but
# DELIBERATELY NOT allowlisted: the L3 default-deny sweep still fails the run
# on a present+completed+not-good entry here. The sweep ignores ABSENT jobs,
# so listing a name here can never wedge a branch.
# ---------------------------------------------------------------------------
CLASSIFICATION_ONLY: dict[str, str] = {
    "zone-filter": (
        "Structural reusable-workflow caller (zone-filter.yml@dev), not a "
        "validator -- so not a STRICT/SKIPPABLE gate. It is NOT soft-"
        "allowlisted: all 12 docs_only-tier SKIPPABLE jobs `needs:` it, and a "
        "FAILED zone-filter cascades every one of them to `skipped`, which "
        "the SKIPPABLE tier tolerates unconditionally -- a SUCCESS verdict "
        "with zero tests and zero type-checks having run. The sweep catching "
        "the zone-filter failure itself closes that fail-open."
    ),
}

# ---------------------------------------------------------------------------
# L4 -- cross-workflow ("external") required contexts (OMN-15496 pattern).
# ---------------------------------------------------------------------------
# ADMISSION RULE: a context is listed here only after confirming its
# `pull_request` trigger fires unconditionally against `dev` (branches filter
# includes `dev` or has none) and its job has no `base_ref`/`docs_only`-style
# skip that would make it silently absent or `skipped` on an ordinary
# non-docs-only dev PR (that would burn the poll deadline and wedge the
# branch -- see EXEMPT_CONTEXTS below for the two contexts that failed this
# check and were deliberately left out). Verified by direct source read of
# every listed workflow file on 2026-08-13, cross-checked against the live
# check-run list on OCC#6231 (a real, currently open dev PR against this same
# ci.yml) where every one of these appeared `success` (two appeared
# transiently `cancelled` under runner contention -- validate-localhost-url --
# which is exactly the fail-closed case this layer exists to catch, not
# evidence the context is unsafe to assert).
EXPECTED_EXTERNAL_CONTEXTS: tuple[str, ...] = (
    "Dep Provenance Gate",  # dep-provenance-gate.yml -- unconditional
    "validate-private-ip",  # validator-g2-ip-family.yml -- unconditional
    "validate-localhost-url",  # validator-g2-ip-family.yml -- unconditional
    "validate-topic-string",  # validator-g2-ip-family.yml -- unconditional
    "validate-todo-marker",  # validator-g2-ip-family.yml -- unconditional
    "main-target-guard",  # main-target-guard.yml -- unconditional
    "non-dev-base-guard",  # non-dev-base-guard.yml -- unconditional
    "occ-preflight / eligibility",  # call-occ-preflight.yml -- already a
    # direct dev-required context; asserted here too so CI Summary does not
    # depend on branch protection staying in sync (belt-and-braces, matches
    # the reference implementation's treatment of `deploy-gate / deploy-gate`).
    "verify / verify",  # call-receipt-gate.yml -- already direct-required;
    # same belt-and-braces rationale as occ-preflight above.
    "call-reject-skip-token / scan / reject-skip-gate-token",  # call-reject-skip.yml
    "required-check-skip-guard / check-skip-vectors",  # already direct-required
    "pr-title / check-title",  # pr-title-check.yml -- no branches: filter
    "Stale TODO Gate",  # stale-todo-gate.yml -- unconditional
    # validate-validator-requirements.yml -- unconditional
    "Enforce validator-requirements.yaml (OMN-13299)",
    "call / validate-docs",  # docs-validate.yml -- unconditional
    "CI Naming Convention",  # omni-standards-compliance.yml -- unconditional
    # doctrine-coverage.yml: the job itself is unconditional (`if:
    # !cancelled()`); this PR also removes the base_ref!='dev' clause that
    # previously skipped every SUBSTANTIVE STEP (not the job) on dev PRs, so
    # asserting this context now means something instead of trivially
    # succeeding on an empty step list.
    "Doctrine Coverage Matrix",
    # security-scan.yml: this PR adds `dev` to the job's `pull_request.branches`
    # trigger (it previously fired on `[main, hotfix/**]` only -- ABSENT on
    # every dev PR, not skipped). The job's own docs_only skip-with-success
    # design (ci.yml:82-86 equivalent) was already wedge-safe.
    "CodeQL / CodeQL Analysis (python)",
)

# Contexts that were CONSIDERED for L4 and deliberately NOT enforced, with the
# concrete reason. Recorded as data -- not a silent omission -- so a reviewer
# does not have to rediscover why a plausible candidate is missing, per the
# fleet-wide MEASURED_NOT_ENFORCED / EXEMPT convention.
EXEMPT_CONTEXTS: dict[str, str] = {
    "PEP 604 Type Union Check (UP007)": (
        "omni-standards-compliance.yml's type-union-check job legitimately "
        "skips on docs_only evidence-only PRs (OMN-14098 fast lane). This "
        "layer's EXTERNAL_GOOD_CONCLUSIONS is success-only (skip fails "
        "closed) with no skippable-external tier yet, so asserting this "
        "context would fail-closed on every evidence-only PR and wedge the "
        "fast lane. Deferred to a follow-up that either re-derives docs_only "
        "for external contexts or adds a skippable-external tier."
    ),
    "Self-companion guard (OMN-15334)": (
        "Duplicate producer: the identical job name is emitted by BOTH "
        "call-occ-autobind.yml and call-occ-companion-effect.yml on the same "
        "PR (confirmed live on OCC#6231 -- two same-named check-runs, both "
        "success). Same ANY-vs-ALL branch-protection ambiguity class as "
        "OMN-15112's occ-preflight finding; asserting one name would not "
        "distinguish which producer is being observed. Excluded pending a "
        "producer-side rename."
    ),
    "occ-autobind / Publish occ-autobind command": (
        "call-occ-autobind.yml:66-67 self-declares 'ADDITIVE / REJECTS "
        "NOTHING: this workflow is NOT a required status check. It publishes "
        "a command; it never fails a PR's merge.' Structurally cannot "
        "validate anything -- it is a command publisher, not a gate."
    ),
    "occ-companion-effect / Publish occ-companion-effect command": (
        "call-occ-companion-effect.yml:60-61 carries the identical "
        "'ADDITIVE / REJECTS NOTHING' self-declaration as occ-autobind "
        "above. Same reasoning."
    ),
    "Enable Auto-Merge": (
        "auto-merge.yml's job arms auto-merge on an already-eligible PR; it "
        "is an automation trigger, not a validator, and its own logic reads "
        "the other required checks' state -- asserting it here would be "
        "circular (it would depend on itself having already run)."
    ),
    "Claude Code": (
        "claude.yml triggers on issue_comment / pull_request_review_comment "
        "/ pull_request_review / issues with an actor filter requiring an "
        "'@claude' mention -- it does not fire on ordinary PR "
        "opened/synchronize events. A context that does not report on every "
        "PR shape must never be listed (the reference implementation's own "
        "admission rule) -- it would burn the poll deadline on most PRs and "
        "wedge the branch."
    ),
    "TODO Audit": (
        "todo-audit-on-merge.yml's job is gated `if: "
        "github.event.pull_request.merged == true` -- it runs only AFTER a "
        "PR merges, so it structurally cannot be a pre-merge gate for that "
        "same PR."
    ),
    # product-readiness-shadow.yml family: every job name is literally suffixed
    # "(shadow)" or is the bare `reason-graph` job in that same file. The
    # file's own header (matches the identical pattern cited inline at
    # ci.yml's contract-corpus-ratchets job) states these are "deliberately
    # kept OUT of required_status_checks, so a red shadow ... CANNOT block
    # merges" -- an intentional non-gating proof surface, not a gap.
    "lint (shadow)": (
        "product-readiness-shadow.yml: self-declared shadow proof surface, "
        "deliberately non-gating by design (matches the file's own header)."
    ),
    "typecheck (shadow)": (
        "product-readiness-shadow.yml: self-declared shadow proof surface, "
        "deliberately non-gating by design (matches the file's own header)."
    ),
    "tests+coverage (shadow)": (
        "product-readiness-shadow.yml: self-declared shadow proof surface, "
        "deliberately non-gating by design (matches the file's own header)."
    ),
    "reason-graph": (
        "product-readiness-shadow.yml: shadow-family job (no '(shadow)' "
        "suffix in its display name, but same file, same non-gating design). "
        "Was a REQUIRED context prior to the 2026-07-25 required-checks.yaml "
        "reconcile pass, which removed it after confirming it was no longer "
        "live-required on either branch; re-promoting it is a separate, "
        "deliberate decision, not something this PR should silently redo by "
        "asserting it in L4."
    ),
}

# External contexts are held to the STRICT bar: `skipped` fails closed. Every
# name in EXPECTED_EXTERNAL_CONTEXTS was verified to run unconditionally
# (never skip) on an ordinary dev PR, so this costs nothing today and closes
# the skip-vector fail-open the same way STRICT_GATE_JOBS does for in-run jobs.
GOOD_CONCLUSIONS: frozenset[str] = frozenset({"success", "skipped"})
EXTERNAL_GOOD_CONCLUSIONS: frozenset[str] = frozenset({"success"})

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_PENDING = 2


@dataclass(frozen=True)
class JobState:
    """The latest-attempt state of a single workflow job."""

    name: str
    status: str  # queued | in_progress | completed | waiting | ...
    conclusion: str | None  # success | failure | cancelled | skipped | timed_out | None
    run_attempt: int


def _state_severity(job: JobState) -> int:
    """Rank same-attempt duplicate jobs by the most blocking state."""

    if job.status != "completed":
        return 2
    if job.conclusion not in GOOD_CONCLUSIONS:
        return 3
    return 1


def dedup_latest(
    jobs: list[dict[str, object]],
    *,
    run_attempt: int | None = None,
) -> dict[str, JobState]:
    """Collapse the raw ``/runs/{id}/jobs`` array to one entry per job name.

    When ``run_attempt`` is provided, only rows from that workflow attempt are
    considered. Within the same attempt, duplicate display names keep the
    most blocking state so a failed matrix leg cannot be hidden by a later
    same-name success.
    """

    latest: dict[str, JobState] = {}
    for raw in jobs:
        name = str(raw.get("name") or "")
        if not name:
            continue
        try:
            attempt = int(str(raw.get("run_attempt") or 1))
        except (TypeError, ValueError):
            attempt = 1
        if run_attempt is not None and attempt != run_attempt:
            continue
        prev = latest.get(name)
        if prev is not None and attempt < prev.run_attempt:
            continue
        conclusion = raw.get("conclusion")
        current = JobState(
            name=name,
            status=str(raw.get("status") or ""),
            conclusion=None if conclusion is None else str(conclusion),
            run_attempt=attempt,
        )
        if (
            prev is not None
            and attempt == prev.run_attempt
            and _state_severity(current) < _state_severity(prev)
        ):
            continue
        latest[name] = current
    return latest


def latest_check_run_by_name(
    check_runs: list[dict[str, object]],
) -> dict[str, JobState]:
    """Collapse ``commits/{sha}/check-runs`` to one entry per context name.

    Resolution is **latest wins** by ``(started_at, id)`` -- the same rule
    GitHub applies when deciding a required status check from several
    same-named check-runs on one SHA.
    """

    latest: dict[str, JobState] = {}
    ordering: dict[str, tuple[str, int]] = {}
    for raw in check_runs:
        name = str(raw.get("name") or "")
        if not name:
            continue
        try:
            run_id = int(str(raw.get("id") or 0))
        except (TypeError, ValueError):
            run_id = 0
        key = (str(raw.get("started_at") or ""), run_id)
        if name in ordering and key <= ordering[name]:
            continue
        conclusion = raw.get("conclusion")
        ordering[name] = key
        latest[name] = JobState(
            name=name,
            status=str(raw.get("status") or ""),
            conclusion=None if conclusion is None else str(conclusion),
            run_attempt=1,
        )
    return latest


def evaluate_external_contexts(
    check_runs: list[dict[str, object]] | None,
    expected: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    """Return ``(failures, missing_or_pending)`` for the declared external contexts.

    ``check_runs is None`` means the caller could not fetch the head SHA's
    check-runs. That is treated as **every** expected context being
    unobserved -- PENDING, never success -- so a transient API failure
    retries and a permanent one fails closed at the deadline.
    """

    if not expected:
        return [], []
    latest = latest_check_run_by_name(check_runs or [])
    failures: list[str] = []
    unresolved: list[str] = []
    for context in expected:
        state = latest.get(context)
        if state is None or state.status != "completed":
            unresolved.append(context)
        elif state.conclusion not in EXTERNAL_GOOD_CONCLUSIONS:
            failures.append(context)
    return sorted(failures), sorted(unresolved)


def _is_allowlisted(name: str, allowlist: frozenset[str]) -> bool:
    """Prefix-aware allowlist check.

    A reusable-workflow caller's inner jobs surface in the jobs API as
    ``"<caller display name> / <inner job name>"``; matching the caller
    segment lets a single allowlist entry cover all of its inner jobs.
    """

    if name in allowlist:
        return True
    caller = name.split(" / ", 1)[0]
    return caller in allowlist


def evaluate(
    jobs: list[dict[str, object]],
    *,
    run_attempt: int | None = None,
    self_name: str = SELF_JOB_NAME,
    strict_gates: tuple[str, ...] = STRICT_GATE_JOBS,
    skippable_gates: tuple[str, ...] = SKIPPABLE_GATE_JOBS,
    allowlist: frozenset[str] = SOFT_ALLOWLIST,
    check_runs: list[dict[str, object]] | None = None,
    external_contexts: tuple[str, ...] = (),
) -> tuple[int, str]:
    """Return ``(exit_code, human_report)`` for the current job snapshot."""

    latest = dedup_latest(jobs, run_attempt=run_attempt)
    gate_names = frozenset(strict_gates) | frozenset(skippable_gates)

    # (1) Strict aggregate gates: present + completed + conclusion == success.
    strict_failures = sorted(
        g
        for g in strict_gates
        if (
            (st := latest.get(g)) is not None
            and st.status == "completed"
            and st.conclusion != "success"
        )
    )

    # (2) Skippable aggregate gates: present + completed + success/skipped.
    skippable_failures = sorted(
        g
        for g in skippable_gates
        if (
            (st := latest.get(g)) is not None
            and st.status == "completed"
            and st.conclusion not in GOOD_CONCLUSIONS
        )
    )

    # (3) Default-deny sweep over every OTHER present+completed job.
    sweep_failures = sorted(
        j.name
        for name, j in latest.items()
        if name != self_name
        and name not in gate_names
        and not _is_allowlisted(name, allowlist)
        and j.status == "completed"
        and j.conclusion not in GOOD_CONCLUSIONS
    )

    # Completeness anchor: every gate must be present AND completed.
    gate_missing_or_pending = [
        g
        for g in (*strict_gates, *skippable_gates)
        if (latest.get(g) is None or latest[g].status != "completed")
    ]

    # (4) L4 external contexts: cross-workflow checks on the PR head.
    external_failures, external_unresolved = evaluate_external_contexts(
        check_runs, external_contexts
    )

    all_failures = (
        strict_failures + skippable_failures + sweep_failures + external_failures
    )
    all_unresolved = gate_missing_or_pending + external_unresolved

    def _verdict(label: str) -> str:
        return _report(
            label,
            latest,
            strict_gates,
            skippable_gates,
            strict_failures,
            skippable_failures,
            sweep_failures,
            gate_missing_or_pending,
            external_contexts,
            external_failures,
            external_unresolved,
        )

    if all_failures:
        return EXIT_FAILURE, _verdict("FAILURE")
    if all_unresolved:
        return EXIT_PENDING, _verdict("PENDING")
    return EXIT_SUCCESS, _verdict("SUCCESS")


def _report(
    verdict: str,
    latest: dict[str, JobState],
    strict_gates: tuple[str, ...],
    skippable_gates: tuple[str, ...],
    strict_failures: list[str],
    skippable_failures: list[str],
    sweep_failures: list[str],
    gate_missing_or_pending: list[str],
    external_contexts: tuple[str, ...] = (),
    external_failures: list[str] | None = None,
    external_unresolved: list[str] | None = None,
) -> str:
    lines = [f"CI Summary verdict: {verdict}", f"  jobs observed: {len(latest)}"]
    lines.append("  strict gates:")
    for g in strict_gates:
        st = latest.get(g)
        lines.append(
            f"    - {g}: <absent>"
            if st is None
            else f"    - {g}: {st.status}/{st.conclusion}"
        )
    lines.append("  skippable gates:")
    for g in skippable_gates:
        st = latest.get(g)
        lines.append(
            f"    - {g}: <absent>"
            if st is None
            else f"    - {g}: {st.status}/{st.conclusion}"
        )
    if strict_failures:
        lines.append(f"  strict-gate failures: {', '.join(strict_failures)}")
    if skippable_failures:
        lines.append(f"  skippable-gate failures: {', '.join(skippable_failures)}")
    if sweep_failures:
        lines.append(f"  default-deny sweep failures: {', '.join(sweep_failures)}")
    if gate_missing_or_pending:
        lines.append(f"  gates missing/pending: {', '.join(gate_missing_or_pending)}")
    if external_contexts:
        lines.append(f"  external contexts asserted: {len(external_contexts)}")
        if external_failures:
            lines.append(f"  external-context failures: {', '.join(external_failures)}")
        if external_unresolved:
            lines.append(
                f"  external contexts missing/pending: {', '.join(external_unresolved)}"
            )
    return "\n".join(lines)


def _load_jobs(path: str | None) -> list[dict[str, object]]:
    if path is None or path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    # Accept either the raw endpoint object ({"jobs": [...]}) or a bare array.
    jobs = data.get("jobs", []) if isinstance(data, dict) else data
    if not isinstance(jobs, list):
        msg = "jobs payload must be a list or an object with a 'jobs' array"
        raise TypeError(msg)
    return jobs


def _load_check_runs(path: str | None) -> list[dict[str, object]] | None:
    """Load ``commits/{sha}/check-runs``; return ``None`` when unavailable.

    ``None`` is the fail-closed signal: :func:`evaluate_external_contexts`
    reads it as "no context observed" -> PENDING -> FAILURE at the caller's
    deadline. A missing, empty, or malformed payload must never green the gate.
    """

    if not path:
        return None
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        data = data.get("check_runs", [])
    if not isinstance(data, list):
        return None
    return [row for row in data if isinstance(row, dict)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--jobs-file",
        default="-",
        help="Path to the GitHub Actions jobs JSON (default: stdin). Accepts the "
        "raw endpoint object or a bare array of job objects.",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print the verdict report and exit 0 regardless (diagnostics only).",
    )
    parser.add_argument(
        "--run-attempt",
        type=int,
        default=None,
        help="Evaluate only rows for this GitHub Actions run_attempt.",
    )
    parser.add_argument(
        "--check-runs-file",
        default=None,
        help="Path to the PR head SHA's commits/{sha}/check-runs JSON, used to "
        "assert EXPECTED_EXTERNAL_CONTEXTS. A missing/unreadable file is "
        "PENDING, never success.",
    )
    parser.add_argument(
        "--event-name",
        default="pull_request",
        help="GitHub event name. External contexts are asserted on "
        "'pull_request' only -- merge_group/workflow_dispatch have no "
        "PR-scoped context set. Defaults to 'pull_request' so a FORGOTTEN "
        "argument enforces rather than silently skips.",
    )
    args = parser.parse_args(argv)

    jobs = _load_jobs(args.jobs_file)
    external_contexts = (
        EXPECTED_EXTERNAL_CONTEXTS if args.event_name == "pull_request" else ()
    )
    code, report = evaluate(
        jobs,
        run_attempt=args.run_attempt,
        check_runs=_load_check_runs(args.check_runs_file),
        external_contexts=external_contexts,
    )
    print(report)
    if args.report_only:
        return EXIT_SUCCESS
    return code


if __name__ == "__main__":
    raise SystemExit(main())
