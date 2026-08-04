# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
from __future__ import annotations

from pathlib import Path

from scripts.validation.check_runner_routing import (
    FORK_AWARE_ROUTE,
    PUBLIC_SELECTOR,
    TRUSTED_SELECTOR,
    expected_selector,
    validate_runs_on,
    validate_workflows,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_normal_route_selects_public_only_for_fork_pull_requests() -> None:
    repository = "OmniNode-ai/onex_change_control"

    assert expected_selector("pull_request", repository, repository) == "trusted"
    assert (
        expected_selector("pull_request", "contributor/onex_change_control", repository)
        == "public"
    )
    assert expected_selector("push", None, repository) == "trusted"
    assert expected_selector("merge_group", None, repository) == "trusted"


def test_workflows_use_the_fork_aware_route() -> None:
    assert validate_workflows(REPO_ROOT) == []


def test_repository_does_not_declare_pull_request_target() -> None:
    workflow_sources = (REPO_ROOT / ".github/workflows").glob("*.yml")

    assert all(
        "  pull_request_target:" not in workflow.read_text(encoding="utf-8")
        for workflow in workflow_sources
    )


def test_legacy_dev_base_public_runner_shortcut_is_rejected() -> None:
    legacy_route = f"""(github.event_name == 'pull_request' && github.base_ref == 'dev')
&& {PUBLIC_SELECTOR}
|| {FORK_AWARE_ROUTE}"""

    violations = validate_runs_on(
        workflow_path=".github/workflows/example.yml",
        job_name="example",
        runs_on=legacy_route,
    )

    assert violations == [
        ".github/workflows/example.yml:example uses the forbidden dev-base "
        "public-runner shortcut"
    ]


def test_pr_wide_public_runner_route_is_rejected() -> None:
    pr_wide_route = f"""github.event_name == 'pull_request'
&& {PUBLIC_SELECTOR}
|| {TRUSTED_SELECTOR}"""

    violations = validate_runs_on(
        workflow_path=".github/workflows/example.yml",
        job_name="example",
        runs_on=pr_wide_route,
    )

    assert violations == [
        ".github/workflows/example.yml:example must route only fork pull requests "
        "to the public selector"
    ]
