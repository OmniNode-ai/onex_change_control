# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15484: the Merge Hold Gate must be wired into a REQUIRED context here.

Four of the incidents in OMN-15483's table — the merge sweep landing a PR while
adversarial verification was still running against it — happened in THIS
repository (OCC#5588, OCC#5586, OCC#5530/#5531, OCC#5584), every one of them
with all required contexts green, because the gate did not exist here. The fan-
out adds one ``merge-hold-gate`` job calling the shared reusable workflow in
omnimarket, plus a strict success-only check in ``ci-summary``.

Two things are proven here, and they are different in kind:

* **The strict registration behaves as claimed** — by loading the SHIPPED
  ``scripts/ci/ci_summary_gate.py`` and EXECUTING its real ``evaluate()``
  against synthetic job snapshots (OMN-15768: this replaced the needs-based
  ``ci-summary`` bash block a retired version of this class rendered and
  executed under bash). Registration, not existence, is the mechanism: an
  unregistered job is invisible to ``evaluate()``'s strict check, and a
  skipped hold gate is indistinguishable from no hold gate at all. An
  assertion that a name appears somewhere in the file would not distinguish a
  working guard from a typo'd entry that never matches a real job name.
* **The anti-removal anchor fires on each removal vector** — the wiring
  validator is driven against mutated copies of the real ``ci.yml`` (and a
  mutated copy of ``ci_summary_gate.py``), not against hand-built fixtures,
  so a change to either file's shape cannot leave these tests passing against
  a form that no longer exists.

Not proven here, deliberately: the hold vocabulary itself, and that a held title
produces exit 1. Those live in omnimarket (one definition, fleet-wide) and are
re-proven live in THIS repo's CI on every run by the reusable workflow's own
self-proof step. Asserting them here would require a local copy of the
vocabulary, which is exactly what AC1 forbids.
"""

from __future__ import annotations

import copy
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_YAML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_PRECOMMIT_CONFIG = _REPO_ROOT / ".pre-commit-config.yaml"
_JOB_ID = "merge-hold-gate"
_SUMMARY_JOB_ID = "ci-summary"
_EXPECTED_CONTEXT = "merge-hold-gate / evaluate"


def _load_wiring_module() -> Any:
    """Load the validator by path.

    ``scripts`` is on ``mypy_path`` but ``scripts/validation`` is not, so a bare
    import type-checks locally and fails ``mypy src/ tests/`` in CI with
    ``import-not-found``. Every other test exercising a ``scripts/validation``
    module uses this loader for the same reason.
    """
    script_path = (
        _REPO_ROOT / "scripts" / "validation" / "check_merge_hold_gate_wiring.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_merge_hold_gate_wiring", script_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ci_yaml() -> dict[str, Any]:
    return dict(yaml.safe_load(_CI_YAML.read_text(encoding="utf-8")))


def _write_ci(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "ci.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _load_gate_module() -> Any:
    """Load scripts/ci/ci_summary_gate.py by path (same reason as
    ``_load_wiring_module`` below: ``scripts/ci`` is not on mypy_path).

    Registers the module in ``sys.modules`` before executing it -- Python
    3.13's ``@dataclass`` decorator (used by ``JobState`` in the target
    module) resolves its owning module via ``sys.modules[cls.__module__]``
    at class-definition time, which is empty until this happens.
    """
    script_path = _REPO_ROOT / "scripts" / "ci" / "ci_summary_gate.py"
    spec = importlib.util.spec_from_file_location("ci_summary_gate", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# AC2 — strict registration, proven by EXECUTING the real evaluate() logic
# ---------------------------------------------------------------------------
#
# OMN-15768 replaced ci-summary's needs-based bash block (rendered and
# executed under bash by the retired version of this class) with an
# in-Python poller: scripts/ci/ci_summary_gate.py's evaluate() IS the
# mechanism now, so registration is proven by calling it directly with
# synthetic job snapshots, not by rendering YAML into bash.


def _job(name: str, conclusion: str) -> dict[str, object]:
    return {"name": name, "status": "completed", "conclusion": conclusion}


def _all_green_jobs(gate: Any) -> list[dict[str, object]]:
    return [
        _job(n, "success") for n in gate.STRICT_GATE_JOBS + gate.SKIPPABLE_GATE_JOBS
    ]


class TestStrictRegistrationIsExecutable:
    """Registration is the mechanism. Proven by running it, not by grepping."""

    def test_success_passes(self) -> None:
        gate = _load_gate_module()
        code, _ = gate.evaluate(_all_green_jobs(gate))
        assert code == gate.EXIT_SUCCESS

    @pytest.mark.parametrize("result", ["skipped", "failure", "cancelled"])
    def test_every_non_success_result_fails_the_required_context(
        self, result: str
    ) -> None:
        """``skipped`` is the one that matters and the one a naive rollup misses.

        ``failure`` and ``cancelled`` are also caught by the L3 default-deny
        sweep even for an unregistered job; ``skipped`` is caught ONLY by
        STRICT_GATE_JOBS registration, and a skipped hold gate is
        indistinguishable from no hold gate: the PR is required-green while
        holding nothing.
        """
        gate = _load_gate_module()
        jobs = [j for j in _all_green_jobs(gate) if j["name"] != _EXPECTED_CONTEXT]
        jobs.append(_job(_EXPECTED_CONTEXT, result))
        code, _ = gate.evaluate(jobs)
        assert code == gate.EXIT_FAILURE

    def test_skipped_specifically_names_the_hold_gate(self) -> None:
        """The failure must be diagnosable, not a generic rollup failure."""
        gate = _load_gate_module()
        jobs = [j for j in _all_green_jobs(gate) if j["name"] != _EXPECTED_CONTEXT]
        jobs.append(_job(_EXPECTED_CONTEXT, "skipped"))
        _, report = gate.evaluate(jobs)
        assert _EXPECTED_CONTEXT in report

    def test_unregistered_hold_gate_makes_a_skip_pass(self) -> None:
        """RED-before control: the pre-fan-out state of this repo.

        A job that is NOT in STRICT_GATE_JOBS/SKIPPABLE_GATE_JOBS and is not
        otherwise present+failing is invisible to evaluate() -- this is
        exactly the bypass OMN-15484 exists to close, and it is the control
        that makes the assertions above non-vacuous.
        """
        gate = _load_gate_module()
        strict = tuple(n for n in gate.STRICT_GATE_JOBS if n != _EXPECTED_CONTEXT)
        jobs = [_job(n, "success") for n in strict + gate.SKIPPABLE_GATE_JOBS]
        jobs.append(_job(_EXPECTED_CONTEXT, "skipped"))
        code, _ = gate.evaluate(jobs, strict_gates=strict)
        assert code == gate.EXIT_SUCCESS, (
            "expected the UNREGISTERED shape to pass on a skipped hold gate -- "
            "if it fails, this control proves nothing about the registration"
        )

    def test_the_hold_gate_is_registered(self) -> None:
        """Without this, ci-summary can report green before the gate rules."""
        gate = _load_gate_module()
        assert _EXPECTED_CONTEXT in gate.STRICT_GATE_JOBS


# ---------------------------------------------------------------------------
# AC4 / AC1 / AC5 — the job's own shape
# ---------------------------------------------------------------------------


class TestHoldGateJobShape:
    @staticmethod
    def _job() -> dict[str, Any]:
        jobs = _ci_yaml()["jobs"]
        assert _JOB_ID in jobs, f"{_JOB_ID} is not a job in ci.yml"
        return dict(jobs[_JOB_ID])

    def test_the_job_is_unconditional(self) -> None:
        """AC4: an unrelated upstream failure must not be able to skip it."""
        job = self._job()
        assert "needs" not in job
        assert "if" not in job

    def test_the_job_calls_the_shared_gate_and_declares_no_vocabulary(self) -> None:
        """AC1: no local re-implementation, no local token list."""
        uses = self._job()["uses"]
        assert uses.startswith(
            "OmniNode-ai/omnimarket/.github/workflows/merge-hold-gate-reusable.yml@"
        )
        assert "steps" not in self._job(), (
            "a reusable-workflow caller has no steps; local steps would mean a "
            "second implementation of the gate lives in this repo"
        )

    def test_no_hold_vocabulary_is_declared_anywhere_in_this_repo(self) -> None:
        """The AC1 property this repo is responsible for, asserted locally too.

        The reusable workflow scans for this on every CI run, which is the
        enforcing surface. This test is the fast local echo of it, and it is
        written WITHOUT a token list — it asserts the absence of a re-compiled
        hold regex by NAME, the same identifier rule omnimarket's own falsifier
        uses, so this file does not itself become the second vocabulary.
        """
        offenders: list[str] = []
        for root in (_REPO_ROOT / "src", _REPO_ROOT / "scripts"):
            if not root.is_dir():
                continue
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8", errors="ignore")
                for line in text.splitlines():
                    normalized = line.upper().replace("-", "_")
                    if "RE.COMPILE" not in normalized.replace(" ", ""):
                        continue
                    if any(
                        fragment in normalized
                        for fragment in ("DO_NOT_MERGE", "HOLD_MARKER")
                    ):
                        offenders.append(f"{path.relative_to(_REPO_ROOT)}: {line!r}")
        assert offenders == [], (
            "a hold vocabulary is declared in this repository; it must live "
            f"only in omnimarket and be read through the shared gate: {offenders}"
        )

    def test_the_declared_context_matches_what_github_will_mint(self) -> None:
        """AC5 seam: this string is validated in ANOTHER repo.

        ``context_name`` is handed to the reusable workflow, which checks it
        against the canonical vocabulary. If it is not the context GitHub
        actually produces (``<caller job id> / <inner job id>``), the remote
        guard validates a name that does not exist.
        """
        assert self._job()["with"]["context_name"] == _EXPECTED_CONTEXT
        assert _EXPECTED_CONTEXT.startswith(f"{_JOB_ID} / ")

    def test_the_pin_is_not_a_feature_branch(self) -> None:
        """A feature-branch pin wedges this repo when the branch is deleted."""
        ref = self._job()["uses"].split("@", 1)[1]
        assert ref in {"dev", "main"} or re.fullmatch(r"[0-9a-f]{40}", ref), (
            f"the shared gate is pinned at {ref!r}; use a 40-hex SHA or a "
            "mainline ref so a squash-merge branch deletion cannot break CI here"
        )


# ---------------------------------------------------------------------------
# The anti-removal anchor fires on each removal vector
# ---------------------------------------------------------------------------


class TestWiringValidator:
    """Driven against MUTATED COPIES of the real ci.yml, not fixtures."""

    def test_the_real_ci_yaml_passes(self) -> None:
        module = _load_wiring_module()
        assert module.check_wiring(_CI_YAML) == []

    def test_a_missing_job_is_caught(self, tmp_path: Path) -> None:
        data = copy.deepcopy(_ci_yaml())
        del data["jobs"][_JOB_ID]
        failures = _load_wiring_module().check_wiring(_write_ci(tmp_path, data))
        assert any("is absent" in f for f in failures), failures

    def test_a_needs_chain_is_caught(self, tmp_path: Path) -> None:
        data = copy.deepcopy(_ci_yaml())
        data["jobs"][_JOB_ID]["needs"] = ["pre-commit"]
        failures = _load_wiring_module().check_wiring(_write_ci(tmp_path, data))
        assert any("needs:" in f for f in failures), failures

    def test_an_if_condition_is_caught(self, tmp_path: Path) -> None:
        data = copy.deepcopy(_ci_yaml())
        data["jobs"][_JOB_ID]["if"] = "github.event_name == 'pull_request'"
        failures = _load_wiring_module().check_wiring(_write_ci(tmp_path, data))
        assert any("`if:`" in f for f in failures), failures

    def test_a_local_reimplementation_is_caught(self, tmp_path: Path) -> None:
        """Swapping the shared call for local steps is the AC1 regression."""
        data = copy.deepcopy(_ci_yaml())
        del data["jobs"][_JOB_ID]["uses"]
        del data["jobs"][_JOB_ID]["with"]
        data["jobs"][_JOB_ID]["runs-on"] = "ubuntu-latest"
        data["jobs"][_JOB_ID]["steps"] = [{"run": "python3 my_own_hold_check.py"}]
        failures = _load_wiring_module().check_wiring(_write_ci(tmp_path, data))
        assert any("does not call the shared gate" in f for f in failures), failures

    def test_a_feature_branch_pin_is_caught(self, tmp_path: Path) -> None:
        data = copy.deepcopy(_ci_yaml())
        base = data["jobs"][_JOB_ID]["uses"].split("@", 1)[0]
        data["jobs"][_JOB_ID]["uses"] = f"{base}@jonah/some-feature-branch"
        failures = _load_wiring_module().check_wiring(_write_ci(tmp_path, data))
        assert any("pins the shared gate at ref" in f for f in failures), failures

    def test_a_wrong_context_name_is_caught(self, tmp_path: Path) -> None:
        data = copy.deepcopy(_ci_yaml())
        data["jobs"][_JOB_ID]["with"]["context_name"] = "something / else"
        failures = _load_wiring_module().check_wiring(_write_ci(tmp_path, data))
        assert any("context GitHub mints" in f for f in failures), failures

    def test_ci_summary_declaring_needs_is_caught(self, tmp_path: Path) -> None:
        """OMN-15768: a `needs:` on ci-summary is now a REGRESSION to the
        needs-graph-omission bug class (OCC#6346), the opposite of what this
        anchor used to require."""
        data = copy.deepcopy(_ci_yaml())
        data["jobs"][_SUMMARY_JOB_ID]["needs"] = [_JOB_ID]
        failures = _load_wiring_module().check_wiring(_write_ci(tmp_path, data))
        assert any("declares `needs:`" in f for f in failures), failures

    def test_job_not_registered_in_strict_gate_jobs_is_caught(
        self, tmp_path: Path
    ) -> None:
        """The half-removal that leaves everything looking wired: the job is
        present, unconditional, and correctly shaped, but its display name is
        no longer registered in ci_summary_gate.py's STRICT_GATE_JOBS."""
        gate_source = (_REPO_ROOT / "scripts" / "ci" / "ci_summary_gate.py").read_text(
            encoding="utf-8"
        )
        mutated = gate_source.replace('"merge-hold-gate / evaluate",\n', "")
        assert mutated != gate_source, "the registration line was not found to strip"
        gate_path = tmp_path / "ci_summary_gate.py"
        gate_path.write_text(mutated, encoding="utf-8")
        failures = _load_wiring_module().check_wiring(
            _CI_YAML, gate_module_path=gate_path
        )
        assert any("STRICT_GATE_JOBS" in f for f in failures), failures

    def test_a_missing_file_is_not_a_pass(self, tmp_path: Path) -> None:
        """Exit-0-on-missing is the shape the fail-loud meta-gate forbids."""
        module = _load_wiring_module()
        with pytest.raises(module.CiWorkflowUnreadableError):
            module.check_wiring(tmp_path / "nope.yml")

    def test_help_exits_zero(self) -> None:
        """The Pre-commit job runs every changed validator with --help."""
        assert _load_wiring_module().main(["--help"]) == 0


class TestValidatorIsWiredAsAHook:
    """A validator no hook executes is advisory (CLAUDE.md rule 5)."""

    def test_the_precommit_hook_exists_and_is_scoped_to_ci_yaml(self) -> None:
        config = yaml.safe_load(_PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
        hooks = [
            hook
            for repo in config["repos"]
            for hook in repo.get("hooks", [])
            if hook.get("id") == "check-merge-hold-gate-wiring"
        ]
        assert len(hooks) == 1, "expected exactly one wiring hook"
        hook = hooks[0]
        assert "check_merge_hold_gate_wiring.py" in hook["entry"]
        assert hook["files"] == r"^\.github/workflows/ci\.yml$"
        assert hook["pass_filenames"] is True
