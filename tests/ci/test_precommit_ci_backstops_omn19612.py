# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Pin whole-tree CI counterparts for OMN-19612's staged-file hooks.

commit 74fba1f357 ("perf(pre-commit): scope validators to staged files")
moved check-runner-routing and no-localhost-fallbacks from pass_filenames: false
to pass_filenames: true (plus a files: filter), for commit speed. Whole-tree
enforcement still has to happen somewhere or a violation sitting outside the
staged diff is never caught again.

Both already have one. check-runner-routing's whole-tree run is the
`ci-naming-convention` job (omni-standards-compliance.yml), asserted
required via CI Summary's EXPECTED_EXTERNAL_CONTEXTS (it is required by
branch protection on `main` only, per required-checks.yaml, but CI Summary
-- required on BOTH branches -- asserts it as an external context too, so a
failure there fails CI Summary on `dev` as well). no-env-fallbacks' is the
`no-localhost-fallbacks` job in ci.yml, in SKIPPABLE_GATE_JOBS: it may
legitimately report `skipped` on a docs-only PR, but any other non-good
conclusion (including failure) still fails CI Summary.

This module pins that shape and derives the staged-scoped hook set
generically, so a NEW staged-scoped hook with no whole-tree counterpart
also fails, rather than only re-checking these two names forever.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ci.ci_summary_gate import (
    EXPECTED_EXTERNAL_CONTEXTS,
    GATE_JOBS,
    SOFT_ALLOWLIST,
)

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[2]
PRECOMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
STANDARDS_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "omni-standards-compliance.yml"
)

# hook_id -> (workflow, job_id, job_name, required-fragment, external?)
# `external` True means the job's check-run is enforced only through CI
# Summary's Layer-4 EXPECTED_EXTERNAL_CONTEXTS (a different workflow file
# than ci.yml, so the in-run poller never sees it directly).
BACKSTOPS: dict[str, tuple[Path, str, str, str, bool]] = {
    "check-runner-routing": (
        STANDARDS_WORKFLOW,
        "ci-naming-convention",
        "CI Naming Convention",
        "python scripts/validation/check_runner_routing.py",
        True,
    ),
    "no-localhost-fallbacks": (
        CI_WORKFLOW,
        "no-localhost-fallbacks",
        "No localhost env-var fallbacks in src/ (OMN-10737)",
        "uv run python scripts/validation/validate_no_env_fallbacks.py",
        False,
    ),
}

# Already pass_filenames: true before commit 74fba1f357 ("perf(pre-commit):
# scope validators to staged files (OMN-19612)") -- not moved to staged
# scope by this PR, so a whole-tree backstop for them is out of scope here.
PRE_EXISTING_STAGED_HOOKS = frozenset(
    {
        "check-ac-binding-acceptance",
        "check-ai-slop",
        "check-contract-shape-v1",
        "check-contract-substance-floor",
        "check-corpus-ratchet-wiring",
        "check-dod-authoring-hygiene",
        "check-evidence-commit-shas",
        "check-hardcoded-topic-compute",
        "check-localhost-url-compute",
        "check-merge-hold-gate-wiring",
        "check-private-ip-compute",
        "check-published-events-structure",
        "check-receipt-honesty",
        "check-todo-marker-compute",
        "check-yamlfmt-contamination",
        "check-yamlfmt-contamination-wiring",
        "dod-evidence-required",
        "lint-contract-check-values",
        "no-hardcoded-topics",
        "validate-contract-yaml",
        "validate-spdx-headers",
        "validate-string-versions",
    }
)


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{path.name} did not parse to a mapping"
    return loaded


def _staged_scoped_hook_ids() -> set[str]:
    config = _load_yaml(PRECOMMIT_CONFIG)
    default_stages = config.get("default_stages", ["pre-commit"])
    ids: set[str] = set()
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            stages = hook.get("stages", default_stages)
            if "pre-commit" not in stages:
                continue
            if hook.get("pass_filenames") is True:
                ids.add(str(hook["id"]))
    return ids


