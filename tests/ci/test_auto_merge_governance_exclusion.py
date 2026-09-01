# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""The OMN-16117 governance-path exclusion must exist on the branch that serves
grant-PR traffic, not only on `dev` (OMN-17437).

Why this exists
---------------
OMN-16117 added a governance-path exclusion to `.github/workflows/auto-merge.yml`
so the workflow never arms auto-merge on a PR touching
`grants/prod_promotion_grants.yaml` (the OMN-13418 prod-promotion trust anchor)
or `allowlists/skip_token_approvals.yaml`. It landed on `dev` only.

**Grant PRs never target `dev`.** Every merged prod-promotion grant PR — #7084,
#7213, #7418, #7464, #7554, #7561, #7662, #7731 — targeted `main`. For
`pull_request: [opened, reopened, ready_for_review]` GitHub resolves the
workflow from the PR's merge ref, so a `main`-based grant PR that does not
itself modify the workflow ran the **unprotected** 165-line `main` copy, which
arms `gh pr merge "$PR" --auto --squash` for any PR authored by `jonahgabriel`
— the same identity agent sessions act under.

`pull_request_review` / `check_suite` / `workflow_dispatch` runs resolve from
the default branch (`dev`) and did get the protected copy, which is why the hole
was intermittent rather than constant, and why it had not fired every time.

What each test covers
---------------------
`test_workflow_carries_governance_exclusion_step`,
`test_every_arming_step_is_gated_on_the_exclusion` and
`test_governance_path_checker_is_importable_and_guards_the_grant_file` are
**per-branch** invariants: they read this checkout only, so they run identically
on `main` and on `dev` once the file is present on both. That is the always-on
protection.

`test_grant_pr_base_refs_all_carry_the_exclusion` is the **cross-branch** drift
catcher: for every branch in :data:`GRANT_PR_BASE_BRANCHES` it applies the same
invariant to that branch's `.github/workflows/auto-merge.yml`, so a governance
change landed on one base branch alone leaves the other one red. The branch this
checkout is destined for is read from the working tree rather than from its ref
— on an open PR the fix is in the tree, not yet on the ref, and a refs-only
check would require the change to be merged before it could merge. Every other
branch is read out of the local git object store; one that is not present there
is reported and skipped rather than silently passing, since OCC's `test` CI job
checks out at the default `fetch-depth: 1` and resolves only the branch under
test. A full clone — any local pre-push run — resolves both and gives the real
cross-branch proof.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_RELPATH = ".github/workflows/auto-merge.yml"
WORKFLOW = REPO_ROOT / WORKFLOW_RELPATH
CHECKER = REPO_ROOT / "scripts" / "ci" / "check_governance_paths.py"

#: Branches that appear as the base of a merged prod-promotion grant PR, and
#: therefore must each carry the exclusion. `main` is where every grant PR to
#: date has landed; `dev` is the default branch, from which the
#: `pull_request_review` / `check_suite` / `workflow_dispatch` runs resolve.
GRANT_PR_BASE_BRANCHES: tuple[str, ...] = ("main", "dev")

#: The exclusion step's stable identity. The `if:` guard on every arming step is
#: written against this id, so renaming the step without renaming the guards
#: would silently ungate them.
GOVERNANCE_GATE_STEP_NAME = "Check governance-path exclusion"
GOVERNANCE_GATE_STEP_ID = "governance_gate"
GOVERNANCE_GATE_GUARD = "steps.governance_gate.outputs.exclude != 'true'"

#: Substrings that identify a step which actually lands or queues a merge. Any
#: such step must be gated on the exclusion.
_ARMING_MARKERS = ("gh pr merge", "enqueuePullRequest", "merge_queue_enqueue.py")

#: Resolved absolute path to git, so the subprocess calls below do not depend
#: on PATH lookup (ruff S607).
_GIT: str = shutil.which("git") or "git"


def _load_workflow(text: str) -> dict[Any, Any]:
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict), "auto-merge.yml did not parse as a mapping"
    return loaded


def _auto_merge_steps(workflow: dict[Any, Any]) -> list[dict[str, Any]]:
    jobs = workflow["jobs"]
    steps: list[dict[str, Any]] = []
    for job in jobs.values():
        steps.extend(job.get("steps", []))
    return steps


def _assert_exclusion_invariant(text: str, origin: str) -> None:
    """Assert the full governance-exclusion invariant over one workflow body.

    ``origin`` names where the body came from (worktree path or git ref) so a
    cross-branch failure says which branch is unprotected.
    """
    workflow = _load_workflow(text)
    steps = _auto_merge_steps(workflow)

    gate_steps = [s for s in steps if s.get("name") == GOVERNANCE_GATE_STEP_NAME]
    assert gate_steps, (
        f"{origin}: no '{GOVERNANCE_GATE_STEP_NAME}' step in {WORKFLOW_RELPATH}. "
        "This branch arms auto-merge on prod-promotion grant PRs with no "
        "governance-path exclusion (OMN-16117 / OMN-17437)."
    )
    gate = gate_steps[0]
    assert gate.get("id") == GOVERNANCE_GATE_STEP_ID, (
        f"{origin}: the governance gate step must keep id "
        f"'{GOVERNANCE_GATE_STEP_ID}' — every arming step's `if:` is written "
        "against it."
    )
    assert "scripts/ci/check_governance_paths.py" in gate.get("run", ""), (
        f"{origin}: the governance gate step must delegate the decision to "
        "scripts/ci/check_governance_paths.py, not inline it in bash."
    )

    arming = [
        s
        for s in steps
        if any(marker in str(s.get("run", "")) for marker in _ARMING_MARKERS)
    ]
    assert arming, f"{origin}: found no auto-merge arming step to check"
    for step in arming:
        guard = str(step.get("if", ""))
        assert GOVERNANCE_GATE_GUARD in guard, (
            f"{origin}: arming step {step.get('name')!r} is not gated on "
            f"`{GOVERNANCE_GATE_GUARD}` — it can arm auto-merge on a grant PR."
        )


