# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Staleness monitor for dev-to-main promotion lag."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from onex_change_control.promotion.manifest import DEFAULT_PROMOTION_REPOS
from onex_change_control.promotion.workflow import write_json

SECONDS_PER_DAY = 86_400
LINEAR_TICKET_THRESHOLD_DAYS = 3
SLACK_ALERT_THRESHOLD_DAYS = 7


class EnumPromotionFailureState(StrEnum):
    """Promotion failure states from the dev/main branch split plan."""

    CODE = "promotion_failed_code"
    INTEGRATION = "promotion_failed_integration"
    RUNTIME = "promotion_failed_runtime"
    FLAKY_INFRA = "promotion_failed_flaky_infra"
    SKIPPED_BY_USER = "promotion_skipped_by_user"


class EnumPromotionAlertSeverity(StrEnum):
    """Alert severity emitted by the staleness monitor."""

    INFO = "info"
    WARNING = "warning"
    P1 = "p1"
    CRITICAL = "critical"


class ModelPromotionFailureRoute(BaseModel):
    """Alert routing policy for one promotion failure state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    failure_class: EnumPromotionFailureState
    severity: EnumPromotionAlertSeverity
    action: str = Field(min_length=1)
    linear_priority: int | None = Field(default=None, ge=1, le=4)
    slack_channel_hint: str = Field(min_length=1)
    requires_user_skip_review: bool = False


class ModelPromotionStalenessRepo(BaseModel):
    """Per-repository dev/main staleness measurement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str = Field(min_length=1)
    source_branch: str = Field(min_length=1)
    target_branch: str = Field(min_length=1)
    source_sha: str = Field(min_length=7)
    target_sha: str = Field(min_length=7)
    unpromoted_commit_count: int = Field(ge=0)
    oldest_unpromoted_commit_sha: str | None = None
    oldest_unpromoted_commit_at: datetime | None = None
    newest_unpromoted_commit_sha: str | None = None
    newest_unpromoted_commit_at: datetime | None = None
    staleness_seconds: int = Field(ge=0)
    staleness_days: float = Field(ge=0)


