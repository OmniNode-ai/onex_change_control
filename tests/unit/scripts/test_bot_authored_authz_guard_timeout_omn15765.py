# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15765: check-bot-authored-authz-guard must not false-fail on starvation.

Root cause (not a matcher defect)
----------------------------------
The 2026-08-08 D-cluster census found ``check-bot-authored-authz-guard`` red on
12 of 17 open OCC PRs, 5 of them human-authored (occ#6172/#6175/#6176/#6182/
#6188). The guard's identity/path decision logic (``evaluate`` in
``check_bot_authored_authz_guard.py``) is unit-tested correct in both
directions elsewhere in this suite
(``tests/unit/scripts/test_check_bot_authored_authz_guard.py`` —
``test_acceptance_1_bot_commit_touches_grants_rejects`` /
``test_acceptance_2_human_touches_grants_passes``) and is untouched by this
fix. Pulling the actual failing job logs (occ#6176 run 31244470013,
occ#6182 run 31234031110) showed every one of them dying inside
``actions/checkout@v7`` / the ``setup-uv`` composite — never reaching the
guard's own step — with ``conclusion=cancelled`` at the job's
``timeout-minutes: 5`` wall. CI Summary's fail-closed ``!= success``
comparison then reddens that cancellation as an authz violation.

This test is the regression anchor for the fix: the job's ``timeout-minutes``
must sit above the latency the preamble (``fetch-depth: 0`` checkout + uv
provisioning) has actually been observed to take on the contended
``omnibase-ci`` fleet (OMN-15722, PR #6173 run 31222755192: up to ~23m
combined), not a budget sized only to the guard's own sub-second Python
logic. It is RED against the pre-fix value of 5 and GREEN against the fixed
value of 45 (matching the uniform floor OMN-15722 set for the other nine
timed ci-summary members sharing this preamble).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_YAML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_JOB_ID = "check-bot-authored-authz-guard"

# Below the lowest latency actually observed for this job's preamble
# (checkout + setup-uv) under fleet contention; a budget below this floor
# reintroduces the starvation false-fail this ticket fixes.
_MIN_SAFE_TIMEOUT_MINUTES = 20


def _load_ci_workflow() -> dict[str, Any]:
    with _CI_YAML.open(encoding="utf-8") as fh:
        doc: dict[str, Any] = yaml.safe_load(fh)
    return doc


def test_ci_yaml_defines_the_guard_job() -> None:
    """Anti-drift anchor: fail loudly if the job is renamed/removed, not silently."""
    doc = _load_ci_workflow()
    jobs = doc.get("jobs", {})
    assert _JOB_ID in jobs, (
        f"{_JOB_ID!r} not found in ci.yml jobs — this test's target moved or "
        "was removed; update _JOB_ID rather than deleting this regression."
    )


def test_guard_job_timeout_is_above_starvation_floor() -> None:
    """OMN-15765: the guard's own budget must outlive its checkout+setup-uv preamble.

    Red-before control: this assertion fails against the pre-fix
    ``timeout-minutes: 5`` (the value observed causing 12/17 false D-cluster
    reds) and passes against the fixed ``timeout-minutes: 45``.
    """
    doc = _load_ci_workflow()
    job = doc["jobs"][_JOB_ID]
    timeout = job.get("timeout-minutes")
    assert timeout is not None, f"{_JOB_ID!r} must declare an explicit timeout-minutes"
    assert timeout >= _MIN_SAFE_TIMEOUT_MINUTES, (
        f"{_JOB_ID!r} timeout-minutes={timeout} is below the observed "
        f"checkout+setup-uv starvation floor ({_MIN_SAFE_TIMEOUT_MINUTES}m) — "
        "this reintroduces the OMN-15765 false-fail (cancelled preamble scored "
        "as an authz REJECT by CI Summary's fail-closed comparison)."
    )


def test_guard_job_checkout_and_setup_uv_preamble_unchanged() -> None:
    """The fix must not touch the guard's decision-logic preamble/invocation.

    Guards against a future edit conflating "raise the timeout" with "narrow
    the guard's matched inputs" — the sanctioned fix for OMN-15765 is a
    scheduling change only; the ``check-bot-authored-authz-guard`` CLI
    invocation and its ``fetch-depth: 0`` checkout are unchanged.
    """
    doc = _load_ci_workflow()
    job = doc["jobs"][_JOB_ID]
    steps = job.get("steps", [])
    step_names = [s.get("name", "") for s in steps]
    assert any("Checkout code" in n for n in step_names)
    assert any("check-bot-authored-authz-guard" in (s.get("run") or "") for s in steps)
