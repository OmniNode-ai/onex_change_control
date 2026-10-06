# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Gate-validator-ref enforcement tests (OMN-15706, amended by OMN-20001).

OMN-20001 supersedes the R3a "resolve from the PR base branch" rule below: the
omnibase_core / omnibase_compat checkouts now read the version this repo has
PINNED (the tag of the omnibase-core version in uv.lock; a recorded compat
tag), never a sibling's live branch, so a merge in a sibling cannot turn a gate
red. The structural rule (every checkout consumes a step output, never a
literal) is unchanged; the liveness tests below now prove the ref follows
uv.lock and ignores the PR base ref. The text from here down is the original
R3a rationale, kept for the history of the two mutations.

Ruling R3a (OMN-15689) forbids ANY hardcoded gate-validator ref (@main, @dev, or
a literal SHA) anywhere in OCC's gate workflows -- the omnibase_core /
omnibase_compat checkout ref consumed by the receipt-gate and OCC-eligibility
validators must be resolved dynamically, at runtime, from the PR's own base
branch (or the merge_group / push / workflow_dispatch equivalent).

The pre-existing shape assertions in test_call_receipt_gate_workflow_omn10415.py
and test_occ_preflight_gate_omn10485.py only check that a `validator_ref` step
*exists* and mentions the right env vars -- they do not check that:

  (a) every omnibase_core / omnibase_compat checkout actually *consumes* that
      step's output (a hardcoded `ref: main` swapped in at the checkout site
      passes the existing shape assertions unchanged), or
  (b) the `validator_ref` step's own resolution logic is live (a hardcoded
      `resolved_ref="main"` inside the step, with the real PR_BASE_REF /
      MERGE_GROUP_BASE_REF logic dead-coded behind `if false; then`, still
      satisfies "mentions PR_BASE_REF" and "contains ref=${resolved_ref}").

Two proven mutation counterexamples (2026-08-05 adversarial verify, ledger
line 12545) pass the pre-existing tests green:

  Mutation A: `ref: ${{ steps.validator_ref.outputs.ref }}` -> `ref: main` on
              the omnibase_core / omnibase_compat checkout steps.
  Mutation B: hardcode `resolved_ref="main"` inside the validator_ref step,
              dead-code the real resolution behind `if false; then`, keep the
              PR_BASE_REF / MERGE_GROUP_BASE_REF env-var mentions and the
              `echo "ref=..."` line.

This file makes both mutations fail while dev-tip passes:
  - `test_no_hardcoded_ref_in_gate_dependency_checkouts` (structural, kills A)
  - `test_validator_ref_resolution_is_live_for_pr_base_ref` and
    `test_validator_ref_resolution_is_live_for_merge_group_base_ref`
    (subshell execution against a sentinel branch name, kills B)

Scope (operator ruling 2026-08-04, OMN-15689 comment 70b00b79 / cae2bf98):
R3a covers exactly the gate-validator-ref class -- the omnibase_core /
omnibase_compat checkouts consumed by the receipt-gate and OCC-eligibility
validators in call-receipt-gate.yml, call-occ-preflight.yml, ci.yml's
append-only-gate job (honesty-gate until OMN-20136), and
validate-validator-requirements.yml.
The 10-item cross-repo reusable-workflow (`uses:`) pin inventory documented in
that ticket (omniclaude zone-filter/skip-guard callers, omnibase_core
zone-filter/validate-docs callers, the omnimarket merge-hold-gate pin, and the
ci.yml:1329 OMN-14505 provenance-anchor pin) is explicitly SANCTIONED and out
of R3a's blast radius -- this file does not touch or flag those.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

WORKFLOWS_DIR = Path(".github/workflows")

# (workflow file, job key) pairs that carry an in-scope `validator_ref` step,
# per the R3a-scoped inventory above. Each entry names exactly one job.
# ci.yml's honesty-gate left this inventory in OMN-20136: it no longer checks
# out omnibase_core at all -- its scanner is the uv.lock-pinned registry
# distribution (see test_honesty_gate_has_no_live_core_checkout below).
GATE_JOBS: list[tuple[str, str]] = [
    ("call-receipt-gate.yml", "verify"),
    ("call-occ-preflight.yml", "occ-preflight"),
    ("validate-validator-requirements.yml", "validate-validator-requirements"),
    ("ci.yml", "append-only-gate"),
]

# Cross-repo checkouts in scope for the "no literal ref" structural check --
# the dependency repos the gate validators are installed from. Deliberately
# does NOT include the onex_change_control self-checkout fallback in
# call-receipt-gate.yml (`resolve_occ_default_branch`), which resolves via a
# live GitHub-API call to the repo's own default branch rather than a
# validator_ref step, and is unreachable for onex_change_control's own PRs
# (invariant I3) in any case.
GATE_DEPENDENCY_REPOS = {
    "OmniNode-ai/omnibase_core",
    "OmniNode-ai/omnibase_compat",
}

