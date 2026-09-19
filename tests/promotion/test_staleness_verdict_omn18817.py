# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""The monitor's verdict is about staleness, not about its own plumbing (OMN-18817).

WHY THIS EXISTS. `Check Dev/Main Staleness` measured dev/main lag correctly
and then decided its own pass/fail on something else entirely: whether each
required notification CONFIRMED itself. Both channels are unwired -- the
Linear step's raw outcome is failure and `SLACK_WEBHOOK_URL` is empty -- so
the job failed on every run. 122 runs are retained, 2026-05-24 to
2026-09-19: 117 failure, 5 cancelled, zero success. Its own heartbeat issue
(OCC#4743) has 55 comments. It is a required context nowhere, so it never
blocked a merge; what it did was make its own measurement unreadable.

THE INVERSION THIS MODULE PINS. A notifier that is not configured is a
WARNING naming its reason. A repository that is genuinely behind beyond the
threshold is the FAILURE. Those were the wrong way round.

WHY POSTURE IS READ AND NEVER LISTED. Some repos' `main` is deliberately not
advanced by a release, so their dev/main gap is not a promotion backlog at
all and would hold the monitor red forever. Excluding them by name would
become the next stale table -- the exact failure that produced this ticket,
where a doc asserted `omnidash` had no sync step on either branch while its
`dev` had carried one for some time. Posture is therefore read from the
scanned checkout's own `release.yml`: a repo is release-synced when some
step of that workflow updates `refs/heads/<target>`.

FAIL-CLOSED. An unreadable or unparseable `release.yml` does NOT earn an
exclusion. An exclusion is an excuse and has to be proven; unproven posture
leaves the repo in the failure set.

EXCLUDED IS NOT SILENT. A repo excluded from the failure decision is still
reported, under its own reason. A silently dropped repo is how OMN-16396 sat
open while the thing it described had changed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from onex_change_control.promotion.staleness import (
    LINEAR_TICKET_THRESHOLD_DAYS,
    EnumPromotionFailureState,
    ModelMonitorNotifier,
    ModelPromotionStalenessRepo,
    build_staleness_report,
    evaluate_monitor_verdict,
    resolve_release_sync_posture,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

EVALUATED_AT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _repo(
    name: str,
    *,
    days: float,
    commits: int = 3,
    release_synced: bool = True,
    reason: str = "release_workflow_syncs_target",
) -> ModelPromotionStalenessRepo:
    return ModelPromotionStalenessRepo(
        repo=name,
        source_branch="dev",
        target_branch="main",
        source_sha="a" * 40,
        target_sha="b" * 40,
        unpromoted_commit_count=commits,
        staleness_seconds=int(days * 86_400),
        staleness_days=days,
        target_is_release_synced=release_synced,
        release_sync_reason=reason,
    )


def _report(*repos: ModelPromotionStalenessRepo, unreadable: tuple[str, ...] = ()):
    return build_staleness_report(
        repos=repos,
        evaluated_at=EVALUATED_AT,
        source_branch="dev",
        target_branch="main",
        failure_class=EnumPromotionFailureState.INTEGRATION,
        unreadable_repos=unreadable,
    )


def _unconfigured(name: str) -> ModelMonitorNotifier:
    return ModelMonitorNotifier(
        name=name,
        required=True,
        notified=False,
        reason="missing_secret",
        outcome="failure",
    )


class TestNotifierIsAWarningOMN18817:
    def test_an_unconfigured_required_notifier_does_not_fail_the_job(self) -> None:
        """AC1. This is the inversion: plumbing cannot be the verdict."""
        verdict = evaluate_monitor_verdict(
            report=_report(_repo("omnimarket", days=0.0, commits=0)),
            notifiers=(_unconfigured("linear-ticket"), _unconfigured("slack-alert")),
            report_generated=True,
        )
        assert verdict.ok is True
        assert verdict.failures == ()

    def test_the_warning_names_the_notifier_and_its_reason(self) -> None:
        """A warning nobody can act on is the same as no warning."""
        verdict = evaluate_monitor_verdict(
            report=_report(_repo("omnimarket", days=0.0, commits=0)),
            notifiers=(_unconfigured("slack-alert"),),
            report_generated=True,
        )
        joined = "\n".join(verdict.warnings)
        assert "slack-alert" in joined
        assert "missing_secret" in joined

    def test_a_notifier_that_did_fire_produces_no_warning(self) -> None:
        verdict = evaluate_monitor_verdict(
            report=_report(_repo("omnimarket", days=0.0, commits=0)),
            notifiers=(
                ModelMonitorNotifier(
                    name="linear-ticket",
                    required=True,
                    notified=True,
                    outcome="success",
                ),
            ),
            report_generated=True,
        )
        assert verdict.ok is True
        assert verdict.warnings == ()

    def test_a_failed_report_generation_is_still_a_failure(self) -> None:
        """The one piece of plumbing that IS the job: measuring at all."""
        verdict = evaluate_monitor_verdict(
            report=_report(_repo("omnimarket", days=0.0, commits=0)),
            notifiers=(),
            report_generated=False,
        )
        assert verdict.ok is False
        assert any("report" in failure for failure in verdict.failures)


class TestStalenessIsTheFailureOMN18817:
    def test_an_over_threshold_release_synced_repo_fails_the_job(self) -> None:
        """AC2."""
        verdict = evaluate_monitor_verdict(
            report=_report(_repo("omnidash", days=24.214, commits=31)),
            notifiers=(_unconfigured("slack-alert"),),
            report_generated=True,
        )
        assert verdict.ok is False
        joined = "\n".join(verdict.failures)
        assert "omnidash" in joined
        assert "24.214" in joined
        assert "31" in joined

    def test_a_repo_inside_the_threshold_does_not_fail(self) -> None:
        verdict = evaluate_monitor_verdict(
            report=_report(_repo("omnibase_core", days=1.23, commits=7)),
            notifiers=(),
            report_generated=True,
        )
        assert verdict.ok is True

    def test_the_threshold_boundary_is_strictly_greater_than(self) -> None:
        """Matches the report's own `> threshold`; equal is not yet stale."""
        exact = float(LINEAR_TICKET_THRESHOLD_DAYS)
        assert (
            evaluate_monitor_verdict(
                report=_report(_repo("omnimemory", days=exact)),
                notifiers=(),
                report_generated=True,
            ).ok
            is True
        )


class TestPostureExclusionOMN18817:
    def test_a_not_release_synced_repo_over_threshold_does_not_fail(self) -> None:
        """AC3. Its gap is an absent mechanism, not an unpromoted backlog."""
        verdict = evaluate_monitor_verdict(
            report=_report(
                _repo(
                    "onex_change_control",
                    days=100.131,
                    commits=7104,
                    release_synced=False,
                    reason="no_release_workflow",
                )
            ),
            notifiers=(),
            report_generated=True,
        )
        assert verdict.ok is True

    def test_an_excluded_repo_is_still_reported_with_its_reason(self) -> None:
        """AC3. Silently dropping it is how OMN-16396 stayed open."""
        verdict = evaluate_monitor_verdict(
            report=_report(
                _repo(
                    "onex_change_control",
                    days=100.131,
                    commits=7104,
                    release_synced=False,
                    reason="no_release_workflow",
                )
            ),
            notifiers=(),
            report_generated=True,
        )
        joined = "\n".join(verdict.warnings + verdict.summary_lines)
        assert "onex_change_control" in joined
        assert "no_release_workflow" in joined

    def test_one_excluded_repo_does_not_mask_a_real_one(self) -> None:
        """The mixed case the live fleet is actually in."""
        verdict = evaluate_monitor_verdict(
            report=_report(
                _repo(
                    "onex_change_control",
                    days=100.131,
                    commits=7104,
                    release_synced=False,
                    reason="no_release_workflow",
                ),
                _repo("omnidash", days=24.214, commits=31),
                _repo("omnibase_core", days=1.23, commits=7),
            ),
            notifiers=(_unconfigured("linear-ticket"),),
            report_generated=True,
        )
        assert verdict.ok is False
        joined = "\n".join(verdict.failures)
        assert "omnidash" in joined
        assert "onex_change_control" not in joined


class TestPostureIsReadNotListedOMN18817:
    def test_no_release_workflow_is_not_release_synced(self, tmp_path: Path) -> None:
        synced, reason = resolve_release_sync_posture(
            repo_path=tmp_path, target_branch="main"
        )
        assert synced is False
        assert reason == "no_release_workflow"

    def test_a_workflow_with_a_target_sync_step_is_release_synced(
        self, tmp_path: Path
    ) -> None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "release.yml").write_text(
            "jobs:\n"
            "  sync-main:\n"
            "    steps:\n"
            "      - name: Sync main to release tag\n"
            '        run: git push origin "${TAG_SHA}:refs/heads/main"\n'
        )
        synced, reason = resolve_release_sync_posture(
            repo_path=tmp_path, target_branch="main"
        )
        assert synced is True
        assert reason == "release_workflow_syncs_target"

    def test_a_workflow_that_only_mentions_the_ref_in_a_comment_is_not_synced(
        self, tmp_path: Path
    ) -> None:
        """The posture must be structural, not a substring over the file.

        `omnidash`'s own release.yml carries `refs/heads/main` in a header
        comment as well as in the step that moves it; a text search cannot
        tell a repo that syncs from a repo that once described syncing.
        """
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "release.yml").write_text(
            "# This workflow would update refs/heads/main, but does not.\n"
            "jobs:\n"
            "  release:\n"
            "    steps:\n"
            "      - run: echo building\n"
        )
        synced, reason = resolve_release_sync_posture(
            repo_path=tmp_path, target_branch="main"
        )
        assert synced is False
        assert reason == "release_workflow_has_no_target_sync_step"

    def test_an_unparseable_workflow_fails_closed_into_the_failure_set(
        self, tmp_path: Path
    ) -> None:
        """An exclusion is an excuse; an unproven one is not granted."""
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "release.yml").write_text("jobs: [unclosed\n")
        synced, reason = resolve_release_sync_posture(
            repo_path=tmp_path, target_branch="main"
        )
        assert synced is True
        assert reason == "release_workflow_unreadable"

    def test_the_module_carries_no_hardcoded_repo_exclusion_list(self) -> None:
        """AC4. A name list here is the defect, not the fix."""
        from pathlib import Path as _Path

        import onex_change_control.promotion.staleness as module

        source = _Path(module.__file__).read_text()
        # Quoted literals, not bare substrings: this package's own name is
        # legitimately in its import lines, and a hardcoded exclusion would
        # be a string literal.
        for repo_name in (
            "omnidash",
            "omniweb",
            "omniintelligence",
            "omninode_infra",
            "onex_change_control",
        ):
            for literal in (f'"{repo_name}"', f"'{repo_name}'"):
                assert literal not in source, (
                    f"{repo_name} is named as a literal in the staleness "
                    "module; posture must be read from each checkout, "
                    "never listed"
                )