def _job(workflow_path: Path, job_id: str) -> dict[str, Any]:
    workflow = _load_yaml(workflow_path)
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict), (
        f"{workflow_path.name} `jobs` did not parse to a mapping"
    )
    assert job_id in jobs, f"{workflow_path.name} has no `{job_id}` job"
    job = jobs[job_id]
    assert isinstance(job, dict), (
        f"{workflow_path.name} job `{job_id}` did not parse to a mapping"
    )
    return job


def _command_lines(workflow_path: Path, job_id: str) -> list[str]:
    """Each line of every step's run: block, as a real shell statement.

    A substring match against the whole run: text would also match a shell
    comment that merely quotes the invocation in prose (measured: this repo's
    ci-naming-convention job carries exactly that comment, right next to the
    real invocation, for the runner-routing script). Only a line whose
    stripped text does not start with '#' counts as an actual command.
    """
    lines: list[str] = []
    for step in _job(workflow_path, job_id)["steps"]:
        for line in str(step.get("run", "")).splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                lines.append(stripped)
    return lines


def test_every_staged_scoped_hook_has_a_declared_backstop() -> None:
    staged = _staged_scoped_hook_ids()
    assert staged, "expected at least one staged-file-scoped hook"
    missing = staged - set(BACKSTOPS) - PRE_EXISTING_STAGED_HOOKS
    assert not missing, (
        f"staged-scoped hooks with no whole-tree backstop: {sorted(missing)}"
    )
    assert set(BACKSTOPS) <= staged, (
        "a BACKSTOPS entry no longer names a staged-scoped hook"
    )


@pytest.mark.parametrize(("hook_id", "backstop"), BACKSTOPS.items())
def test_whole_tree_counterpart_is_present_and_fail_closed(
    hook_id: str, backstop: tuple[Path, str, str, str, bool]
) -> None:
    workflow_path, job_id, job_name, fragment, _external = backstop
    job = _job(workflow_path, job_id)
    assert job["name"] == job_name
    lines = _command_lines(workflow_path, job_id)
    assert any(fragment in line for line in lines), (
        f"{hook_id} lost its whole-tree counterpart ({fragment}) -- a comment "
        "quoting the script path does not count, only a real command line"
    )
    assert job.get("continue-on-error") is not True
    for step in job["steps"]:
        assert step.get("continue-on-error") is not True, (
            f"a step of {job_id} is continue-on-error, so {hook_id}'s whole-tree "
            "run can fail silently"
        )


@pytest.mark.parametrize(("hook_id", "backstop"), BACKSTOPS.items())
def test_backstop_job_is_enforced_by_ci_summary(
    hook_id: str, backstop: tuple[Path, str, str, str, bool]
) -> None:
    _workflow_path, _job_id, job_name, _fragment, external = backstop
    if external:
        assert job_name in EXPECTED_EXTERNAL_CONTEXTS, (
            f"{hook_id}'s backstop job {job_name!r} is not asserted by CI Summary's "
            "EXPECTED_EXTERNAL_CONTEXTS"
        )
    else:
        assert job_name in GATE_JOBS, (
            f"{hook_id}'s backstop job {job_name!r} is not in GATE_JOBS"
        )
        assert job_name not in SOFT_ALLOWLIST


def test_ci_summary_itself_is_required_on_dev() -> None:
    required = _load_yaml(REPO_ROOT / ".github" / "required-checks.yaml")
    matches = [
        gate
        for gate in required["gates"]
        if gate.get("name") == "CI Summary" and gate.get("job_path") == ["ci-summary"]
    ]
    assert len(matches) == 1
    assert matches[0]["mode"] == "REQUIRED"
    assert matches[0].get("branch", "BOTH") in ("BOTH", "dev")


def test_workflows_carry_no_pull_request_paths_filter() -> None:
    for workflow_path in {CI_WORKFLOW, STANDARDS_WORKFLOW}:
        workflow: dict[Any, Any] = _load_yaml(workflow_path)
        triggers = workflow[True] if True in workflow else workflow["on"]
        pull_request = triggers.get("pull_request") or {}
        assert "paths" not in pull_request, (
            f"{workflow_path.name} gained a paths filter"
        )
        assert "paths-ignore" not in pull_request, (
            f"{workflow_path.name} gained a paths-ignore filter"
        )
