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

* **The registration behaves as claimed** — by loading the SHIPPED
  ``scripts/ci/ci_summary_gate.py`` and EXECUTING its real ``evaluate()``
  against synthetic job snapshots (OMN-15768: this replaced the needs-based
  ``ci-summary`` bash block a retired version of this class rendered and
  executed under bash). ``test``/``type-check`` are SKIPPABLE_GATE_JOBS
  entries (success OR skipped both pass) rather than STRICT, because they
  carry a LEGITIMATE skip path (the OMN-14098 docs-only fast lane) -- the
  design no longer needs the gate to independently re-derive ``docs_only``
  at evaluate() time, because ``TestJobsHaveNoDevBaseRefCarveOut`` below
  proves BY SOURCE INSPECTION that the job's own ``if:`` in ci.yml can
  produce ``skipped`` for no reason other than ``docs_only == 'true'``. A
  ``failure``/``cancelled`` result, or an ABSENT job, still fails closed.
* **The anti-removal control fires**: a job dropped from
  SKIPPABLE_GATE_JOBS entirely becomes invisible to the completeness anchor
  the moment it is also absent from the run (e.g. renamed) -- this is the
  control that makes registration non-decorative.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_YAML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_TEST_NAME = "Tests"  # ci_summary_gate.py SKIPPABLE_GATE_JOBS display name
_TYPE_CHECK_NAME = "Type Check"


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


class TestStrictRegistrationIsExecutable:
    """Registration is the mechanism. Proven by running it, not by grepping."""

    def test_both_success_passes(self) -> None:
        gate = _load_gate_module()
        code, _ = gate.evaluate(_all_green_jobs(gate))
        assert code == gate.EXIT_SUCCESS

    def test_both_skipped_is_the_legitimate_docs_only_fast_lane(self) -> None:
        """SKIPPABLE_GATE_JOBS tolerates a skip unconditionally at the
        evaluate() layer; TestJobsHaveNoDevBaseRefCarveOut below is what
        proves that skip can only happen for a docs_only PR in the first
        place, so this remains safe."""
        gate = _load_gate_module()
        jobs = [
            j
            for j in _all_green_jobs(gate)
            if j["name"] not in (_TEST_NAME, _TYPE_CHECK_NAME)
        ]
        jobs.append(_job(_TEST_NAME, "skipped"))
        jobs.append(_job(_TYPE_CHECK_NAME, "skipped"))
        code, _ = gate.evaluate(jobs)
        assert code == gate.EXIT_SUCCESS

    @pytest.mark.parametrize("result", ["failure", "cancelled"])
    def test_test_job_non_success_fails_closed(self, result: str) -> None:
        gate = _load_gate_module()
        jobs = [j for j in _all_green_jobs(gate) if j["name"] != _TEST_NAME]
        jobs.append(_job(_TEST_NAME, result))
        code, _ = gate.evaluate(jobs)
        assert code == gate.EXIT_FAILURE

    @pytest.mark.parametrize("result", ["failure", "cancelled"])
    def test_type_check_job_non_success_fails_closed(self, result: str) -> None:
        gate = _load_gate_module()
        jobs = [j for j in _all_green_jobs(gate) if j["name"] != _TYPE_CHECK_NAME]
        jobs.append(_job(_TYPE_CHECK_NAME, result))
        code, _ = gate.evaluate(jobs)
        assert code == gate.EXIT_FAILURE

    def test_an_absent_unregistered_test_job_is_invisible(self) -> None:
        """RED-before control: the pre-fix state of this repo.

        A renamed/absent job that is NOT in STRICT_GATE_JOBS/
        SKIPPABLE_GATE_JOBS is invisible to the completeness anchor -- the
        default-deny sweep only inspects jobs that actually appear in the
        run, so a `test` that silently stops running (renamed, deleted, or
        never wired) reads as SUCCESS rather than PENDING. This is the
        control that makes registration non-decorative.
        """
        gate = _load_gate_module()
        strict = gate.STRICT_GATE_JOBS
        skippable = tuple(n for n in gate.SKIPPABLE_GATE_JOBS if n != _TEST_NAME)
        jobs = [_job(n, "success") for n in strict + skippable]
        code, _ = gate.evaluate(jobs, strict_gates=strict, skippable_gates=skippable)
        assert code == gate.EXIT_SUCCESS, (
            "expected the UNREGISTERED+ABSENT shape to pass -- if it fails, "
            "this control proves nothing about the registration"
        )

    def test_test_and_type_check_are_registered(self) -> None:
        gate = _load_gate_module()
        assert _TEST_NAME in gate.SKIPPABLE_GATE_JOBS
        assert _TYPE_CHECK_NAME in gate.SKIPPABLE_GATE_JOBS


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
