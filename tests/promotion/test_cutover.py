# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression tests for OMN-11746 dev/main cutover orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from onex_change_control.promotion.cutover import (
    EnumCutoverAction,
    execute_cutover,
    normalize_branch_protection_for_put,
    selected_waves_from_args,
)


class FakeGitHubClient:
    """In-memory GitHub client for cutover tests."""

    def __init__(self) -> None:
        self.default_branches: dict[str, str] = {}
        self.branch_shas: dict[tuple[str, str], str] = {}
        self.protections: dict[tuple[str, str], dict[str, Any]] = {}
        self.open_prs: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.created_branches: list[tuple[str, str, str]] = []
        self.updated_protections: list[tuple[str, str, dict[str, Any]]] = []
        self.default_updates: list[tuple[str, str]] = []
        self.retargeted_prs: list[tuple[str, int, str]] = []

    def repo_default_branch(self, repo: str) -> str:
        return self.default_branches[repo]

    def branch_sha(self, repo: str, branch: str) -> str | None:
        return self.branch_shas.get((repo, branch))

    def create_branch(self, repo: str, branch: str, sha: str) -> None:
        self.created_branches.append((repo, branch, sha))
        self.branch_shas[(repo, branch)] = sha

    def get_branch_protection(self, repo: str, branch: str) -> dict[str, Any]:
        return self.protections[(repo, branch)]

    def put_branch_protection(
        self, repo: str, branch: str, payload: dict[str, Any]
    ) -> None:
        self.updated_protections.append((repo, branch, payload))
        self.protections[(repo, branch)] = payload

    def set_default_branch(self, repo: str, branch: str) -> None:
        self.default_updates.append((repo, branch))
        self.default_branches[repo] = branch

    def list_open_prs(self, repo: str, base: str) -> list[dict[str, Any]]:
        return list(self.open_prs.get((repo, base), []))

    def retarget_pr(self, repo: str, number: int, base: str) -> None:
        self.retargeted_prs.append((repo, number, base))


def _protection() -> dict[str, Any]:
    return {
        "required_status_checks": {
            "strict": False,
            "contexts": ["CI Summary"],
            "checks": [{"context": "Build and push to ECR", "app_id": 15368}],
        },
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": None,
        "restrictions": None,
        "required_linear_history": {"enabled": False},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_conversation_resolution": {"enabled": True},
    }


def _client_for_wave_one() -> FakeGitHubClient:
    client = FakeGitHubClient()
    for repo in ("omnibase_compat", "omnibase_core"):
        client.default_branches[repo] = "main"
        client.branch_shas[(repo, "main")] = f"{repo}-main-sha"
        client.protections[(repo, "main")] = _protection()
        client.protections[(repo, "dev")] = _protection()
        client.open_prs[(repo, "main")] = []
    client.branch_shas[("omnibase_core", "dev")] = "omnibase_core-dev-sha"
    client.open_prs[("omnibase_core", "main")] = [
        {
            "number": 42,
            "title": "example",
            "url": "https://github.example/pr/42",
            "headRefName": "feature/example",
            "headRefOid": "abc1234",
            "baseRefName": "main",
        }
    ]
    return client


def test_protection_payload_preserves_required_context_names() -> None:
    payload = normalize_branch_protection_for_put(_protection())

    assert payload["required_status_checks"] == {
        "strict": False,
        "contexts": ["Build and push to ECR", "CI Summary"],
    }
    assert payload["required_pull_request_reviews"] is None
    assert payload["required_conversation_resolution"] is True


def test_dry_run_all_waves_plans_without_mutating() -> None:
    client = _client_for_wave_one()
    manifest = execute_cutover(
        client=client,
        owner="OmniNode-ai",
        cutover_id="cutover-test",
        selected_waves=(1,),
        dry_run=True,
        repos=("omnibase_compat", "omnibase_core"),
        generated_at=datetime(2026, 5, 23, tzinfo=UTC),
    )

    assert manifest.dry_run is True
    assert manifest.selected_waves == (1,)
    assert [entry.repo for entry in manifest.repos] == [
        "omnibase_compat",
        "omnibase_core",
    ]
    compat = manifest.repos[0]
    assert compat.dev_sha_before is None
    assert compat.dev_sha_after == "omnibase_compat-main-sha"
    assert EnumCutoverAction.CREATE_DEV_BRANCH in {
        action.action for action in compat.actions
    }
    assert client.created_branches == []
    assert client.updated_protections == []
    assert client.default_updates == []
    assert client.retargeted_prs == []


def test_execute_wave_creates_missing_dev_and_retargets_prs() -> None:
    client = _client_for_wave_one()

    manifest = execute_cutover(
        client=client,
        owner="OmniNode-ai",
        cutover_id="cutover-test",
        selected_waves=(1,),
        dry_run=False,
        repos=("omnibase_compat", "omnibase_core"),
        generated_at=datetime(2026, 5, 23, tzinfo=UTC),
    )

    assert ("omnibase_compat", "dev", "omnibase_compat-main-sha") in (
        client.created_branches
    )
    assert ("omnibase_compat", "dev") in {
        (repo, branch) for repo, branch, _payload in client.updated_protections
    }
    assert ("omnibase_core", 42, "dev") in client.retargeted_prs
    core = next(entry for entry in manifest.repos if entry.repo == "omnibase_core")
    assert core.open_prs_retargeted == 1
    assert core.retargeted_prs[0].fresh_checks_required is True
    assert core.retargeted_prs[0].auto_merge_rearm == "after_fresh_checks_pass"


def test_execute_requires_explicit_wave_stop_point() -> None:
    with pytest.raises(ValueError, match="requires --wave"):
        selected_waves_from_args(wave=None, all_waves=False, execute=True)


def test_execute_all_waves_still_requires_explicit_wave_stop_point() -> None:
    with pytest.raises(ValueError, match="requires --wave"):
        selected_waves_from_args(wave=None, all_waves=True, execute=True)


def test_dry_run_defaults_to_all_waves() -> None:
    assert selected_waves_from_args(wave=None, all_waves=False, execute=False) == (
        1,
        2,
        3,
        4,
    )
