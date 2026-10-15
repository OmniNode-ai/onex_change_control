# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Pin the CI step and pre-commit hook that run the authz-guard scope tests.

Operator ruling 2026-10-14T12:04:57Z: no human-authored commit and no required
human merge decision on guarded change-control paths. OMN-14919 (#13803)
narrowed check-bot-authored-authz-guard to ``grants/`` and the governance
exclusion to ``grants/prod_promotion_grants.yaml``; the two unit modules below
hold that scope in both directions. This module pins that the required
``check-bot-authored-authz-guard`` job (a CI Summary member) and a pre-commit
hook both run them, so a change that re-widens the guard fails in both places.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUARD_JOB = "check-bot-authored-authz-guard"
_HOOK_ID = "authz-guard-scope-tests"
_SCOPE_TESTS = (
    "tests/unit/scripts/test_check_bot_authored_authz_guard.py",
    "tests/unit/scripts/test_check_governance_paths.py",
)


def _guard_job_runs() -> list[str]:
    ci = yaml.safe_load((_REPO_ROOT / ".github/workflows/ci.yml").read_text())
    job: dict[str, Any] = ci["jobs"][_GUARD_JOB]
    return [step["run"] for step in job["steps"] if isinstance(step.get("run"), str)]


def _hook() -> dict[str, Any]:
    config = yaml.safe_load((_REPO_ROOT / ".pre-commit-config.yaml").read_text())
    hooks = [
        hook
        for repo in config["repos"]
        if repo.get("repo") == "local"
        for hook in repo["hooks"]
        if hook.get("id") == _HOOK_ID
    ]
    assert hooks, f"no {_HOOK_ID!r} hook declared in .pre-commit-config.yaml"
    found: dict[str, Any] = hooks[0]
    return found


def test_scope_test_modules_exist() -> None:
    for path in _SCOPE_TESTS:
        assert (_REPO_ROOT / path).is_file(), path


def test_guard_job_runs_both_scope_test_modules() -> None:
    runs = _guard_job_runs()
    for path in _SCOPE_TESTS:
        assert any("pytest" in run and path in run for run in runs), (
            f"the {_GUARD_JOB} job runs no pytest step over {path}"
        )


def test_guard_job_scope_step_is_unconditional() -> None:
    ci = yaml.safe_load((_REPO_ROOT / ".github/workflows/ci.yml").read_text())
    steps = [
        step
        for step in ci["jobs"][_GUARD_JOB]["steps"]
        if isinstance(step.get("run"), str)
        and "pytest" in step["run"]
        and _SCOPE_TESTS[0] in step["run"]
    ]
    assert steps, "no scope-test step found in the guard job"
    for step in steps:
        assert "if" not in step, "the scope-test step must not be skippable"
        assert not step.get("continue-on-error"), "the scope-test step must block"


def test_pre_commit_hook_runs_both_scope_test_modules() -> None:
    hook = _hook()
    entry = hook["entry"]
    assert isinstance(entry, str)
    for path in _SCOPE_TESTS:
        assert path in entry, f"{_HOOK_ID} entry omits {path}"
    assert hook.get("pass_filenames") is False


def test_pre_commit_hook_triggers_on_the_guard_its_tests_and_both_wirings() -> None:
    files = _hook()["files"]
    assert isinstance(files, str)
    pattern = re.compile(files)
    for path in (
        "src/onex_change_control/scripts/check_bot_authored_authz_guard.py",
        "scripts/ci/check_governance_paths.py",
        *_SCOPE_TESTS,
        ".github/workflows/ci.yml",
        ".pre-commit-config.yaml",
    ):
        assert pattern.search(path), f"{_HOOK_ID} does not trigger on {path}"