# A ref value is "live" only if it is a GitHub Actions expression that reads
# a validator_ref-family step's `ref` output. Anything else -- a bare branch
# name, a bare SHA, or an expression that does NOT reference a step output --
# is treated as a hardcoded literal for the purpose of this ratchet.
_LIVE_REF_EXPRESSION_MARKERS = ("steps.", ".outputs.")


def _load_workflow(path: Path) -> dict[str, Any]:
    loaded = cast("dict[Any, Any]", yaml.safe_load(path.read_text(encoding="utf-8")))
    if "on" not in loaded and True in loaded:
        loaded["on"] = loaded[True]
    return cast("dict[str, Any]", loaded)


def _iter_gate_jobs() -> list[tuple[Path, str, dict[str, Any]]]:
    """Yield (workflow_path, job_key, job_dict) for every in-scope gate job."""
    out: list[tuple[Path, str, dict[str, Any]]] = []
    seen_files: dict[str, dict[str, Any]] = {}
    for filename, job_key in GATE_JOBS:
        if filename not in seen_files:
            seen_files[filename] = _load_workflow(WORKFLOWS_DIR / filename)
        workflow = seen_files[filename]
        assert job_key in workflow["jobs"], (
            f"{filename}: expected job '{job_key}' — "
            "R3a-scoped gate job inventory is stale"
        )
        out.append((WORKFLOWS_DIR / filename, job_key, workflow["jobs"][job_key]))
    return out


def _is_live_ref_expression(ref_value: Any) -> bool:
    if not isinstance(ref_value, str):
        return False
    return all(marker in ref_value for marker in _LIVE_REF_EXPRESSION_MARKERS)


# ---------------------------------------------------------------------------
# Inventory sanity: fail loudly if a new validator_ref step or a new
# omnibase_core/omnibase_compat checkout appears in a gate workflow without
# being added to the scoped inventories above, rather than silently not
# covering it.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_gate_job_inventory_has_no_unaccounted_validator_ref_steps() -> None:
    """Every `validator_ref`-id step in a gate-caller workflow must be in GATE_JOBS."""
    for filename in {f for f, _ in GATE_JOBS}:
        workflow = _load_workflow(WORKFLOWS_DIR / filename)
        accounted_job_keys = {jk for f, jk in GATE_JOBS if f == filename}
        for job_key, job in workflow["jobs"].items():
            step_ids = {step.get("id") for step in job.get("steps", [])}
            if "validator_ref" in step_ids:
                assert job_key in accounted_job_keys, (
                    f"{filename}: job '{job_key}' has a validator_ref step not "
                    "covered by this test's GATE_JOBS inventory — add it before "
                    "landing (a new gate-validator-ref site with no enforcement "
                    "coverage is exactly the gap R3a exists to close)."
                )


@pytest.mark.unit
def test_honesty_gate_has_no_live_core_checkout() -> None:
    """OMN-20136: honesty-gate runs the uv.lock-pinned scanner, not a core checkout."""
    job = _load_workflow(WORKFLOWS_DIR / "ci.yml")["jobs"]["honesty-gate"]
    for step in job.get("steps", []):
        repo = (step.get("with") or {}).get("repository")
        assert repo not in GATE_DEPENDENCY_REPOS, (
            f"ci.yml honesty-gate checks out {repo}; the receipt-honesty scanner "
            "must come from the OCC uv.lock (OMN-20136), not a live checkout."
        )
        assert "omnibase_core_source" not in str(step.get("env") or {}), (
            "ci.yml honesty-gate points PYTHONPATH at a core source checkout"
        )
    assert str((job.get("env") or {}).get("UV_LOCKED")) == "1", (
        "ci.yml honesty-gate must set UV_LOCKED=1 so a stale lock fails closed"
    )


# ---------------------------------------------------------------------------
# (a) Structural: every omnibase_core / omnibase_compat checkout in a gate
# job must consume a step-output expression, never a literal ref. Kills
# Mutation A.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_hardcoded_ref_in_gate_dependency_checkouts() -> None:
    checkout_sites: list[tuple[str, str, dict[str, Any]]] = []
    for wf_path, job_key, job in _iter_gate_jobs():
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("actions/checkout@"):
                with_block = step.get("with", {}) or {}
                repository = with_block.get("repository")
                if repository in GATE_DEPENDENCY_REPOS:
                    checkout_sites.append((str(wf_path), job_key, with_block))

    assert checkout_sites, (
        "no omnibase_core/omnibase_compat checkout steps found in the "
        "R3a-scoped gate jobs — inventory drift, investigate before trusting "
        "this ratchet"
    )

    for wf_path_str, job_key, with_block in checkout_sites:
        ref_value = with_block.get("ref")
        repository = with_block["repository"]
        assert _is_live_ref_expression(ref_value), (
            f"{wf_path_str} job '{job_key}': checkout of {repository} has ref="
            f"{ref_value!r}, which is not a live step-output expression. "
            "R3a forbids ANY hardcoded gate-validator ref (@main, @dev, or a "
            "literal SHA) — the ref must be "
            "'${{ steps.validator_ref.outputs.ref }}' (or equivalent step "
            "output), never a literal."
        )


