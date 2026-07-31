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

* **The strict registration behaves as claimed** — by RENDERING the real
  ``ci-summary`` shell block out of the shipped ``ci.yml`` and EXECUTING it
  under bash for each possible job result. Registration, not existence, is the
  mechanism: OCC's ``ci-summary`` rollup is
  ``contains(needs.*.result, 'failure') || contains(needs.*.result,
  'cancelled')``, which passes on a SKIPPED need. An assertion that the string
  appears in the file would not distinguish a working guard from a typo'd
  ``needs.merge_hold_gate`` (underscores) that silently evaluates to empty.
* **The anti-removal anchor fires on each removal vector** — the wiring
  validator is driven against mutated copies of the real ``ci.yml``, not
  against hand-built fixtures, so a change to the workflow's shape cannot leave
  these tests passing against a file that no longer exists in that form.

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
import subprocess
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


# ---------------------------------------------------------------------------
# AC2 — strict registration, proven by EXECUTING the real ci-summary block
# ---------------------------------------------------------------------------

_NEEDS_RESULT_RE = re.compile(r"\$\{\{\s*needs\.([A-Za-z0-9_\-]+)\.result\s*\}\}")
_CONTAINS_ROLLUP_RE = re.compile(
    r"\$\{\{\s*contains\(needs\.\*\.result,\s*'failure'\)\s*\|\|\s*"
    r"contains\(needs\.\*\.result,\s*'cancelled'\)\s*\}\}"
)

# The shipped strict success-only block for the hold gate, matched so the
# removal controls below can strip exactly it and nothing else.
_STRICT_CHECK_BLOCK_RE = re.compile(
    r"\n *if \[\[ \"\$\{\{ needs\." + _JOB_ID + r"\.result \}\}\".*?\n *fi",
    re.DOTALL,
)


def _render_ci_summary_script(results: dict[str, str]) -> str:
    """Render the SHIPPED ci-summary step script with concrete job results.

    GitHub evaluates ``${{ ... }}`` before bash ever sees the script, so
    reproducing the real behaviour means substituting first and running the
    result. Two expression shapes appear in this step:

    * ``${{ needs.<job>.result }}`` — replaced with that job's result. A job not
      named in ``results`` renders as the empty string, which is what GitHub
      produces for a need that does not exist. That is deliberate: it is how a
      typo'd job name in the guard shows up as a silently passing check.
    * the generic ``contains(needs.*.result, 'failure') || contains(...,
      'cancelled')`` rollup — computed from ``results``.

    Args:
        results: Job id -> result (``success``/``skipped``/``failure``/
            ``cancelled``).

    Returns:
        A bash script, ready to execute.
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


def _run_summary(hold_result: str) -> subprocess.CompletedProcess[str]:
    """Execute the real ci-summary block with every other gate green."""
    results = dict.fromkeys(_all_needs(), "success")
    results[_JOB_ID] = hold_result
    return subprocess.run(
        ["bash", "-c", _render_ci_summary_script(results)],
        capture_output=True,
        text=True,
        check=False,
    )


class TestStrictRegistrationIsExecutable:
    """Registration is the mechanism. Proven by running it, not by grepping."""

    def test_success_passes(self) -> None:
        completed = _run_summary("success")
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "All required jobs passed" in completed.stdout

    @pytest.mark.parametrize("result", ["skipped", "failure", "cancelled"])
    def test_every_non_success_result_fails_the_required_context(
        self, result: str
    ) -> None:
        """``skipped`` is the one that matters and the one a rollup misses.

        ``failure`` and ``cancelled`` are already caught by the generic rollup.
        ``skipped`` is caught ONLY by the explicit success-only check, and a
        skipped hold gate is indistinguishable from no hold gate: the PR is
        required-green while holding nothing.
        """
        completed = _run_summary(result)
        assert completed.returncode == 1, completed.stdout + completed.stderr

    def test_skipped_specifically_names_the_hold_gate(self) -> None:
        """The failure must be diagnosable, not a generic rollup failure."""
        completed = _run_summary("skipped")
        assert _JOB_ID in completed.stdout
        assert "OMN-15484" in completed.stdout

    def test_removing_the_strict_check_makes_a_skip_pass(self) -> None:
        """RED-before, against the real file: the pre-fan-out state of this repo.

        Deleting only the explicit check — leaving the job present and in
        ``needs:`` — restores exactly the bypass this ticket exists to close,
        and the generic rollup still reports success. This is the control that
        makes the assertions above non-vacuous.
        """
        summary = _ci_yaml()["jobs"][_SUMMARY_JOB_ID]
        script = "\n".join(
            step["run"] for step in summary["steps"] if isinstance(step.get("run"), str)
        )
        stripped = _STRICT_CHECK_BLOCK_RE.sub("", script)
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
            "expected the UNREGISTERED shape to pass on a skipped hold gate — "
            "if it fails, this control proves nothing about the registration"
        )

    def test_the_hold_gate_is_in_ci_summary_needs(self) -> None:
        """Without this, ci-summary can report green before the gate rules."""
        assert _JOB_ID in _all_needs()


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

    def test_dropping_it_from_ci_summary_needs_is_caught(self, tmp_path: Path) -> None:
        data = copy.deepcopy(_ci_yaml())
        data["jobs"][_SUMMARY_JOB_ID]["needs"] = [
            n for n in data["jobs"][_SUMMARY_JOB_ID]["needs"] if n != _JOB_ID
        ]
        failures = _load_wiring_module().check_wiring(_write_ci(tmp_path, data))
        assert any("does not list" in f for f in failures), failures

    def test_dropping_the_strict_check_is_caught(self, tmp_path: Path) -> None:
        """The half-removal that leaves everything looking wired."""
        data = copy.deepcopy(_ci_yaml())
        steps = data["jobs"][_SUMMARY_JOB_ID]["steps"]
        for step in steps:
            if isinstance(step.get("run"), str):
                step["run"] = _STRICT_CHECK_BLOCK_RE.sub("", step["run"])
        failures = _load_wiring_module().check_wiring(_write_ci(tmp_path, data))
        assert any("no strict success-only check" in f for f in failures), failures

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
