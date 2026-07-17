# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression tests for OMN-11738 promotion staleness monitoring."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from onex_change_control.promotion.staleness import (
    FAILURE_ROUTES,
    EnumPromotionAlertSeverity,
    EnumPromotionFailureState,
    ModelPromotionStalenessRepo,
    build_staleness_report,
    measure_repo_staleness,
)

if TYPE_CHECKING:
    from pathlib import Path


def _git(repo: Path, *args: str, commit_date: str | None = None) -> str:
    env = None
    if commit_date is not None:
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": commit_date,
            "GIT_COMMITTER_DATE": commit_date,
        }
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def _commit(repo: Path, message: str, *, commit_date: str) -> None:
    _git(repo, "commit", "--allow-empty", "-m", message, commit_date=commit_date)


def test_failure_class_routes_drive_alert_severity() -> None:
    assert (
        FAILURE_ROUTES[EnumPromotionFailureState.CODE].severity
        == EnumPromotionAlertSeverity.P1
    )
    assert (
        FAILURE_ROUTES[EnumPromotionFailureState.RUNTIME].severity
        == EnumPromotionAlertSeverity.CRITICAL
    )
    assert FAILURE_ROUTES[
        EnumPromotionFailureState.FLAKY_INFRA
    ].requires_user_skip_review


def test_staleness_report_requires_linear_ticket_and_slack_alert() -> None:
    repo = ModelPromotionStalenessRepo(
        repo="omniweb",
        source_branch="dev",
        target_branch="main",
        source_sha="a" * 40,
        target_sha="b" * 40,
        unpromoted_commit_count=2,
        oldest_unpromoted_commit_sha="c" * 40,
        oldest_unpromoted_commit_at=datetime(2026, 5, 15, tzinfo=UTC),
        newest_unpromoted_commit_sha="d" * 40,
        newest_unpromoted_commit_at=datetime(2026, 5, 22, tzinfo=UTC),
        staleness_seconds=8 * 86_400,
        staleness_days=8.0,
    )

    report = build_staleness_report(
        repos=(repo,),
        evaluated_at=datetime(2026, 5, 23, tzinfo=UTC),
        source_branch="dev",
        target_branch="main",
        failure_class=EnumPromotionFailureState.INTEGRATION,
    )

    assert report.requires_linear_ticket is True
    assert report.requires_slack_alert is True
    assert report.linear_ticket_title is not None
    assert "promotion_failed_integration" in (report.linear_ticket_description or "")
    assert report.alert_route.linear_priority == 1


def test_measure_repo_staleness_uses_oldest_unpromoted_commit(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "example"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "checkout", "-b", "main")
    _commit(repo, "main", commit_date="2026-05-17T12:00:00+00:00")
    _git(repo, "update-ref", "refs/remotes/origin/main", "main")
    _git(repo, "checkout", "-b", "dev")
    _commit(repo, "old dev", commit_date="2026-05-18T12:00:00+00:00")
    oldest = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "new dev", commit_date="2026-05-22T12:00:00+00:00")
    _git(repo, "update-ref", "refs/remotes/origin/dev", "dev")

    result = measure_repo_staleness(
        repo_path=repo,
        repo="example",
        source_branch="dev",
        target_branch="main",
        now=datetime(2026, 5, 23, 12, 0, tzinfo=UTC),
    )

    assert result.unpromoted_commit_count == 2
    assert result.oldest_unpromoted_commit_sha == oldest
    assert result.staleness_days == 5.0