# ---------------------------------------------------------------------------
# (b) Live execution: run each validator_ref step's bash body in a subshell
# harness with a sentinel branch name and assert the emitted ref equals the
# sentinel. Kills Mutation B (a hardcoded resolved_ref ignores the sentinel).
# ---------------------------------------------------------------------------


def _extract_validator_ref_step(workflow_path: Path, job_key: str) -> dict[str, Any]:
    workflow = _load_workflow(workflow_path)
    job = workflow["jobs"][job_key]
    step_by_id = {step["id"]: step for step in job["steps"] if "id" in step}
    assert "validator_ref" in step_by_id, (
        f"{workflow_path} job '{job_key}': no step with id 'validator_ref'"
    )
    return cast("dict[str, Any]", step_by_id["validator_ref"])


def _run_validator_ref_script(
    step: dict[str, Any], env_overrides: dict[str, str], lock_text: str
) -> str:
    """Execute the validator_ref step's `run:` body in a bash subshell.

    Runs in a temp dir holding `uv.lock` (as the PR-head checkout does).
    `$GITHUB_OUTPUT` is a real temp file; declared step `env:` entries keep
    their declared value unless overridden.
    """
    script = step["run"]
    with tempfile.TemporaryDirectory() as tmpdir:
        github_output = Path(tmpdir) / "github_output"
        github_output.write_text("", encoding="utf-8")
        (Path(tmpdir) / "uv.lock").write_text(lock_text, encoding="utf-8")

        run_env = dict(os.environ)
        run_env.update({k: str(v) for k, v in step.get("env", {}).items()})
        run_env.update(env_overrides)
        run_env["GITHUB_OUTPUT"] = str(github_output)

        result = subprocess.run(
            ["bash", "-c", script],
            env=run_env,
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, (
            f"validator_ref script exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        return github_output.read_text(encoding="utf-8")


_LOCK_TEMPLATE = (
    '[[package]]\nname = "omnibase-compat"\nversion = "9.9.9"\n\n'
    '[[package]]\nname = "omnibase-core"\nversion = "{version}"\n'
    'source = {{ registry = "https://pypi.org/simple" }}\n'
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filename", "job_key"), GATE_JOBS, ids=[f"{f}::{j}" for f, j in GATE_JOBS]
)
def test_validator_ref_follows_uv_lock_and_ignores_live_branches(
    filename: str, job_key: str
) -> None:
    """The ref is v<uv.lock omnibase-core version>, whatever the PR base is.

    Falsifier: a step that still resolves from the PR base / merge_group base /
    push ref (the pre-OMN-20001 behavior) would emit the sentinel branch name
    here instead of the lock's tag.
    """
    step = _extract_validator_ref_step(WORKFLOWS_DIR / filename, job_key)
    sentinel = "sibling-live-branch-sentinel-omn-20001"
    output = _run_validator_ref_script(
        step,
        {
            "PR_BASE_REF": sentinel,
            "MERGE_GROUP_BASE_REF": sentinel,
            "PUSH_OR_DISPATCH_REF_NAME": sentinel,
        },
        _LOCK_TEMPLATE.format(version="1.2.3"),
    )
    assert "ref=v1.2.3\n" in output, output
    assert sentinel not in output, output
    # A second lock version moves the ref (the pin is read, not hardcoded).
    output = _run_validator_ref_script(step, {}, _LOCK_TEMPLATE.format(version="4.5.6"))
    assert "ref=v4.5.6\n" in output, output


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filename", "job_key"), GATE_JOBS, ids=[f"{f}::{j}" for f, j in GATE_JOBS]
)
def test_validator_ref_fails_closed_without_a_lock_pin(
    filename: str, job_key: str
) -> None:
    """No omnibase-core entry in uv.lock fails the step; no live-branch fallback."""
    step = _extract_validator_ref_step(WORKFLOWS_DIR / filename, job_key)
    script = step["run"]
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "uv.lock").write_text(
            '[[package]]\nname = "other"\nversion = "1"\n', encoding="utf-8"
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env={**os.environ, "GITHUB_OUTPUT": str(Path(tmpdir) / "out")},
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    assert result.returncode != 0, result.stdout
    assert "OMN-20001" in result.stdout


@pytest.mark.unit
@pytest.mark.parametrize(
    "filename", ["call-receipt-gate.yml", "call-occ-preflight.yml"]
)
def test_compat_pin_is_a_recorded_tag(filename: str) -> None:
    """The compat ref is a pinned release tag, not a branch name."""
    job_key = next(j for f, j in GATE_JOBS if f == filename)
    step = _extract_validator_ref_step(WORKFLOWS_DIR / filename, job_key)
    output = _run_validator_ref_script(step, {}, _LOCK_TEMPLATE.format(version="1.2.3"))
    pin = step["env"]["OMNIBASE_COMPAT_PIN"]
    assert pin.startswith("v"), pin
    assert f"compat_ref={pin}\n" in output