def _base_branch_under_edit() -> str | None:
    """Return the grant-PR base branch this checkout is destined for, if known.

    For that one branch the **working tree** is the authority, not the committed
    ref: on an open PR the fix is in the tree and not yet on the ref, and a
    cross-branch check that read only refs would demand the change already be
    merged before it could merge.

    Resolution order: the PR base branch GitHub Actions publishes, then the
    branch's own upstream. Returns None when neither is available, in which case
    every branch is checked from its ref.
    """
    github_base = os.environ.get("GITHUB_BASE_REF", "").strip()
    if github_base:
        return github_base

    upstream = subprocess.run(
        [
            _GIT,
            "-C",
            str(REPO_ROOT),
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if upstream.returncode != 0:
        return None
    name: str = upstream.stdout.strip()
    return name.split("/", 1)[1] if name.startswith("origin/") else name or None


def _read_blob(ref: str, relpath: str) -> str | None:
    """Return the blob at ``ref:relpath`` from the local object store, or None."""
    for candidate in (f"refs/remotes/origin/{ref}", f"refs/heads/{ref}", ref):
        resolved = subprocess.run(
            [
                _GIT,
                "-C",
                str(REPO_ROOT),
                "rev-parse",
                "--verify",
                "--quiet",
                candidate,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if resolved.returncode != 0:
            continue
        blob = subprocess.run(
            [_GIT, "-C", str(REPO_ROOT), "show", f"{candidate}:{relpath}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if blob.returncode == 0:
            return blob.stdout
    return None


@pytest.mark.unit
def test_workflow_carries_governance_exclusion_step() -> None:
    """This checkout's auto-merge workflow has the exclusion step."""
    _assert_exclusion_invariant(WORKFLOW.read_text(encoding="utf-8"), str(WORKFLOW))


@pytest.mark.unit
def test_every_arming_step_is_gated_on_the_exclusion() -> None:
    """No step can arm or enqueue a merge without consulting the gate.

    Covered by :func:`_assert_exclusion_invariant`; kept as its own test so a
    regression that keeps the step but drops a guard names itself precisely.
    """
    workflow = _load_workflow(WORKFLOW.read_text(encoding="utf-8"))
    arming = [
        s
        for s in _auto_merge_steps(workflow)
        if any(marker in str(s.get("run", "")) for marker in _ARMING_MARKERS)
    ]
    assert arming, "found no auto-merge arming step to check"
    ungated = [
        s.get("name")
        for s in arming
        if GOVERNANCE_GATE_GUARD not in str(s.get("if", ""))
    ]
    assert not ungated, f"arming steps not gated on the governance exclusion: {ungated}"


@pytest.mark.unit
def test_governance_path_checker_is_importable_and_guards_the_grant_file() -> None:
    """The decision module resolves on this branch and covers the grant file.

    The workflow shells out to this path at runtime; a workflow step that
    references a script the branch does not carry would fail open on `main`
    exactly the way OMN-17437 describes.
    """
    assert CHECKER.is_file(), (
        f"{CHECKER} is missing on this branch — the auto-merge workflow shells "
        "out to it, so the exclusion cannot run here."
    )
    spec = importlib.util.spec_from_file_location("check_governance_paths", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "grants/prod_promotion_grants.yaml" in module.GOVERNANCE_PATHS
    assert "allowlists/skip_token_approvals.yaml" in module.GOVERNANCE_PATHS
    # Fail-closed contract: an undetermined changed-file set excludes.
    assert module.touches_governance_path(None) is True
    assert module.touches_governance_path(["grants/prod_promotion_grants.yaml"]) is True
    assert module.touches_governance_path(["contracts/OMN-17437.yaml"]) is False


@pytest.mark.unit
def test_grant_pr_base_refs_all_carry_the_exclusion() -> None:
    """Cross-branch: every branch that serves grant-PR traffic carries the gate.

    This is the test that makes OMN-16117's failure mode non-repeatable — a fix
    landed on one base branch alone leaves the other red here.
    """
    under_edit = _base_branch_under_edit()
    unresolved: list[str] = []
    checked: list[str] = []
    for branch in GRANT_PR_BASE_BRANCHES:
        if branch == under_edit:
            # This checkout is what `branch` becomes once the change lands, so
            # the tree — not the ref — is the authority for it.
            _assert_exclusion_invariant(
                WORKFLOW.read_text(encoding="utf-8"),
                f"worktree (destined for {branch}):{WORKFLOW_RELPATH}",
            )
            checked.append(branch)
            continue
        body = _read_blob(branch, WORKFLOW_RELPATH)
        if body is None:
            unresolved.append(branch)
            continue
        _assert_exclusion_invariant(body, f"ref {branch}:{WORKFLOW_RELPATH}")
        checked.append(branch)

    if not checked:
        pytest.skip(
            "no grant-PR base ref resolvable in the local object store "
            f"({unresolved}); OCC's `test` CI job checks out at fetch-depth 1. "
            "The per-branch tests above still cover this checkout."
        )
