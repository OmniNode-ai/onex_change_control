# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_platform_leads_review_tripwire (OMN-14445 -> OMN-18327).

The check keeps its name and loses its original subject. It used to assert a
GitHub SETTING — that `required_pull_request_reviews` existed on `dev` once
`@platform-leads` had more than one member. The 2026-09-13 operator ruling
(`docs/tracking/ROLLING_WORK_LEDGER.md:7452`), read with the standing
2026-08-28 rule that no human review is a required check until there are
full-time employees, retires that subject: change-control PRs land on machine
gates only, so a gate demanding a review setting could only be satisfied by
breaking policy.

It now asserts the MACHINE dual control that replaced the review — the
authoring-time `self_granted` refusal in this repo's grants validator, that
refusal actually being wired in `ci.yml`, and the promotion-time
`self_granted` refusal in omninode_infra's k3s gate.

These tests isolate the pure decision logic (`evaluate`) from the I/O, prove
the behavioural probe distinguishes a working refusal from one that refuses
everything, prove an ABSENT refusal is a TRIP rather than an INCONCLUSIVE,
and separately prove the I/O layer still surfaces API failures as INCONCLUSIVE
rather than a silent pass.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from unittest import mock

import pytest

from onex_change_control.scripts.check_platform_leads_review_tripwire import (
    GH_COMMAND_TIMEOUT_SECONDS,
    GH_MAX_ATTEMPTS,
    GH_SECONDARY_RATE_LIMIT_MIN_WAIT_SECONDS,
    GRANTS_VALIDATOR_JOB,
    PROMOTION_GATE_MARKERS,
    REQUESTER_FLAG,
    SELF_GRANTED_REASON,
    EnumGhFailureClass,
    GiveUpContext,
    TripwireDeferredRateLimitError,
    TripwireInconclusiveError,
    _diagnose,
    _run_gh_checked,
    authoring_time_refusal_behaves,
    authoring_time_refusal_wired,
    classify_gh_failure,
    evaluate,
    main,
    promotion_time_refusal_present,
    seconds_until_core_reset,
)

MODULE = "onex_change_control.scripts.check_platform_leads_review_tripwire"

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


class TestEvaluateDecisionLogicOMN18327:
    """Pure logic — no subprocess, no filesystem."""

    OK = (True, "detail")

    def test_all_three_facts_true_passes(self) -> None:
        safe, message = evaluate(authoring=self.OK, wired=self.OK, promotion=self.OK)
        assert safe is True
        assert "PASS" in message

    def test_missing_authoring_refusal_trips(self) -> None:
        safe, message = evaluate(
            authoring=(False, "no requester parameter"),
            wired=self.OK,
            promotion=self.OK,
        )
        assert safe is False
        assert "TRIPWIRE TRIPPED" in message
        assert "AUTHORING-TIME REFUSAL: no requester parameter" in message

    def test_unwired_authoring_refusal_trips(self) -> None:
        safe, message = evaluate(
            authoring=self.OK,
            wired=(False, "job never passes --requester"),
            promotion=self.OK,
        )
        assert safe is False
        assert "AUTHORING-TIME REFUSAL NOT WIRED" in message

    def test_missing_promotion_refusal_trips(self) -> None:
        safe, message = evaluate(
            authoring=self.OK, wired=self.OK, promotion=(False, "marker gone")
        )
        assert safe is False
        assert "PROMOTION-TIME REFUSAL: marker gone" in message

    def test_every_failing_fact_is_named_not_just_the_first(self) -> None:
        """An operator must learn all the damage in one read, not one per re-run."""
        safe, message = evaluate(
            authoring=(False, "alpha-detail"),
            wired=(False, "beta-detail"),
            promotion=(False, "gamma-detail"),
        )
        assert safe is False
        for detail in ("alpha-detail", "beta-detail", "gamma-detail"):
            assert detail in message

    def test_trip_message_forbids_re_adding_a_required_review(self) -> None:
        """The 2026-09-13 ruling is cited in the failure, not only in a docstring.

        The previous incarnation of this check was cleared by enabling a
        required review, which froze the fleet. Whoever reads this failure
        must be told, in the failure itself, that that remedy is ruled out.
        """
        _safe, message = evaluate(
            authoring=(False, "gone"), wired=self.OK, promotion=self.OK
        )
        assert "ROLLING_WORK_LEDGER.md:7452" in message
        assert "do not" in message.lower()
        assert "required review" in message.lower()

    def test_pass_message_keeps_the_honest_limit(self) -> None:
        """CLAUDE.md rule 12's limit survives the re-scope, in the green path."""
        _safe, message = evaluate(authoring=self.OK, wired=self.OK, promotion=self.OK)
        assert "no file proves a human said the words" in message
        assert "blast radius" in message


