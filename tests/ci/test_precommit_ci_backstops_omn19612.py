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
`no-localhost-fallbacks` job in ci.yml. It is unconditional and belongs to
STRICT_GATE_JOBS, so a skipped conclusion fails CI Summary.

This module pins that shape and derives the staged-scoped hook set
generically, so a NEW staged-scoped hook with no whole-tree counterpart
also fails, rather than only re-checking these two names forever. Both jobs
are pinned against job-level `if:` and `needs:` and step-level `if:` because
any of those can skip the backstop. The in-run job also runs this pin module
itself, unconditionally, so the structural checks execute on every PR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ci.ci_summary_gate import (
    EXPECTED_EXTERNAL_CONTEXTS,
    SKIPPABLE_GATE_JOBS,
    SOFT_ALLOWLIST,
    STRICT_GATE_JOBS,
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


def _pull_request_trigger(workflow_path: Path) -> dict[str, Any]:
    workflow = _load_yaml(workflow_path)
    yaml_keyed_workflow: dict[Any, Any] = workflow
    triggers: Any = (
        yaml_keyed_workflow[True]
        if True in yaml_keyed_workflow
        else yaml_keyed_workflow["on"]
    )
    assert isinstance(triggers, dict), (
        f"{workflow_path.name} `on` did not parse to a mapping"
    )
    pull_request: Any = triggers.get("pull_request")
    assert isinstance(pull_request, dict), (
        f"{workflow_path.name} has no pull_request trigger mapping"
    )
    return pull_request


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
    assert "if" not in job, (
        f"{job_id} has a job-level if, so {hook_id}'s backstop can be skipped"
    )
    assert "needs" not in job, (
        f"{job_id} has needs, so a failed or skipped upstream can skip "
        f"{hook_id}'s backstop"
    )
    assert job.get("continue-on-error") is not True
    for step in job["steps"]:
        assert "if" not in step, (
            f"a step of {job_id} has an if, so {hook_id}'s whole-tree run is "
            "conditional"
        )
        assert "continue-on-error" not in step, (
            f"a step of {job_id} carries continue-on-error, so {hook_id}'s "
            "whole-tree run is not structurally fail-closed"
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
        assert job_name in STRICT_GATE_JOBS, (
            f"{hook_id}'s backstop job {job_name!r} is not in STRICT_GATE_JOBS"
        )
        assert job_name not in SKIPPABLE_GATE_JOBS
        assert job_name not in SOFT_ALLOWLIST


@pytest.mark.parametrize("backstop", BACKSTOPS.values())
def test_backstop_workflow_runs_on_pull_requests_to_dev(
    backstop: tuple[Path, str, str, str, bool],
) -> None:
    workflow_path, _job_id, _name, _fragment, _external = backstop
    pull_request = _pull_request_trigger(workflow_path)
    branches = pull_request.get("branches", [])
    assert isinstance(branches, list)
    assert "dev" in branches, (
        f"{workflow_path.name} does not run for pull requests targeting dev"
    )
    assert "branches-ignore" not in pull_request
    # An explicit `types:` list replaces GitHub's default activity types, so
    # dropping `synchronize` would stop the backstop running on every push to
    # an open PR while leaving the job itself untouched.
    types = pull_request.get("types")
    if types is not None:
        assert isinstance(types, list)
        missing = {"opened", "synchronize", "reopened"} - set(types)
        assert not missing, (
            f"{workflow_path.name} pull_request types dropped {sorted(missing)}"
        )


def test_in_run_backstop_executes_this_pin_module_unconditionally() -> None:
    job = _job(CI_WORKFLOW, "no-localhost-fallbacks")
    expected = (
        "uv run pytest tests/ci/test_precommit_ci_backstops_omn19612.py "
        "-q -p no:cacheprovider"
    )
    matching_steps: list[dict[str, Any]] = []
    for raw_step in job["steps"]:
        assert isinstance(raw_step, dict)
        step: dict[str, Any] = raw_step
        real_lines = [
            line.strip()
            for line in str(step.get("run", "")).splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if expected in real_lines:
            matching_steps.append(step)

    assert matching_steps, "no-localhost-fallbacks no longer runs its pin test"
    assert all("if" not in step for step in matching_steps), (
        "the OMN-19612 self-run step must be unconditional"
    )


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
        pull_request = _pull_request_trigger(workflow_path)
        assert "paths" not in pull_request, (
            f"{workflow_path.name} gained a paths filter"
        )
        assert "paths-ignore" not in pull_request, (
            f"{workflow_path.name} gained a paths-ignore filter"
        )
