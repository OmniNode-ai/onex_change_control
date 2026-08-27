# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_platform_leads_review_tripwire (OMN-14445).

OMN-14441's self-approval check (`approved_by != PR-author`) is a complete
defense against forged approvals ONLY because `@platform-leads` has exactly
one member today. This tripwire is the mechanism that must fail loudly the
moment that assumption stops holding, instead of letting it expire silently.
These tests isolate the pure decision logic (`evaluate`) from the `gh api`
I/O (`get_team_member_count` / `is_review_required`) so the safety-critical
branching is provable without a live GitHub call, and separately prove the
I/O layer surfaces API failures as INCONCLUSIVE rather than a silent pass.
"""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from onex_change_control.scripts.check_platform_leads_review_tripwire import (
    GH_COMMAND_TIMEOUT_SECONDS,
    GH_MAX_ATTEMPTS,
    GH_SECONDARY_RATE_LIMIT_MIN_WAIT_SECONDS,
    EnumGhFailureClass,
    GiveUpContext,
    TripwireDeferredRateLimitError,
    TripwireInconclusiveError,
    _diagnose,
    _run_gh_checked,
    classify_gh_failure,
    evaluate,
    get_team_member_count,
    is_review_required,
    main,
    seconds_until_core_reset,
)

pytestmark = pytest.mark.unit

#: The VERBATIM stderr GitHub returned on onex_change_control jobs
#: 98501448095 and 98499618819 (2026-08-27), which wedged OCC PRs #7279 and
#: #7280 while the old diagnostic blamed a missing read:org scope. Pinned as
#: a literal so a future refactor of the classifier cannot silently
#: reintroduce the misdiagnosis (OMN-16373).
PRODUCTION_RATE_LIMIT_STDERR = (
    "gh: API rate limit exceeded for user ID 1002253. If you reach out to "
    "GitHub Support for help, please include the request ID "
    "AC20:1A1545:B0D215:24E578E:6A901FB3 and timestamp 2026-08-27 11:29:55 "
    "UTC. For more on scraping GitHub and how it may affect your rights, "
    "please review our Terms of Service "
    "(https://docs.github.com/en/site-policy/github-terms/"
    "github-terms-of-service) (HTTP 403)"
)

SECONDARY_RATE_LIMIT_STDERR = (
    "gh: You have exceeded a secondary rate limit and have been temporarily "
    "blocked from content creation. Please retry your request again later. "
    "(HTTP 403)"
)


def _completed(
    *, returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestEvaluateDecisionLogicOMN14445:
    """Pure logic — no subprocess involved."""

    def test_review_required_passes_regardless_of_member_count(self) -> None:
        safe, message = evaluate(
            member_count=5,
            review_required=True,
            org="OmniNode-ai",
            team="platform-leads",
        )
        assert safe is True
        assert "PASS" in message

    def test_single_member_and_no_review_required_passes_by_construction(self) -> None:
        safe, message = evaluate(
            member_count=1,
            review_required=False,
            org="OmniNode-ai",
            team="platform-leads",
        )
        assert safe is True
        assert "by construction" in message

    def test_zero_members_and_no_review_required_passes(self) -> None:
        # Degenerate but not the failure mode this tripwire targets.
        safe, _message = evaluate(
            member_count=0,
            review_required=False,
            org="OmniNode-ai",
            team="platform-leads",
        )
        assert safe is True

    def test_two_members_and_no_review_required_trips(self) -> None:
        safe, message = evaluate(
            member_count=2,
            review_required=False,
            org="OmniNode-ai",
            team="platform-leads",
        )
        assert safe is False
        assert "TRIPWIRE TRIPPED" in message

    def test_many_members_and_no_review_required_trips(self) -> None:
        safe, message = evaluate(
            member_count=7,
            review_required=False,
            org="OmniNode-ai",
            team="platform-leads",
        )
        assert safe is False
        assert "TRIPWIRE TRIPPED" in message


class TestGetTeamMemberCountOMN14445:
    def test_parses_member_count_from_gh_api(self) -> None:
        with mock.patch(
            "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
            return_value=_completed(returncode=0, stdout="1\n"),
        ):
            assert get_team_member_count("OmniNode-ai", "platform-leads") == 1

    def test_gh_api_failure_raises_inconclusive_not_silent_pass(self) -> None:
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
                return_value=_completed(
                    returncode=1, stderr="HTTP 403: Resource not accessible"
                ),
            ),
            pytest.raises(TripwireInconclusiveError, match="could not read membership"),
        ):
            get_team_member_count("OmniNode-ai", "platform-leads")

    def test_unparseable_output_raises_inconclusive(self) -> None:
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
                return_value=_completed(returncode=0, stdout="not-a-number"),
            ),
            pytest.raises(
                TripwireInconclusiveError, match="unexpected member-count output"
            ),
        ):
            get_team_member_count("OmniNode-ai", "platform-leads")


class TestIsReviewRequiredOMN14445:
    def test_true_when_key_present(self) -> None:
        with mock.patch(
            "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
            return_value=_completed(returncode=0, stdout="true\n"),
        ):
            assert is_review_required("OmniNode-ai/onex_change_control", "dev") is True

    def test_false_when_key_absent(self) -> None:
        with mock.patch(
            "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
            return_value=_completed(returncode=0, stdout="false\n"),
        ):
            assert is_review_required("OmniNode-ai/onex_change_control", "dev") is False

    def test_gh_api_failure_raises_inconclusive_not_silent_pass(self) -> None:
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
                return_value=_completed(returncode=1, stderr="HTTP 404"),
            ),
            pytest.raises(
                TripwireInconclusiveError, match="could not read branch protection"
            ),
        ):
            is_review_required("OmniNode-ai/onex_change_control", "dev")


class TestDiagnoseTokenSourceOMN14445:
    """OMN-14445 review: an INCONCLUSIVE gate must be legible, not a mystery.

    The wedge risk is real: unlike this repo's other cross-repo `gh` usage
    (public-repo clones that work with no token at all), these two API reads
    are ORG-PRIVATE with no unauthenticated fallback. If CROSS_REPO_PAT ever
    lapses, this job goes INCONCLUSIVE on every PR. These tests prove the
    diagnostic names "token problem" before the raw gh error, for each
    credential_origin case, so that failure reads as a token issue, not a
    platform-leads policy violation.
    """

    def test_fallback_names_token_problem_first(self) -> None:
        msg = _diagnose("could not read membership of x/y", "HTTP 403", "fallback")
        assert "TOKEN PROBLEM, NOT A POLICY VIOLATION" in msg
        assert msg.index("TOKEN PROBLEM") < msg.index("HTTP 403")
        assert "CROSS_REPO_PAT" in msg
        assert "fork-originated PR" in msg

    def test_cross_repo_pat_present_but_failing_names_scope_problem(self) -> None:
        msg = _diagnose(
            "could not read membership of x/y", "HTTP 403", "cross_repo_pat"
        )
        assert "TOKEN PROBLEM, NOT A POLICY VIOLATION" in msg
        assert "read:org scope" in msg

    def test_unknown_credential_origin_still_flags_possible_token_problem(self) -> None:
        msg = _diagnose("could not read membership of x/y", "HTTP 403", "unknown")
        assert "possible token problem" in msg

    def test_raw_gh_error_always_preserved(self) -> None:
        msg = _diagnose("could not read membership of x/y", "HTTP 403", "fallback")
        assert "could not read membership of x/y" in msg
        assert "HTTP 403" in msg


class TestGetTeamMemberCountTokenSourceOMN14445:
    def test_inconclusive_message_carries_credential_origin_diagnostic(self) -> None:
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
                return_value=_completed(returncode=1, stderr="HTTP 403"),
            ),
            pytest.raises(TripwireInconclusiveError, match="TOKEN PROBLEM"),
        ):
            get_team_member_count(
                "OmniNode-ai", "platform-leads", credential_origin="fallback"
            )


class TestClassifyGhFailureOMN16373:
    """GitHub returns HTTP 403 for BOTH "no scope" and "rate limited".

    Classifying on the status code is what produced the misdiagnosis these
    tests exist to prevent. Classification must come from the error body.
    """

    def test_production_rate_limit_stderr_classifies_as_rate_limited(self) -> None:
        assert (
            classify_gh_failure(PRODUCTION_RATE_LIMIT_STDERR)
            is EnumGhFailureClass.RATE_LIMITED
        )

    def test_secondary_rate_limit_classifies_as_rate_limited(self) -> None:
        assert (
            classify_gh_failure("You have exceeded a secondary rate limit (HTTP 403)")
            is EnumGhFailureClass.RATE_LIMITED
        )

    def test_genuine_scope_failure_still_classifies_as_permission(self) -> None:
        assert (
            classify_gh_failure("gh: Resource not accessible by integration (HTTP 403)")
            is EnumGhFailureClass.PERMISSION
        )

    def test_bad_credentials_classifies_as_permission(self) -> None:
        assert (
            classify_gh_failure("gh: Bad credentials (HTTP 401)")
            is EnumGhFailureClass.PERMISSION
        )

    def test_service_unavailable_classifies_as_transient(self) -> None:
        assert (
            classify_gh_failure("gh: No server is currently available (HTTP 503)")
            is EnumGhFailureClass.TRANSIENT
        )

    def test_unrecognised_error_is_unclassified_not_guessed(self) -> None:
        assert (
            classify_gh_failure("gh: something entirely new (HTTP 418)")
            is EnumGhFailureClass.UNCLASSIFIED
        )


class TestDiagnoseDoesNotBlameScopeForRateLimitOMN16373:
    """The regression this ticket exists to kill.

    A rate-limited read reported as "the PAT lacks read:org scope" sent at
    least five separate lanes to investigate a credential that was working.
    """

    def test_rate_limit_diagnosis_does_not_advise_checking_scopes(self) -> None:
        msg = _diagnose(
            "could not read membership of OmniNode-ai/platform-leads",
            PRODUCTION_RATE_LIMIT_STDERR,
            "cross_repo_pat",
            EnumGhFailureClass.RATE_LIMITED,
        )
        assert "RATE LIMIT, NOT A SCOPE PROBLEM" in msg
        assert "lacks read:org scope" not in msg
        assert "Do NOT rotate the PAT" in msg

    def test_rate_limit_diagnosis_leads_with_cause_then_raw_error(self) -> None:
        msg = _diagnose(
            "could not read membership of OmniNode-ai/platform-leads",
            PRODUCTION_RATE_LIMIT_STDERR,
            "cross_repo_pat",
            EnumGhFailureClass.RATE_LIMITED,
        )
        assert msg.index("RATE LIMIT") < msg.index("API rate limit exceeded")
        assert PRODUCTION_RATE_LIMIT_STDERR in msg

    def test_transient_diagnosis_does_not_advise_checking_scopes(self) -> None:
        msg = _diagnose(
            "could not read branch protection for x/y",
            "gh: No server is currently available (HTTP 503)",
            "cross_repo_pat",
            EnumGhFailureClass.TRANSIENT,
        )
        assert "TRANSIENT GITHUB API FAILURE" in msg
        assert "lacks read:org scope" not in msg

    def test_permission_failure_still_gets_the_scope_advice(self) -> None:
        # The scope hypothesis is correct for THIS class — the fix narrows it,
        # it does not remove it.
        msg = _diagnose(
            "could not read membership of x/y",
            "gh: Bad credentials (HTTP 401)",
            "cross_repo_pat",
            EnumGhFailureClass.PERMISSION,
        )
        assert "read:org scope" in msg


class TestGiveUpDescriptionIsHonestOMN16373:
    """The diagnostic must not overstate its own effort.

    Caught on the first live CI run of this fix: the message claimed it
    "already retried up to 4 times" after giving up on attempt 1 because the
    bucket's reset was hours away. Inflating the effort in a failure message
    is the same class of defect as misnaming the cause.
    """

    def test_single_attempt_is_not_reported_as_a_full_retry_budget(self) -> None:
        msg = _diagnose(
            "could not read membership of x/y",
            PRODUCTION_RATE_LIMIT_STDERR,
            "cross_repo_pat",
            EnumGhFailureClass.RATE_LIMITED,
            give_up=GiveUpContext(attempts_made=1, reset_wait_seconds=3600.0),
        )
        assert "Gave up after 1 attempt." not in msg  # must explain WHY, not just count
        assert "Gave up after 1 attempt" in msg
        assert f"retried up to {GH_MAX_ATTEMPTS} times" not in msg
        assert "does not reset for ~60 min" in msg

    def test_exhausted_budget_says_so_explicitly(self) -> None:
        msg = _diagnose(
            "could not read membership of x/y",
            PRODUCTION_RATE_LIMIT_STDERR,
            "cross_repo_pat",
            EnumGhFailureClass.RATE_LIMITED,
            give_up=GiveUpContext(attempts_made=GH_MAX_ATTEMPTS),
        )
        assert f"Gave up after {GH_MAX_ATTEMPTS} attempts" in msg
        assert "full retry budget" in msg

    def test_give_up_detail_is_omitted_when_attempt_count_is_unknown(self) -> None:
        msg = _diagnose(
            "could not read membership of x/y",
            PRODUCTION_RATE_LIMIT_STDERR,
            "cross_repo_pat",
            EnumGhFailureClass.RATE_LIMITED,
        )
        assert "Gave up after" not in msg
        assert "RATE LIMIT, NOT A SCOPE PROBLEM" in msg


class TestRunGhCheckedRetryOMN16373:
    """Retry is bounded, class-aware, and still fail-closed."""

    def test_rate_limited_call_is_retried_then_succeeds(self) -> None:
        attempts = [
            _completed(returncode=1, stderr=PRODUCTION_RATE_LIMIT_STDERR),
            _completed(returncode=0, stdout="1\n"),
        ]
        slept: list[float] = []
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
                side_effect=attempts,
            ),
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.seconds_until_core_reset",
                return_value=None,
            ),
        ):
            result = _run_gh_checked(
                ["api", "orgs/x/teams/y/members"],
                action="could not read membership of x/y",
                credential_origin="cross_repo_pat",
                sleep=slept.append,
            )
        assert result.stdout.strip() == "1"
        assert len(slept) == 1

    def test_permission_failure_is_not_retried(self) -> None:
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
                return_value=_completed(
                    returncode=1, stderr="gh: Bad credentials (HTTP 401)"
                ),
            ) as mock_run,
            pytest.raises(TripwireInconclusiveError),
        ):
            _run_gh_checked(
                ["api", "orgs/x/teams/y/members"],
                action="could not read membership of x/y",
                credential_origin="cross_repo_pat",
                sleep=lambda _seconds: None,
            )
        assert mock_run.call_count == 1

    def test_persistent_rate_limit_still_fails_closed_after_budget(self) -> None:
        slept: list[float] = []
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
                return_value=_completed(
                    returncode=1, stderr=PRODUCTION_RATE_LIMIT_STDERR
                ),
            ) as mock_run,
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.seconds_until_core_reset",
                return_value=None,
            ),
            pytest.raises(TripwireInconclusiveError, match="RATE LIMIT"),
        ):
            _run_gh_checked(
                ["api", "orgs/x/teams/y/members"],
                action="could not read membership of x/y",
                credential_origin="cross_repo_pat",
                sleep=slept.append,
            )
        assert mock_run.call_count == GH_MAX_ATTEMPTS
        assert len(slept) == GH_MAX_ATTEMPTS - 1

    def test_reset_further_away_than_budget_gives_up_immediately(self) -> None:
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
                return_value=_completed(
                    returncode=1, stderr=PRODUCTION_RATE_LIMIT_STDERR
                ),
            ) as mock_run,
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.seconds_until_core_reset",
                return_value=3600.0,
            ),
            pytest.raises(TripwireInconclusiveError, match="RATE LIMIT"),
        ):
            _run_gh_checked(
                ["api", "orgs/x/teams/y/members"],
                action="could not read membership of x/y",
                credential_origin="cross_repo_pat",
                sleep=lambda _seconds: None,
            )
        assert mock_run.call_count == 1

    def test_secondary_rate_limit_uses_one_minute_minimum_without_core_probe(
        self,
    ) -> None:
        attempts = [
            _completed(returncode=1, stderr=SECONDARY_RATE_LIMIT_STDERR),
            _completed(returncode=0, stdout="1\n"),
        ]
        slept: list[float] = []
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
                side_effect=attempts,
            ),
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.seconds_until_core_reset",
            ) as mock_core_reset,
        ):
            result = _run_gh_checked(
                ["api", "orgs/x/teams/y/members"],
                action="could not read membership of x/y",
                credential_origin="cross_repo_pat",
                sleep=slept.append,
            )

        assert result.stdout.strip() == "1"
        assert slept == [GH_SECONDARY_RATE_LIMIT_MIN_WAIT_SECONDS]
        mock_core_reset.assert_not_called()

    def test_shared_deadline_gives_up_before_sleeping_past_workflow_budget(
        self,
    ) -> None:
        slept: list[float] = []
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
                return_value=_completed(
                    returncode=1, stderr=SECONDARY_RATE_LIMIT_STDERR
                ),
            ) as mock_run,
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.time.monotonic",
                return_value=1000.0,
            ),
            pytest.raises(TripwireInconclusiveError, match="RATE LIMIT"),
        ):
            _run_gh_checked(
                ["api", "orgs/x/teams/y/members"],
                action="could not read membership of x/y",
                credential_origin="cross_repo_pat",
                sleep=slept.append,
                deadline=1000.0
                + GH_SECONDARY_RATE_LIMIT_MIN_WAIT_SECONDS
                + GH_COMMAND_TIMEOUT_SECONDS,
            )

        assert mock_run.call_count == 1
        assert slept == []


class TestSecondsUntilCoreResetOMN16373:
    def test_parses_reset_epoch(self) -> None:
        payload = '{"resources":{"core":{"limit":5000,"remaining":0,"reset":1000}}}'
        with mock.patch(
            "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
            return_value=_completed(returncode=0, stdout=payload),
        ):
            assert seconds_until_core_reset(now=940.0) == 60.0

    def test_already_reset_clamps_to_zero(self) -> None:
        payload = '{"resources":{"core":{"reset":1000}}}'
        with mock.patch(
            "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
            return_value=_completed(returncode=0, stdout=payload),
        ):
            assert seconds_until_core_reset(now=2000.0) == 0.0

    def test_unreadable_rate_limit_returns_none_not_a_guess(self) -> None:
        with mock.patch(
            "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
            return_value=_completed(returncode=1, stderr="boom"),
        ):
            assert seconds_until_core_reset() is None

    def test_malformed_payload_returns_none(self) -> None:
        with mock.patch(
            "onex_change_control.scripts.check_platform_leads_review_tripwire._run_gh",
            return_value=_completed(returncode=0, stdout="{}"),
        ):
            assert seconds_until_core_reset() is None


class TestCliMainOMN14445:
    def test_exit_0_when_review_required(self) -> None:
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.get_team_member_count",
                return_value=3,
            ),
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.is_review_required",
                return_value=True,
            ),
        ):
            assert main([]) == 0

    def test_exit_0_when_single_member(self) -> None:
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.get_team_member_count",
                return_value=1,
            ),
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.is_review_required",
                return_value=False,
            ),
        ):
            assert main([]) == 0

    def test_exit_1_when_tripped(self) -> None:
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.get_team_member_count",
                return_value=2,
            ),
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.is_review_required",
                return_value=False,
            ),
        ):
            assert main([]) == 1

    def test_exit_2_when_inconclusive_not_exit_0(self) -> None:
        with mock.patch(
            "onex_change_control.scripts.check_platform_leads_review_tripwire.get_team_member_count",
            side_effect=TripwireInconclusiveError(
                "could not read membership: HTTP 403"
            ),
        ):
            assert main([]) == 2

    def test_exit_0_when_rate_limit_deferred(self) -> None:
        with mock.patch(
            "onex_change_control.scripts.check_platform_leads_review_tripwire.get_team_member_count",
            side_effect=TripwireDeferredRateLimitError(
                "RATE LIMIT, NOT A SCOPE PROBLEM"
            ),
        ):
            assert main([]) == 0

    def test_credential_origin_flag_threads_into_both_gh_calls(self) -> None:
        with (
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.get_team_member_count",
                return_value=1,
            ) as mock_members,
            mock.patch(
                "onex_change_control.scripts.check_platform_leads_review_tripwire.is_review_required",
                return_value=False,
            ) as mock_reviews,
        ):
            assert main(["--credential-origin", "fallback"]) == 0
            assert mock_members.call_args.kwargs["credential_origin"] == "fallback"
            assert mock_reviews.call_args.kwargs["credential_origin"] == "fallback"
