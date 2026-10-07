# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Staleness monitor for dev-to-main promotion lag."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
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


RELEASE_WORKFLOW_PATH = Path(".github") / "workflows" / "release.yml"

#: Posture reasons. Read, never listed: an exclusion keyed on a repository
#: NAME is a table that goes stale, and a stale exclusion table is the defect
#: this module was changed to remove (OMN-18817).
POSTURE_SYNCED = "release_workflow_syncs_target"
POSTURE_NO_WORKFLOW = "no_release_workflow"
POSTURE_NO_SYNC_STEP = "release_workflow_has_no_target_sync_step"
POSTURE_UNREADABLE = "release_workflow_unreadable"


def _step_text(step: object) -> str:
    """Every string a step could carry a ref update in, joined."""
    if not isinstance(step, dict):
        return ""
    parts: list[str] = []
    run = step.get("run")
    if isinstance(run, str):
        parts.append(run)
    with_block = step.get("with")
    if isinstance(with_block, dict):
        parts.extend(str(value) for value in with_block.values())
    return "\n".join(parts)


def _read_release_workflow(repo_path: Path, source_branch: str | None) -> str | None:
    """The release workflow's text, read from the SOURCE branch when named.

    Reading the working tree would read whatever branch happens to be
    checked out. That is the exact trap this function exists to avoid: a
    repo whose target branch is months behind has a months-old copy of its
    own release workflow there, so a posture read against it can report
    that a sync step does not exist when the source branch has carried one
    for some time. `git show <ref>:<path>` is exact and independent of the
    checkout.
    """
    if source_branch is not None:
        for ref in (f"origin/{source_branch}", source_branch):
            completed = subprocess.run(  # noqa: S603  Why: fixed argv, no shell.
                [  # noqa: S607  Why: `git` from PATH, repo convention.
                    "git",
                    "-C",
                    str(repo_path),
                    "show",
                    f"{ref}:{RELEASE_WORKFLOW_PATH.as_posix()}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if completed.returncode == 0:
                return completed.stdout
        return None
    workflow = repo_path / RELEASE_WORKFLOW_PATH
    if not workflow.is_file():
        return None
    try:
        return workflow.read_text(encoding="utf-8")
    except OSError:
        return None


def resolve_release_sync_posture(
    *, repo_path: Path, target_branch: str, source_branch: str | None = None
) -> tuple[bool, str]:
    """Is this checkout's target branch advanced by its own release workflow?

    Answered STRUCTURALLY, from the parsed workflow rather than from a text
    search: a release workflow can carry ``refs/heads/<target>`` in a header
    comment describing a mechanism it no longer has, and a substring match
    cannot tell that apart from a workflow that moves the ref. Only a step's
    own ``run`` body or ``with`` values count.

    Fails CLOSED. A workflow that cannot be parsed returns ``True`` — an
    exclusion is an excuse and an unproven one is not granted, so the repo
    stays in the failure set with its reason recorded.

    Returns ``(is_release_synced, reason)``.
    """
    text = _read_release_workflow(repo_path, source_branch)
    if text is None:
        return False, POSTURE_NO_WORKFLOW
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return True, POSTURE_UNREADABLE
    if not isinstance(document, dict):
        return True, POSTURE_UNREADABLE

    needle = f"refs/heads/{target_branch}"
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return False, POSTURE_NO_SYNC_STEP
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if needle in _step_text(step):
                return True, POSTURE_SYNCED
    return False, POSTURE_NO_SYNC_STEP


class ModelMonitorNotifier(BaseModel):
    """One notification channel's self-reported state for this run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    required: bool
    notified: bool
    reason: str | None = None
    outcome: str | None = None


class ModelMonitorVerdict(BaseModel):
    """The monitor's own pass/fail, and why.

    ``ok`` is derived from ``failures`` at construction, so a verdict cannot
    claim success while carrying a failure, nor the reverse.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    summary_lines: tuple[str, ...] = ()


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
    #: Whether this checkout's own release workflow advances the target
    #: branch. Defaults True so a caller that has not resolved posture keeps
    #: the repo in the failure set rather than silently excusing it.
    target_is_release_synced: bool = True
    release_sync_reason: str = ""


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
    unreadable_repos: tuple[str, ...] = ()


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
    unreadable_repos: tuple[str, ...] = (),
) -> ModelPromotionStalenessReport:
    """Build a deterministic staleness report and notification plan.

    ``unreadable_repos`` names repositories the monitor could not clone or
    fetch (e.g. a permissions gap or transient network failure) — OMN-15052.
    A repo the monitor cannot read is itself a staleness-monitoring blind
    spot, so its presence forces a Slack alert even when every *readable*
    repo is within threshold. The monitor must report what it could not
    read rather than silently dropping it.
    """
    max_staleness_days = max((repo.staleness_days for repo in repos), default=0.0)
    stale_repos = tuple(
        repo for repo in repos if repo.staleness_days > linear_ticket_threshold_days
    )
    requires_linear_ticket = bool(stale_repos)
    requires_slack_alert = bool(unreadable_repos) or any(
        repo.staleness_days > slack_alert_threshold_days for repo in repos
    )
    route = FAILURE_ROUTES[failure_class]
    title = None
    description = None
    slack_message = None

    unreadable_note = ""
    if unreadable_repos:
        unreadable_note = (
            "\n\n## Unreadable repositories (monitor blind spot)\n\n"
            + "\n".join(f"- {repo}" for repo in sorted(unreadable_repos))
            + "\n\nThese repositories could not be cloned/fetched this run "
            "(permissions gap or transient failure) and were excluded from "
            "the staleness measurement above — they are NOT proven fresh."
        )

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
            f"{unreadable_note}"
        )

    if requires_slack_alert:
        if any(repo.staleness_days > slack_alert_threshold_days for repo in repos):
            slack_message = (
                f"Promotion staleness is {max_staleness_days}d "
                f"({failure_class.value}, {route.severity.value}). "
                f"Route: {route.slack_channel_hint}."
            )
        else:
            slack_message = (
                "Promotion staleness monitor could not read "
                f"{len(unreadable_repos)} repo(s): "
                f"{', '.join(sorted(unreadable_repos))}. Staleness for these "
                "repos is UNKNOWN, not clean."
            )
        if unreadable_repos and "could not read" not in slack_message:
            slack_message += f" Also unreadable: {', '.join(sorted(unreadable_repos))}."

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
        unreadable_repos=tuple(sorted(unreadable_repos)),
    )


