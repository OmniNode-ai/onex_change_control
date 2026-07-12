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
    TripwireInconclusiveError,
    _diagnose,
    evaluate,
    get_team_member_count,
    is_review_required,
    main,
)

pytestmark = pytest.mark.unit


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