class TestAuthoringTimeRefusalBehavesOMN18327:
    """The behavioural probe, including its own built-in positive control."""

    def test_passes_when_validator_refuses_only_the_self_approval(self) -> None:
        def fake(*, approved_by: str, requester: str) -> list[str]:
            return (
                [f"Entry[0]: {SELF_GRANTED_REASON} — approved_by is requester"]
                if approved_by == requester
                else []
            )

        with mock.patch(f"{MODULE}._self_approval_errors", side_effect=fake):
            ok, detail = authoring_time_refusal_behaves()
        assert ok is True
        assert SELF_GRANTED_REASON in detail

    def test_fails_when_validator_refuses_nothing(self) -> None:
        with mock.patch(f"{MODULE}._self_approval_errors", return_value=[]):
            ok, detail = authoring_time_refusal_behaves()
        assert ok is False
        assert "did NOT refuse" in detail

    def test_fails_when_validator_refuses_everything(self) -> None:
        """A validator that refuses every grant proves nothing about approvals.

        This is the positive control, and it is part of the GATE, not only of
        this test suite: without it, "it refused" is evidence of nothing.
        """
        with mock.patch(
            f"{MODULE}._self_approval_errors",
            return_value=[f"Entry[0]: {SELF_GRANTED_REASON} — always"],
        ):
            ok, detail = authoring_time_refusal_behaves()
        assert ok is False
        assert "does not distinguish" in detail

    def test_absent_refusal_is_a_trip_not_an_inconclusive(self) -> None:
        """An absent control is a determinate fact, not an unreadable one.

        Reporting it as INCONCLUSIVE would send an operator to check a
        credential instead of to restore the missing check.
        """
        from onex_change_control.scripts.check_platform_leads_review_tripwire import (
            TripwireAuthoringRefusalAbsentError,
        )

        with mock.patch(
            f"{MODULE}._self_approval_errors",
            side_effect=TripwireAuthoringRefusalAbsentError("no requester param"),
        ):
            ok, detail = authoring_time_refusal_behaves()
        assert ok is False
        assert "no requester param" in detail

    def test_runs_the_real_validator_end_to_end(self) -> None:
        """No mocks: the real grants validator must refuse the synthetic entry.

        This is the test that actually couples this gate to OMN-17157. It is
        expected to FAIL on any revision where the self-approval refusal has
        been removed — which is the point.
        """
        ok, detail = authoring_time_refusal_behaves()
        assert ok is True, detail