class ModelPromotionStalenessReport(BaseModel):
    """Workflow evidence for the promotion staleness monitor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "1.0.0"
    evaluated_at: datetime
    source_branch: str
    target_branch: str
    linear_ticket_threshold_days: int
    slack_alert_threshold_days: int
    failure_class: EnumPromotionFailureState
    alert_route: ModelPromotionFailureRoute
    max_staleness_days: float
    stale_repo_count: int
    requires_linear_ticket: bool
    requires_slack_alert: bool
    linear_ticket_title: str | None = None
    linear_ticket_description: str | None = None
    slack_message: str | None = None
    repos: tuple[ModelPromotionStalenessRepo, ...]


FAILURE_ROUTES: dict[EnumPromotionFailureState, ModelPromotionFailureRoute] = {
    EnumPromotionFailureState.CODE: ModelPromotionFailureRoute(
        failure_class=EnumPromotionFailureState.CODE,
        severity=EnumPromotionAlertSeverity.P1,
        action="file per-repository failure ticket and block promotion",
        linear_priority=1,
        slack_channel_hint="repo-owner-triage",
    ),
    EnumPromotionFailureState.INTEGRATION: ModelPromotionFailureRoute(
        failure_class=EnumPromotionFailureState.INTEGRATION,
        severity=EnumPromotionAlertSeverity.P1,
        action="file cross-repository integration ticket and block promotion",
        linear_priority=1,
        slack_channel_hint="integration-triage",
    ),
    EnumPromotionFailureState.RUNTIME: ModelPromotionFailureRoute(
        failure_class=EnumPromotionFailureState.RUNTIME,
        severity=EnumPromotionAlertSeverity.CRITICAL,
        action="file runtime topology ticket and block promotion",
        linear_priority=1,
        slack_channel_hint="runtime-stability",
    ),
    EnumPromotionFailureState.FLAKY_INFRA: ModelPromotionFailureRoute(
        failure_class=EnumPromotionFailureState.FLAKY_INFRA,
        severity=EnumPromotionAlertSeverity.WARNING,
        action="alert and request user-approved skip review",
        linear_priority=2,
        slack_channel_hint="infra-flake-triage",
        requires_user_skip_review=True,
    ),
    EnumPromotionFailureState.SKIPPED_BY_USER: ModelPromotionFailureRoute(
        failure_class=EnumPromotionFailureState.SKIPPED_BY_USER,
        severity=EnumPromotionAlertSeverity.INFO,
        action="record skip evidence and watch expiry",
        linear_priority=3,
        slack_channel_hint="promotion-audit",
        requires_user_skip_review=True,
    ),
}


def _run_git(repo_path: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo_path), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _branch_sha(repo_path: Path, branch: str) -> str:
    candidates = (f"origin/{branch}", branch)
    for candidate in candidates:
        try:
            return _run_git(repo_path, "rev-parse", candidate)
        except subprocess.CalledProcessError:
            continue
    msg = f"could not resolve {branch!r} in {repo_path}"
    raise RuntimeError(msg)


def _parse_commit_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _unpromoted_commit_records(
    repo_path: Path, source_branch: str, target_branch: str
) -> tuple[tuple[str, datetime], ...]:
    output = _run_git(
        repo_path,
        "log",
        "--reverse",
        "--format=%H%x09%cI",
        f"origin/{target_branch}..origin/{source_branch}",
    )
    records: list[tuple[str, datetime]] = []
    for line in output.splitlines():
        if not line:
            continue
        commit_sha, committed_at = line.split("\t", 1)
        records.append((commit_sha, _parse_commit_at(committed_at)))
    return tuple(records)


def measure_repo_staleness(
    *,
    repo_path: Path,
    repo: str,
    source_branch: str,
    target_branch: str,
    now: datetime,
) -> ModelPromotionStalenessRepo:
    """Measure how long ``target_branch`` has been behind ``source_branch``."""
    evaluated_at = now.astimezone(UTC)
    records = _unpromoted_commit_records(repo_path, source_branch, target_branch)
    source_sha = _branch_sha(repo_path, source_branch)
    target_sha = _branch_sha(repo_path, target_branch)
    oldest = records[0] if records else None
    newest = records[-1] if records else None
    staleness_seconds = (
        max(0, int((evaluated_at - oldest[1]).total_seconds())) if oldest else 0
    )
    return ModelPromotionStalenessRepo(
        repo=repo,
        source_branch=source_branch,
        target_branch=target_branch,
        source_sha=source_sha,
        target_sha=target_sha,
        unpromoted_commit_count=len(records),
        oldest_unpromoted_commit_sha=oldest[0] if oldest else None,
        oldest_unpromoted_commit_at=oldest[1] if oldest else None,
        newest_unpromoted_commit_sha=newest[0] if newest else None,
        newest_unpromoted_commit_at=newest[1] if newest else None,
        staleness_seconds=staleness_seconds,
        staleness_days=round(staleness_seconds / SECONDS_PER_DAY, 3),
    )


def build_staleness_report(  # noqa: PLR0913
    *,
    repos: tuple[ModelPromotionStalenessRepo, ...],
    evaluated_at: datetime,
    source_branch: str,
    target_branch: str,
    failure_class: EnumPromotionFailureState,
    linear_ticket_threshold_days: int = LINEAR_TICKET_THRESHOLD_DAYS,
    slack_alert_threshold_days: int = SLACK_ALERT_THRESHOLD_DAYS,
) -> ModelPromotionStalenessReport:
    """Build a deterministic staleness report and notification plan."""
    max_staleness_days = max((repo.staleness_days for repo in repos), default=0.0)
    stale_repos = tuple(
        repo for repo in repos if repo.staleness_days > linear_ticket_threshold_days
    )
    requires_linear_ticket = bool(stale_repos)
    requires_slack_alert = any(
        repo.staleness_days > slack_alert_threshold_days for repo in repos
    )
    route = FAILURE_ROUTES[failure_class]
    title = None
    description = None
    slack_message = None

    if requires_linear_ticket:
        title = (
            "P1: dev/main promotion staleness exceeds "
            f"{linear_ticket_threshold_days} days"
        )
        rows = "\n".join(
            "- "
            f"{repo.repo}: {repo.staleness_days}d stale, "
            f"{repo.unpromoted_commit_count} unpromoted commit(s), "
            f"{repo.oldest_unpromoted_commit_sha or 'n/a'}"
            for repo in sorted(stale_repos, key=lambda item: item.repo)
        )
        description = (
            "<!-- source: OMN-11738 -->\n\n"
            f"Main is behind dev beyond the {linear_ticket_threshold_days}-day "
            "promotion staleness threshold.\n\n"
            f"Failure class: `{failure_class.value}`\n"
            f"Alert severity: `{route.severity.value}`\n"
            f"Action: {route.action}\n\n"
            "## Stale repositories\n\n"
            f"{rows}\n"
        )

    if requires_slack_alert:
        slack_message = (
            f"Promotion staleness is {max_staleness_days}d "
            f"({failure_class.value}, {route.severity.value}). "
            f"Route: {route.slack_channel_hint}."
        )

    return ModelPromotionStalenessReport(
        evaluated_at=evaluated_at.astimezone(UTC),
        source_branch=source_branch,
        target_branch=target_branch,
        linear_ticket_threshold_days=linear_ticket_threshold_days,
        slack_alert_threshold_days=slack_alert_threshold_days,
        failure_class=failure_class,
        alert_route=route,
        max_staleness_days=max_staleness_days,
        stale_repo_count=len(stale_repos),
        requires_linear_ticket=requires_linear_ticket,
        requires_slack_alert=requires_slack_alert,
        linear_ticket_title=title,
        linear_ticket_description=description,
        slack_message=slack_message,
        repos=repos,
    )


def generate_staleness_report(  # noqa: PLR0913
    *,
    workspace: Path,
    repos: tuple[str, ...],
    source_branch: str,
    target_branch: str,
    failure_class: EnumPromotionFailureState,
    evaluated_at: datetime | None = None,
) -> ModelPromotionStalenessReport:
    """Generate a dev/main staleness report from local repo checkouts."""
    now = evaluated_at or datetime.now(UTC)
    measurements = tuple(
        measure_repo_staleness(
            repo_path=workspace / repo,
            repo=repo,
            source_branch=source_branch,
            target_branch=target_branch,
            now=now,
        )
        for repo in repos
    )
    return build_staleness_report(
        repos=measurements,
        evaluated_at=now,
        source_branch=source_branch,
        target_branch=target_branch,
        failure_class=failure_class,
    )


def _parse_repos(values: list[str]) -> tuple[str, ...]:
    if not values:
        return DEFAULT_PROMOTION_REPOS
    repos: list[str] = []
    for value in values:
        repos.extend(item.strip() for item in value.split(",") if item.strip())
    if not repos:
        return DEFAULT_PROMOTION_REPOS
    return tuple(dict.fromkeys(repos))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    report = subparsers.add_parser("report")
    report.add_argument("--workspace", type=Path, required=True)
    report.add_argument("--repo", action="append", default=[])
    report.add_argument("--source-branch", default="dev")
    report.add_argument("--target-branch", default="main")
    report.add_argument(
        "--failure-class",
        choices=[item.value for item in EnumPromotionFailureState],
        default=EnumPromotionFailureState.INTEGRATION.value,
    )
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--evaluated-at")
    return parser.parse_args()


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return _parse_commit_at(value)


def main() -> int:
    """CLI entrypoint for promotion staleness evidence."""
    args = _parse_args()

    if args.command == "report":
        payload = generate_staleness_report(
            workspace=args.workspace,
            repos=_parse_repos(args.repo),
            source_branch=args.source_branch,
            target_branch=args.target_branch,
            failure_class=EnumPromotionFailureState(args.failure_class),
            evaluated_at=_parse_optional_datetime(args.evaluated_at),
        )
        write_json(args.output, payload)
        return 0

    msg = f"unknown command: {args.command}"
    raise ValueError(msg)


def report_outputs(report_path: Path) -> dict[str, Any]:
    """Return workflow-output scalar values for a report JSON file."""
    data = json.loads(report_path.read_text())
    return {
        "requires_linear_ticket": str(data["requires_linear_ticket"]).lower(),
        "requires_slack_alert": str(data["requires_slack_alert"]).lower(),
        "linear_ticket_title": data.get("linear_ticket_title") or "",
        "max_staleness_days": str(data["max_staleness_days"]),
    }
