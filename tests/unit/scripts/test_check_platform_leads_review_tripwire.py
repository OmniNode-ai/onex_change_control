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