class TestPostureReadsTheSourceBranchOMN18817:
    """The trap this closes, with a repo built to spring it.

    A repository whose target branch is far behind carries an OLD copy of
    its own release workflow there. Reading the working tree, or the target
    branch, reports that no sync step exists while the source branch has
    carried one for some time — which is precisely the stale claim that sent
    this ticket's first diagnosis the wrong way about one repo.
    """

    @staticmethod
    def _git(repo: Path, *args: str) -> None:
        """Always with a scrubbed env (OMN-14891/OMN-18434).

        Git exports GIT_DIR and friends into every hook environment and they
        override both `cwd=` and `-C`, so an unscrubbed fixture run under a
        pre-push hook rewrites the REAL worktree instead of tmp_path.
        """
        import os
        import subprocess

        from omnibase_core.validators.no_unguarded_git_subprocess import (
            scrub_git_location_env,
        )

        subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            env=scrub_git_location_env(os.environ),
        )

    def _repo_whose_target_predates_the_sync_step(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        (repo / ".github" / "workflows").mkdir(parents=True)
        self._git(repo.parent, "init", "-q", "-b", "main", str(repo))
        self._git(repo, "config", "user.email", "t@example.invalid")
        self._git(repo, "config", "user.name", "t")
        (repo / ".github" / "workflows" / "release.yml").write_text(
            "jobs:\n  release:\n    steps:\n      - run: echo building\n"
        )
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "main: no sync step yet")
        self._git(repo, "checkout", "-q", "-b", "dev")
        (repo / ".github" / "workflows" / "release.yml").write_text(
            "jobs:\n"
            "  release:\n"
            "    steps:\n"
            "      - run: echo building\n"
            "  sync-main:\n"
            "    steps:\n"
            '        - run: git push origin "${TAG}:refs/heads/main"\n'
        )
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "dev: add the sync step")
        self._git(repo, "checkout", "-q", "main")
        return repo

    def test_the_source_branch_answer_wins_over_the_checked_out_tree(
        self, tmp_path: Path
    ) -> None:
        repo = self._repo_whose_target_predates_the_sync_step(tmp_path)
        synced, reason = resolve_release_sync_posture(
            repo_path=repo, target_branch="main", source_branch="dev"
        )
        assert synced is True
        assert reason == "release_workflow_syncs_target"

    def test_and_the_checked_out_tree_alone_gives_the_wrong_answer(
        self, tmp_path: Path
    ) -> None:
        """The positive control: without the source ref this reads false."""
        repo = self._repo_whose_target_predates_the_sync_step(tmp_path)
        synced, reason = resolve_release_sync_posture(
            repo_path=repo, target_branch="main"
        )
        assert synced is False
        assert reason == "release_workflow_has_no_target_sync_step"