def evaluate_monitor_verdict(
    *,
    report: ModelPromotionStalenessReport,
    notifiers: tuple[ModelMonitorNotifier, ...],
    report_generated: bool,
) -> ModelMonitorVerdict:
    """Decide whether the monitor itself passed, and say why (OMN-18817).

    The inversion this function carries is the whole point. Before it, the
    job failed when a required notification could not confirm itself and
    never failed on staleness at all -- so with both channels unwired it was
    red on every run, for 118 days, while the number it exists to publish
    went unread. Now:

    * a required notifier that did not fire is a WARNING naming its reason;
    * a repo past the threshold whose target branch IS advanced by its own
      release workflow is the FAILURE;
    * a repo past the threshold whose target branch is NOT so advanced is a
      WARNING -- its gap is an absent mechanism, not an unpromoted backlog,
      and it is reported rather than dropped;
    * a report that could not be generated is still a failure, because
      measuring is the one piece of plumbing that IS the job.

    ``ok`` is derived from ``failures``, never asserted alongside them.
    """
    failures: list[str] = []
    warnings: list[str] = []
    summary: list[str] = []

    if not report_generated:
        failures.append(
            "staleness report generation did not succeed; the monitor "
            "measured nothing this run"
        )

    for notifier in notifiers:
        if notifier.required and not notifier.notified:
            warnings.append(
                f"notifier {notifier.name} did not confirm "
                f"(reason={notifier.reason or 'unknown'}, "
                f"outcome={notifier.outcome or 'unknown'}). Alerting is "
                "degraded; the staleness verdict below is unaffected."
            )

    if report.unreadable_repos:
        warnings.append(
            f"{len(report.unreadable_repos)} repo(s) could not be read this "
            f"run: {', '.join(report.unreadable_repos)}. Their staleness is "
            "UNKNOWN, not clean."
        )

    threshold = report.linear_ticket_threshold_days
    over = tuple(
        repo
        for repo in sorted(report.repos, key=lambda item: -item.staleness_days)
        if repo.staleness_days > threshold
    )
    for repo in over:
        line = (
            f"{repo.repo}: {repo.staleness_days}d behind "
            f"{repo.target_branch}, {repo.unpromoted_commit_count} "
            "unpromoted commit(s)"
        )
        if repo.target_is_release_synced:
            failures.append(
                f"{line} — past the {threshold}-day promotion threshold "
                f"and its release workflow does advance {repo.target_branch}, "
                "so this is a real unreleased backlog"
            )
        else:
            warnings.append(
                f"{line} — excluded from the verdict: "
                f"{repo.release_sync_reason or POSTURE_NO_SYNC_STEP}. The gap "
                "is an absent mechanism, not an unpromoted backlog"
            )

    summary.append(f"max_staleness_days: {report.max_staleness_days}")
    summary.append(f"threshold_days: {threshold}")
    for repo in sorted(report.repos, key=lambda item: -item.staleness_days):
        summary.append(
            f"- {repo.repo}: {repo.staleness_days}d, "
            f"{repo.unpromoted_commit_count} unpromoted, "
            f"release_synced={repo.target_is_release_synced} "
            f"({repo.release_sync_reason or 'unresolved'})"
        )

    return ModelMonitorVerdict(
        ok=not failures,
        failures=tuple(failures),
        warnings=tuple(warnings),
        summary_lines=tuple(summary),
    )