class TestAuthoringTimeRefusalWiredOMN18327:
    """`ci.yml` must actually pass a requester, or the refusal is dead code."""

    def _workflow(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "ci.yml"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def test_passes_when_the_job_passes_the_requester_flag(
        self, tmp_path: Path
    ) -> None:
        workflow = self._workflow(
            tmp_path,
            f"""
            jobs:
              {GRANTS_VALIDATOR_JOB}:
                steps:
                  - run: |
                      uv run validate-prod-promotion-grants \\
                        --file grants/prod_promotion_grants.yaml \\
                        {REQUESTER_FLAG} someone
            """,
        )
        ok, detail = authoring_time_refusal_wired(workflow)
        assert ok is True
        assert GRANTS_VALIDATOR_JOB in detail

    def test_fails_when_the_job_omits_the_requester_flag(self, tmp_path: Path) -> None:
        workflow = self._workflow(
            tmp_path,
            f"""
            jobs:
              {GRANTS_VALIDATOR_JOB}:
                steps:
                  - run: uv run validate-prod-promotion-grants --file grants/x.yaml
            """,
        )
        ok, detail = authoring_time_refusal_wired(workflow)
        assert ok is False
        assert "requester=None" in detail

    def test_fails_when_the_job_is_gone_entirely(self, tmp_path: Path) -> None:
        workflow = self._workflow(
            tmp_path,
            """
            jobs:
              some-other-job:
                steps:
                  - run: echo hi
            """,
        )
        ok, detail = authoring_time_refusal_wired(workflow)
        assert ok is False
        assert "declares no" in detail

    def test_flag_in_a_different_job_does_not_satisfy_it(self, tmp_path: Path) -> None:
        """Parsed, not grepped: the flag must be in THIS job's own steps."""
        workflow = self._workflow(
            tmp_path,
            f"""
            jobs:
              {GRANTS_VALIDATOR_JOB}:
                steps:
                  - run: uv run validate-prod-promotion-grants --file grants/x.yaml
              unrelated-job:
                steps:
                  - run: echo {REQUESTER_FLAG}
            """,
        )
        ok, _detail = authoring_time_refusal_wired(workflow)
        assert ok is False

    def test_unparseable_workflow_is_inconclusive_not_a_silent_pass(
        self, tmp_path: Path
    ) -> None:
        workflow = tmp_path / "ci.yml"
        workflow.write_text("jobs: [unclosed\n", encoding="utf-8")
        with pytest.raises(TripwireInconclusiveError, match="could not parse"):
            authoring_time_refusal_wired(workflow)

    def test_missing_workflow_is_inconclusive_not_a_silent_pass(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(TripwireInconclusiveError, match="could not parse"):
            authoring_time_refusal_wired(tmp_path / "absent.yml")

    def test_the_repos_own_ci_yml_is_the_live_subject(self) -> None:
        """Not a fixture: this repo's real ci.yml must satisfy the assertion.

        Expected to FAIL on any revision where the grants-validator job does
        not pass a requester.
        """
        repo_root = Path(__file__).resolve().parents[3]
        ok, detail = authoring_time_refusal_wired(
            repo_root / ".github" / "workflows" / "ci.yml"
        )
        assert ok is True, detail


class TestPromotionTimeRefusalPresentOMN18327:
    """The cross-repo half, read over the API with the shared gh machinery."""

    def _source(self) -> str:
        return (
            "class EnumGrantOutcome(StrEnum):\n"
            '    SELF_GRANTED = "self_granted"\n'
            "if approved_by == requested_by:\n"
            "    return GrantResolution(outcome=EnumGrantOutcome.SELF_GRANTED)\n"
        )

    def test_passes_when_both_markers_present(self) -> None:
        with mock.patch(
            f"{MODULE}._run_gh",
            return_value=_completed(returncode=0, stdout=self._source()),
        ):
            ok, detail = promotion_time_refusal_present(
                "OmniNode-ai/omninode_infra", "scripts/x.py", "dev"
            )
        assert ok is True
        assert "SELF_GRANTED" in detail

    @pytest.mark.parametrize("dropped", PROMOTION_GATE_MARKERS)
    def test_fails_when_either_marker_is_missing(self, dropped: str) -> None:
        """Both markers are load-bearing; losing either must trip, not half-pass."""
        with mock.patch(
            f"{MODULE}._run_gh",
            return_value=_completed(
                returncode=0, stdout=self._source().replace(dropped, "REMOVED")
            ),
        ):
            ok, detail = promotion_time_refusal_present(
                "OmniNode-ai/omninode_infra", "scripts/x.py", "dev"
            )
        assert ok is False
        assert dropped in detail

    def test_api_failure_raises_inconclusive_not_a_silent_pass(self) -> None:
        with (
            mock.patch(
                f"{MODULE}._run_gh",
                return_value=_completed(returncode=1, stderr="HTTP 404"),
            ),
            pytest.raises(TripwireInconclusiveError, match="could not read"),
        ):
            promotion_time_refusal_present(
                "OmniNode-ai/omninode_infra", "scripts/x.py", "dev"
            )

    def test_inconclusive_message_carries_credential_origin_diagnostic(self) -> None:
        with (
            mock.patch(
                f"{MODULE}._run_gh",
                return_value=_completed(returncode=1, stderr="HTTP 403"),
            ),
            pytest.raises(TripwireInconclusiveError, match="TOKEN PROBLEM"),
        ):
            promotion_time_refusal_present(
                "OmniNode-ai/omninode_infra",
                "scripts/x.py",
                "dev",
                credential_origin="fallback",
            )

    def test_ref_is_sent_as_a_query_string_not_a_field(self) -> None:
        """`gh api --field` forces a POST; this read is a GET."""
        with mock.patch(
            f"{MODULE}._run_gh",
            return_value=_completed(returncode=0, stdout=self._source()),
        ) as runner:
            promotion_time_refusal_present(
                "OmniNode-ai/omninode_infra", "scripts/x.py", "dev"
            )
        args = runner.call_args.args[0]
        assert "?ref=dev" in args[1]
        assert "--field" not in args


class TestCliMainOMN18327:
    @staticmethod
    def _run(
        argv: list[str],
        *,
        authoring: tuple[bool, str] = (True, "ok"),
        wired: tuple[bool, str] = (True, "ok"),
        promotion: tuple[bool, str] | None = (True, "ok"),
        promotion_error: Exception | None = None,
    ) -> tuple[int, mock.MagicMock]:
        promotion_patch = (
            mock.patch(
                f"{MODULE}.promotion_time_refusal_present", side_effect=promotion_error
            )
            if promotion_error is not None
            else mock.patch(
                f"{MODULE}.promotion_time_refusal_present", return_value=promotion
            )
        )
        with (
            mock.patch(
                f"{MODULE}.authoring_time_refusal_behaves", return_value=authoring
            ),
            mock.patch(f"{MODULE}.authoring_time_refusal_wired", return_value=wired),
            promotion_patch as promotion_mock,
        ):
            return main(argv), promotion_mock

    def test_exit_0_when_all_three_hold(self) -> None:
        assert self._run([])[0] == 0

    def test_exit_1_when_authoring_refusal_absent(self) -> None:
        assert self._run([], authoring=(False, "gone"))[0] == 1

    def test_exit_1_when_refusal_unwired(self) -> None:
        assert self._run([], wired=(False, "no --requester"))[0] == 1

    def test_exit_1_when_promotion_refusal_absent(self) -> None:
        assert self._run([], promotion=(False, "marker gone"))[0] == 1

    def test_exit_2_when_inconclusive_not_exit_0(self) -> None:
        code, _ = self._run(
            [],
            promotion_error=TripwireInconclusiveError("could not read: HTTP 403"),
        )
        assert code == 2

    def test_exit_0_when_rate_limit_deferred(self) -> None:
        code, _ = self._run(
            [],
            promotion_error=TripwireDeferredRateLimitError(
                "RATE LIMIT, NOT A SCOPE PROBLEM"
            ),
        )
        assert code == 0

    def test_credential_origin_flag_threads_into_the_cross_repo_read(self) -> None:
        code, promotion_mock = self._run(["--credential-origin", "fallback"])
        assert code == 0
        assert promotion_mock.call_args.kwargs["credential_origin"] == "fallback"
