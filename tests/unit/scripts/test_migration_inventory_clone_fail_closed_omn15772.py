# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15772: the ``migration-inventory`` job's "Clone peer repos" step must
fail closed and guarantee a fresh clone on every run.

Two independently-observed defects motivated this:

1. **Silent clone failure** (ticket-original evidence): the step piped every
   ``git clone`` through ``2>/dev/null`` and wrapped the loop body in
   ``|| true``. A failed or partial peer clone was absorbed silently, and
   ``check-migration-inventory`` then validated whatever partial checkout
   happened to land on disk -- three same-head reruns of the same PR produced
   divergent error counts (14/17/9) off an unchanged file set.
2. **Stale clone on persistent self-hosted runners** (2026-08-18 comment):
   all self-hosted runner containers in the fleet carry a persistent
   ``/tmp/repos`` across job runs (unlike GitHub-hosted runners, which are
   ephemeral). A ``git clone`` into an already-populated target silently
   fails under the old pattern, so the validator ran against STALE sibling
   state from an earlier job instead of current ``origin/dev``.

The fix reuses the retry-with-freshness ``clone_omni_repo`` pattern already
proven in this same file's sibling job, ``migration-conflicts`` (its
"Clone cross-repo dependencies" step): every attempt does ``rm -rf
"${target}"`` before cloning (guarantees freshness regardless of what a
runner container is carrying), and the function ``return 1``s with an
``::error::`` after exhausting three attempts -- under GitHub Actions' default
``bash -e`` step shell, a plain (non-``||``-wrapped) non-zero return fails
the step, so simply not swallowing the exit code is what makes this
fail-closed.

Driven against the real ``ci.yml``, not a hand-built fixture, so a future
edit to either job cannot silently regress this shape.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_YAML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _ci_yaml() -> dict[str, Any]:
    return dict(yaml.safe_load(_CI_YAML.read_text(encoding="utf-8")))


def _step(job_id: str, step_name: str) -> dict[str, Any]:
    jobs = _ci_yaml()["jobs"]
    assert job_id in jobs, f"{job_id} is not a job in ci.yml"
    for step in jobs[job_id]["steps"]:
        if step.get("name") == step_name:
            return dict(step)
    msg = f"no step named {step_name!r} in job {job_id!r}"
    raise AssertionError(msg)


def _clone_peer_repos_script() -> str:
    return str(_step("migration-inventory", "Clone peer repos")["run"])


def _clone_cross_repo_deps_script() -> str:
    return str(_step("migration-conflicts", "Clone cross-repo dependencies")["run"])


def _clone_function_body(script: str) -> str:
    """Extract the ``clone_omni_repo() { ... }`` function body only, so the
    per-job repo list / target root (which legitimately differs) is not
    compared. YAML block-scalar dedenting puts the closing brace at column 0."""
    match = re.search(r"clone_omni_repo\(\)\s*\{(.*?)\n\}", script, flags=re.DOTALL)
    assert match is not None, "clone_omni_repo() function not found in script"
    return match.group(1)


class TestCloneStepFailsClosed:
    """AC: a failed peer clone must fail the job, never be silently absorbed."""

    def test_stderr_is_not_swallowed(self) -> None:
        script = _clone_peer_repos_script()
        assert "2>/dev/null" not in script, (
            "clone errors must be visible in the job log, not redirected away"
        )

    def test_clone_failure_is_not_wrapped_in_or_true(self) -> None:
        script = _clone_peer_repos_script()
        assert "|| true" not in script, (
            "a bare `|| true` on the clone call absorbs a failed/partial peer "
            "clone -- the validator then silently runs against an incomplete "
            "checkout instead of failing the job"
        )

    def test_the_function_signals_failure_on_exhaustion(self) -> None:
        script = _clone_peer_repos_script()
        assert "return 1" in script
        assert "::error::" in script


class TestCloneStepGuaranteesFreshness:
    """AC: self-hosted runners carry persistent temp dirs across jobs --
    the step must not trust whatever is already on disk."""

    def test_the_repos_root_is_removed_before_use(self) -> None:
        script = _clone_peer_repos_script()
        assert re.search(r'repo_root="\$\{RUNNER_TEMP:-/tmp\}/', script), (
            "the repos root must be scoped to the current workflow run instead "
            "of hardcoding a shared /tmp/repos directory"
        )
        assert 'rm -rf "$repo_root"' in script, (
            "the repos root must be wiped before cloning so a prior job's "
            "checkout on this (persistent, self-hosted) runner container "
            "cannot be validated as if it were fresh"
        )
        # the wipe must precede population, not follow it
        wipe_idx = script.index('rm -rf "$repo_root"')
        mkdir_idx = script.index('mkdir -p "$repo_root"')
        assert wipe_idx < mkdir_idx

    def test_each_clone_target_is_removed_before_its_own_attempt(self) -> None:
        """Defense in depth under the top-level wipe: the per-attempt
        `rm -rf "${target}"` inside clone_omni_repo() is what actually makes
        `git clone` succeed into a previously-populated target."""
        body = _clone_function_body(_clone_peer_repos_script())
        assert 'rm -rf "${target}"' in body


class TestCloneFunctionMatchesProvenSiblingPattern:
    """migration-conflict-check's clone_omni_repo() already has this shape
    and is exercised in CI today. Reusing it (not inventing a parallel
    pattern) is the point -- assert the two function bodies stay identical
    so they cannot drift apart again."""

    def test_function_body_is_identical_to_the_proven_sibling(self) -> None:
        inventory_body = _clone_function_body(_clone_peer_repos_script())
        conflict_body = _clone_function_body(_clone_cross_repo_deps_script())
        assert inventory_body == conflict_body, (
            "migration-inventory's clone_omni_repo() has drifted from the "
            "proven pattern in migration-conflict-check's clone_omni_repo() "
            "-- reconcile them, don't maintain two copies"
        )


class TestValidateInventoryStepIsUnchanged:
    """Scope guard: this ticket fixes the clone step, not the validator."""

    def test_the_validate_step_still_reads_configured_repo_root(self) -> None:
        step = _step("migration-inventory", "Validate inventory")
        assert '--repos-root "$MIGRATION_INVENTORY_REPOS_ROOT"' in str(step["run"])