def generate_staleness_report(  # noqa: PLR0913
    *,
    workspace: Path,
    repos: tuple[str, ...],
    source_branch: str,
    target_branch: str,
    failure_class: EnumPromotionFailureState,
    evaluated_at: datetime | None = None,
    unreadable_repos: tuple[str, ...] = (),
) -> ModelPromotionStalenessReport:
    """Generate a dev/main staleness report from local repo checkouts.

    One repo's git history being unreadable must not blind the whole run
    (OMN-15052) — each repo is measured independently and a failure is
    recorded rather than raised, so the report still fires for every repo
    that *did* resolve.
    """
    now = evaluated_at or datetime.now(UTC)
    measurements: list[ModelPromotionStalenessRepo] = []
    unreadable: list[str] = list(unreadable_repos)
    for repo in repos:
        try:
            measured = measure_repo_staleness(
                repo_path=workspace / repo,
                repo=repo,
                source_branch=source_branch,
                target_branch=target_branch,
                now=now,
            )
        except (RuntimeError, subprocess.CalledProcessError, OSError):
            unreadable.append(repo)
            continue
        # Posture is resolved from the checkout that was just measured, so
        # the exclusion and the measurement can never describe different
        # trees.
        synced, reason = resolve_release_sync_posture(
            repo_path=workspace / repo,
            target_branch=target_branch,
            source_branch=source_branch,
        )
        measurements.append(
            measured.model_copy(
                update={
                    "target_is_release_synced": synced,
                    "release_sync_reason": reason,
                }
            )
        )
    return build_staleness_report(
        repos=tuple(measurements),
        evaluated_at=now,
        source_branch=source_branch,
        target_branch=target_branch,
        failure_class=failure_class,
        unreadable_repos=tuple(dict.fromkeys(unreadable)),
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
    report.add_argument(
        "--unreadable-repo",
        action="append",
        default=[],
        help=(
            "Repo that failed to clone/fetch before this command ran; "
            "recorded in the report as a monitor blind spot instead of "
            "silently dropped (OMN-15052)."
        ),
    )
    report.add_argument("--source-branch", default="dev")
    report.add_argument("--target-branch", default="main")
    report.add_argument(
        "--failure-class",
        choices=[item.value for item in EnumPromotionFailureState],
        default=EnumPromotionFailureState.INTEGRATION.value,
    )
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--evaluated-at")

    verdict = subparsers.add_parser("verdict")
    verdict.add_argument("--report", type=Path, required=True)
    verdict.add_argument(
        "--notifier",
        action="append",
        default=[],
        help=(
            "One channel's self-reported state as "
            "name:required:notified:reason:outcome. Repeatable. A channel "
            "that did not confirm is a WARNING, never a failure (OMN-18817)."
        ),
    )
    verdict.add_argument(
        "--report-generated",
        default="true",
        help="false when the measurement step itself did not succeed.",
    )
    return parser.parse_args()


def _parse_notifier(value: str) -> ModelMonitorNotifier:
    parts = value.split(":", 4)
    while len(parts) < 5:  # noqa: PLR2004  Why: the five declared fields.
        parts.append("")
    name, required, notified, reason, outcome = parts
    return ModelMonitorNotifier(
        name=name or "unnamed",
        required=required.strip().casefold() == "true",
        notified=notified.strip().casefold() == "true",
        reason=reason or None,
        outcome=outcome or None,
    )


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
            unreadable_repos=tuple(dict.fromkeys(args.unreadable_repo)),
        )
        write_json(args.output, payload)
        return 0

    if args.command == "verdict":
        report = ModelPromotionStalenessReport.model_validate_json(
            args.report.read_text(encoding="utf-8")
        )
        outcome = evaluate_monitor_verdict(
            report=report,
            notifiers=tuple(_parse_notifier(item) for item in args.notifier),
            report_generated=args.report_generated.strip().casefold() != "false",
        )
        # `sys.stdout.write` rather than `print`: this module is `src/`
        # library code, where the repo's lint forbids `print`, and the
        # workflow reads these lines as GitHub annotations.
        for line in outcome.summary_lines:
            sys.stdout.write(f"{line}\n")
        for warning in outcome.warnings:
            sys.stdout.write(f"::warning::{warning}\n")
        for failure in outcome.failures:
            sys.stdout.write(f"::error::{failure}\n")
        if outcome.ok:
            sys.stdout.write(
                "Monitor verdict: PASS - no release-synced repository is past "
                "the promotion threshold.\n"
            )
            return 0
        return 1

    msg = f"unknown command: {args.command}"
    raise ValueError(msg)


def report_outputs(report_path: Path) -> dict[str, Any]:
    """Return workflow-output scalar values for a report JSON file."""
    data = json.loads(report_path.read_text())
    unreadable_repos = data.get("unreadable_repos") or []
    return {
        "requires_linear_ticket": str(data["requires_linear_ticket"]).lower(),
        "requires_slack_alert": str(data["requires_slack_alert"]).lower(),
        "linear_ticket_title": data.get("linear_ticket_title") or "",
        "max_staleness_days": str(data["max_staleness_days"]),
        "unreadable_repo_count": str(len(unreadable_repos)),
        "unreadable_repos": ",".join(unreadable_repos),
    }
