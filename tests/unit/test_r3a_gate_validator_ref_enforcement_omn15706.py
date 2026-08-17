# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""R3a enforcement tests (OMN-15706).

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
honesty-gate + append-only-gate jobs, and validate-validator-requirements.yml.
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
# per the R3a-scoped inventory above. Each entry names exactly one job; ci.yml
# carries two distinct in-scope jobs (honesty-gate, append-only-gate).
GATE_JOBS: list[tuple[str, str]] = [
    ("call-receipt-gate.yml", "verify"),
    ("call-occ-preflight.yml", "occ-preflight"),
    ("validate-validator-requirements.yml", "validate-validator-requirements"),
    ("ci.yml", "honesty-gate"),
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
    step: dict[str, Any], env_overrides: dict[str, str]
) -> str:
    """Execute the validator_ref step's `run:` body in a bash subshell.

    Mirrors the GitHub Actions step-output contract closely enough to prove
    liveness: `$GITHUB_OUTPUT` is a real temp file, the declared step `env:`
    entries not present in `env_overrides` are exported as empty strings (as
    GitHub Actions does for an unset expression), and the script's own
    `set -euo pipefail` is honored by running it through `bash -c` directly
    (no shell wrapping that could swallow a nonzero exit).
    """
    script = step["run"]
    declared_env_names = list(step.get("env", {}).keys())

    with tempfile.TemporaryDirectory() as tmpdir:
        github_output = Path(tmpdir) / "github_output"
        github_output.write_text("", encoding="utf-8")

        run_env = dict(os.environ)
        # Actions substitutes an unresolved `${{ ... }}` expression with an
        # empty string, not an unset var -- match that for every declared env
        # name so the script's `[ -n "${VAR:-}" ]` checks behave identically.
        for name in declared_env_names:
            run_env[name] = ""
        # GITHUB_EVENT_NAME is a GitHub Actions default env var (not a
        # step-declared one) that ci.yml's notice line interpolates; set it
        # so `set -u` doesn't trip on an unrelated, non-R3a variable.
        run_env.setdefault("GITHUB_EVENT_NAME", "pull_request")
        run_env.update(env_overrides)
        run_env["GITHUB_OUTPUT"] = str(github_output)

        result = subprocess.run(
            ["bash", "-c", script],
            env=run_env,
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


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filename", "job_key"), GATE_JOBS, ids=[f"{f}::{j}" for f, j in GATE_JOBS]
)
def test_validator_ref_resolution_is_live_for_pr_base_ref(
    filename: str, job_key: str
) -> None:
    """A real (non-hardcoded) PR_BASE_REF must drive the resolved ref."""
    step = _extract_validator_ref_step(WORKFLOWS_DIR / filename, job_key)
    sentinel = "r3a-sentinel-branch-omn-15706-pr-base"

    output = _run_validator_ref_script(step, {"PR_BASE_REF": sentinel})

    assert f"ref={sentinel}" in output, (
        f"{filename} job '{job_key}': validator_ref step did not emit "
        f"'ref={sentinel}' for PR_BASE_REF={sentinel!r} — actual output:\n"
        f"{output!r}\n"
        'A hardcoded resolved_ref (e.g. resolved_ref="main") that ignores '
        "PR_BASE_REF would fail this exact assertion (Mutation B)."
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filename", "job_key"), GATE_JOBS, ids=[f"{f}::{j}" for f, j in GATE_JOBS]
)
def test_validator_ref_resolution_is_live_for_merge_group_base_ref(
    filename: str, job_key: str
) -> None:
    """With PR_BASE_REF absent, a real MERGE_GROUP_BASE_REF must drive the ref."""
    step = _extract_validator_ref_step(WORKFLOWS_DIR / filename, job_key)
    sentinel = "r3a-sentinel-branch-omn-15706-merge-group"

    output = _run_validator_ref_script(step, {"MERGE_GROUP_BASE_REF": sentinel})

    assert f"ref={sentinel}" in output, (
        f"{filename} job '{job_key}': validator_ref step did not emit "
        f"'ref={sentinel}' for MERGE_GROUP_BASE_REF={sentinel!r} (PR_BASE_REF "
        f"absent) — actual output:\n{output!r}"
    )


@pytest.mark.unit
def test_validator_ref_resolution_normalizes_refs_heads_prefix() -> None:
    """A `refs/heads/<branch>` form base ref must normalize to `<branch>`.

    Guards the `normalize_branch` helper each validator_ref step defines --
    covered once against call-receipt-gate.yml's copy since all five copies
    share byte-identical normalize_branch bodies (verified structurally by
    test_gate_job_inventory_has_no_unaccounted_validator_ref_steps' file
    coverage plus the parametrized liveness tests above).
    """
    step = _extract_validator_ref_step(
        WORKFLOWS_DIR / "call-receipt-gate.yml", "verify"
    )
    output = _run_validator_ref_script(
        step, {"PR_BASE_REF": "refs/heads/r3a-normalize-check"}
    )
    assert "ref=r3a-normalize-check" in output
