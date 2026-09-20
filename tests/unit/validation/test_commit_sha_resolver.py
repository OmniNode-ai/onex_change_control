# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Focused controls for bounded commit SHA resolution (OMN-17501)."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

from onex_change_control.validation.commit_sha_resolver import (
    CommitShaResolver,
    EnumCommitShaOutcome,
    EnumCommitShaUnavailableCategory,
)

if TYPE_CHECKING:
    from collections.abc import Callable

FULL_SHA = "a" * 40
OTHER_SHA = "b" * 40
UPPER_SHA = FULL_SHA.upper()


def _http(
    status: int,
    headers: dict[str, str] | None = None,
    body: str | None = None,
) -> str:
    header_lines = "\n".join(
        f"{name}: {value}" for name, value in (headers or {}).items()
    )
    response_body = body if body is not None else json.dumps({"sha": FULL_SHA})
    return f"HTTP/2 {status}\n{header_lines}\n\n{response_body}"


class FakeRunner:
    """Process fake whose handlers receive the exact argv."""

    def __init__(
        self, handler: Callable[[list[str]], subprocess.CompletedProcess[str]]
    ) -> None:
        self.handler = handler
        self.calls: list[list[str]] = []

    def __call__(
        self, command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        return self.handler(command)


def _completed(
    command: list[str], *, status: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, status, stdout=stdout, stderr=stderr)


def test_local_index_is_one_process_and_still_requires_remote_confirmation() -> None:
    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == "git":
            return _completed(command, stdout=f"{FULL_SHA}\n{OTHER_SHA}\n")
        return _completed(command, stdout=_http(200))

    runner = FakeRunner(handler)
    resolver = CommitShaResolver(runner=runner)

    for _ in range(10_000):
        assert (
            resolver.resolve(FULL_SHA, ("OmniNode-ai/onex_change_control",)).outcome
            is EnumCommitShaOutcome.REACHABLE_REMOTE
        )

    assert runner.calls[0] == ["git", "rev-list", "--remotes=origin"]
    assert len(runner.calls) == 2
    assert resolver.remote_calls == 1


def test_stale_local_origin_ref_cannot_override_remote_missing() -> None:
    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == "git":
            return _completed(command, stdout=f"{FULL_SHA}\n")
        return _completed(command, status=1, stdout=_http(404))

    runner = FakeRunner(handler)
    resolution = CommitShaResolver(runner=runner).resolve(
        FULL_SHA, ("OmniNode-ai/onex_change_control",)
    )
    assert resolution.outcome is EnumCommitShaOutcome.MISSING
    assert len(runner.calls) == 2


def test_unclassified_422_is_not_a_definitive_missing_result() -> None:
    """A 422 that is NOT GitHub's "no commit found" answer still halts.

    OMN-17502 widened MISSING to cover the one 422 the commits endpoint uses to
    say "this SHA is not in this repository". Every other 422 is a real
    validation error and must keep the session fail-closed.
    """
    runner = FakeRunner(
        lambda command: _completed(command, status=1, stdout=_http(422))
    )
    resolver = CommitShaResolver(runner=runner)

    first = resolver.remote_resolution("OmniNode-ai/onex_change_control", FULL_SHA)
    second = resolver.remote_resolution("OmniNode-ai/omnimarket", OTHER_SHA)

    assert first.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert first.status_code == 422
    assert second.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert len(runner.calls) == 1


def test_github_sha_absent_422_is_missing_and_lets_the_next_repo_answer() -> None:
    """OMN-17502: a product SHA absent from OCC must not halt the session.

    GitHub answers 422 — not 404 — when a well-formed 40-hex SHA is absent from
    the repository. Before this, that made "not in THIS repo" indistinguishable
    from an outage, and because UNAVAILABLE is terminal for the session, the
    trusted product-repo hint could never be consulted: every autobind receipt
    carrying a product repository's head SHA failed closed on the first probe
    (observed live on OCC#8018, 2026-09-02).
    """

    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] != "gh":
            return _completed(command, stdout="")
        if "repos/OmniNode-ai/onex_change_control/commits/" in command[-1]:
            return _completed(
                command,
                status=1,
                stdout=_http(
                    422,
                    body=json.dumps(
                        {"message": f"No commit found for SHA: {FULL_SHA}"}
                    ),
                ),
            )
        return _completed(command, stdout=_http(200))

    runner = FakeRunner(handler)
    resolution = CommitShaResolver(runner=runner).resolve(
        FULL_SHA,
        ("OmniNode-ai/onex_change_control", "OmniNode-ai/omninode_infra"),
    )

    assert resolution.outcome is EnumCommitShaOutcome.REACHABLE_REMOTE
    assert resolution.repo == "OmniNode-ai/omninode_infra"
    gh_calls = [call for call in runner.calls if call[0] == "gh"]
    assert [call[-1] for call in gh_calls] == [
        f"repos/OmniNode-ai/onex_change_control/commits/{FULL_SHA}",
        f"repos/OmniNode-ai/omninode_infra/commits/{FULL_SHA}",
    ]


def test_sha_absent_422_without_a_hint_stays_missing_not_unavailable() -> None:
    """The single-repo case is a receipt defect, not an infrastructure outage."""
    runner = FakeRunner(
        lambda command: _completed(
            command,
            status=1,
            stdout=_http(
                422,
                body=json.dumps({"message": f"No commit found for SHA: {FULL_SHA}"}),
            ),
        )
    )
    resolver = CommitShaResolver(runner=runner)

    resolution = resolver.remote_resolution("OmniNode-ai/onex_change_control", FULL_SHA)

    assert resolution.outcome is EnumCommitShaOutcome.MISSING
    assert resolution.status_code == 422


def test_remote_request_keeps_json_body_and_has_no_silent_flag() -> None:
    runner = FakeRunner(lambda command: _completed(command, stdout=_http(200)))

    resolution = CommitShaResolver(runner=runner).remote_resolution(
        "OmniNode-ai/onex_change_control", FULL_SHA
    )

    assert resolution.outcome is EnumCommitShaOutcome.REACHABLE_REMOTE
    assert runner.calls == [
        [
            "gh",
            "api",
            "--include",
            f"repos/OmniNode-ai/onex_change_control/commits/{FULL_SHA}",
        ]
    ]


def test_uppercase_full_sha_normalizes_local_remote_and_cache_keys() -> None:
    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == "git":
            return _completed(command, stdout=f"{FULL_SHA}\n")
        return _completed(command, stdout=_http(200))

    runner = FakeRunner(handler)
    resolver = CommitShaResolver(runner=runner)
    first = resolver.resolve(UPPER_SHA, ("OmniNode-ai/onex_change_control",))
    second = resolver.resolve(FULL_SHA, ("OmniNode-ai/onex_change_control",))

    assert first.outcome is EnumCommitShaOutcome.REACHABLE_REMOTE
    assert second.outcome is EnumCommitShaOutcome.REACHABLE_REMOTE
    assert first.sha == FULL_SHA
    assert second.sha == FULL_SHA
    assert runner.calls == [
        ["git", "rev-list", "--remotes=origin"],
        [
            "gh",
            "api",
            "--include",
            f"repos/OmniNode-ai/onex_change_control/commits/{FULL_SHA}",
        ],
    ]


def test_remote_result_is_cached_for_ten_thousand_claims() -> None:
    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == "git":
            return _completed(command)
        return _completed(command, stdout=_http(200))

    runner = FakeRunner(handler)
    resolver = CommitShaResolver(runner=runner)
    for _ in range(10_000):
        assert (
            resolver.resolve(FULL_SHA, ("OmniNode-ai/onex_change_control",)).outcome
            is EnumCommitShaOutcome.REACHABLE_REMOTE
        )

    assert len(runner.calls) == 2
    assert runner.calls[0] == ["git", "rev-list", "--remotes=origin"]
    assert runner.calls[1][-1].endswith(FULL_SHA)
    assert resolver.remote_calls == 1


def test_http_404_missing_is_cached() -> None:
    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == "git":
            return _completed(command)
        return _completed(command, status=1, stdout=_http(404))

    runner = FakeRunner(handler)
    resolver = CommitShaResolver(runner=runner)
    assert (
        resolver.remote_resolution("OmniNode-ai/onex_change_control", FULL_SHA).outcome
        is EnumCommitShaOutcome.MISSING
    )
    assert (
        resolver.remote_resolution("OmniNode-ai/onex_change_control", FULL_SHA).outcome
        is EnumCommitShaOutcome.MISSING
    )
    assert len(runner.calls) == 1


@pytest.mark.parametrize("status", [403, 429, 500])
def test_remote_unavailable_halts_later_calls(status: int) -> None:
    runner = FakeRunner(
        lambda command: _completed(command, status=1, stdout=_http(status))
    )
    resolver = CommitShaResolver(runner=runner)
    first = resolver.remote_resolution("OmniNode-ai/onex_change_control", FULL_SHA)
    second = resolver.remote_resolution("OmniNode-ai/omnimarket", OTHER_SHA)

    assert first.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert second.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert len(runner.calls) == 1
    assert first.status_code == status


def test_network_timeout_and_budget_are_unavailable_without_retry() -> None:
    def timeout_runner(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 1)

    runner = FakeRunner(timeout_runner)
    resolver = CommitShaResolver(runner=runner)
    assert (
        resolver.remote_resolution("OmniNode-ai/onex_change_control", FULL_SHA).outcome
        is EnumCommitShaOutcome.UNAVAILABLE
    )
    assert (
        resolver.remote_resolution("OmniNode-ai/onex_change_control", OTHER_SHA).outcome
        is EnumCommitShaOutcome.UNAVAILABLE
    )
    assert len(runner.calls) == 1

    def network_runner(
        _command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        message = "network unavailable"
        raise OSError(message)

    network = FakeRunner(network_runner)
    assert (
        CommitShaResolver(runner=network)
        .remote_resolution("OmniNode-ai/onex_change_control", FULL_SHA)
        .outcome
        is EnumCommitShaOutcome.UNAVAILABLE
    )
    assert len(network.calls) == 1

    budget_runner = FakeRunner(lambda command: _completed(command, stdout=_http(200)))
    budgeted = CommitShaResolver(rest_budget=0, runner=budget_runner)
    assert (
        budgeted.remote_resolution("OmniNode-ai/onex_change_control", FULL_SHA).outcome
        is EnumCommitShaOutcome.UNAVAILABLE
    )
    assert budget_runner.calls == []


def test_response_headers_and_process_errors_are_fail_closed() -> None:
    rate_limited = FakeRunner(
        lambda command: _completed(
            command,
            status=1,
            stdout=_http(
                429,
                {"X-RateLimit-Reset": "1770000000", "Retry-After": "60"},
            ),
        )
    )
    resolution = CommitShaResolver(runner=rate_limited).remote_resolution(
        "OmniNode-ai/onex_change_control", FULL_SHA
    )
    assert resolution.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert resolution.reset_at == "1770000000"
    assert resolution.retry_after == "60"

    process_error = FakeRunner(
        lambda command: _completed(command, status=1, stdout=_http(200))
    )
    assert (
        CommitShaResolver(runner=process_error)
        .remote_resolution("OmniNode-ai/onex_change_control", FULL_SHA)
        .outcome
        is EnumCommitShaOutcome.UNAVAILABLE
    )


@pytest.mark.parametrize(
    "body",
    [
        "",
        "not-json",
        "[]",
        '{"message": "login required"}',
        json.dumps({"sha": OTHER_SHA}),
        json.dumps({"sha": FULL_SHA, "message": "Bad credentials"}),
    ],
)
def test_http_200_requires_matching_full_sha_json_object(body: str) -> None:
    runner = FakeRunner(
        lambda command: _completed(command, stdout=_http(200, body=body))
    )
    resolution = CommitShaResolver(runner=runner).remote_resolution(
        "OmniNode-ai/onex_change_control", FULL_SHA
    )
    assert resolution.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert resolution.status_code == 200


def test_fresh_session_retries_after_prior_unavailable() -> None:
    unavailable = FakeRunner(
        lambda command: _completed(command, status=1, stdout=_http(429))
    )
    assert (
        CommitShaResolver(runner=unavailable)
        .remote_resolution("OmniNode-ai/onex_change_control", FULL_SHA)
        .outcome
        is EnumCommitShaOutcome.UNAVAILABLE
    )

    reachable = FakeRunner(lambda command: _completed(command, stdout=_http(200)))
    assert (
        CommitShaResolver(runner=reachable)
        .remote_resolution("OmniNode-ai/onex_change_control", FULL_SHA)
        .outcome
        is EnumCommitShaOutcome.REACHABLE_REMOTE
    )
    assert len(reachable.calls) == 1


def test_invalid_sha_makes_zero_process_calls() -> None:
    runner = FakeRunner(_completed)
    resolver = CommitShaResolver(runner=runner)
    assert (
        resolver.resolve("abc123", ("OmniNode-ai/onex_change_control",)).outcome
        is EnumCommitShaOutcome.INVALID
    )
    assert runner.calls == []


def test_remote_cache_isolated_by_repository() -> None:
    runner = FakeRunner(lambda command: _completed(command, stdout=_http(200)))
    resolver = CommitShaResolver(runner=runner)
    assert (
        resolver.remote_resolution("OmniNode-ai/onex_change_control", FULL_SHA).outcome
        is EnumCommitShaOutcome.REACHABLE_REMOTE
    )
    assert (
        resolver.remote_resolution("OmniNode-ai/omnimarket", FULL_SHA).outcome
        is EnumCommitShaOutcome.REACHABLE_REMOTE
    )
    assert len(runner.calls) == 2


# ---------------------------------------------------------------------------
# OMN-16360: UNAVAILABLE is one outcome with several opposite remedies.
#
# These controls pin the CATEGORY, not the verdict. Every case below stays
# UNAVAILABLE, which is asserted in each test so a future change that
# loosens fail-closed to buy a nicer message turns them red.
# ---------------------------------------------------------------------------

# The exact response shape measured on onex_change_control job 106062659651
# (2026-09-20T10:25:23Z): HTTP 403, an hourly reset four minutes out, a spent
# quota, and no retry-after. The gate rendered it as a bare "HTTP 403", which
# reads as a permission fault; the quota reset at 10:30:00Z and the same gate
# passed at 10:32:06Z.
_OCCURRENCE_HEADERS = {
    "x-ratelimit-remaining": "0",
    "x-ratelimit-reset": "1789900200",
}
_OCCURRENCE_BODY = json.dumps(
    {
        "message": (
            "API rate limit exceeded for installation ID 12345678. "
            "If you reach out to GitHub Support for help, please include the "
            "request ID."
        ),
        "documentation_url": "https://docs.github.com/rest/overview/rate-limits",
    }
)


def _resolve_once(
    status: int,
    headers: dict[str, str] | None = None,
    body: str | None = None,
) -> object:
    runner = FakeRunner(
        lambda command: _completed(
            command, status=1, stdout=_http(status, headers, body)
        )
    )
    return CommitShaResolver(runner=runner).remote_resolution(
        "OmniNode-ai/omnibase_infra", FULL_SHA
    )


def test_spent_quota_403_is_named_a_primary_rate_limit_not_a_bare_status() -> None:
    resolution = _resolve_once(403, _OCCURRENCE_HEADERS, _OCCURRENCE_BODY)

    assert resolution.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert resolution.category is EnumCommitShaUnavailableCategory.RATE_LIMIT_PRIMARY
    assert resolution.rate_limit_remaining == "0"
    assert resolution.reset_at == "1789900200"
    assert resolution.retry_after is None
    assert resolution.api_message is not None
    assert "rate limit exceeded" in resolution.api_message


def test_out_of_scope_403_is_named_a_permission_refusal() -> None:
    """The same status and a healthy quota is the opposite remedy.

    A permission refusal never clears by waiting, so it must not render
    identically to the quota case above.
    """

    resolution = _resolve_once(
        403,
        {"x-ratelimit-remaining": "4998", "x-ratelimit-reset": "1789900200"},
        json.dumps({"message": "Resource not accessible by integration"}),
    )

    assert resolution.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert resolution.category is EnumCommitShaUnavailableCategory.PERMISSION
    assert resolution.rate_limit_remaining == "4998"


def test_secondary_limit_is_distinguished_and_keeps_retry_after() -> None:
    resolution = _resolve_once(
        403,
        {"retry-after": "60", "x-ratelimit-remaining": "4998"},
        json.dumps({"message": "You have exceeded a secondary rate limit"}),
    )

    assert resolution.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert resolution.category is EnumCommitShaUnavailableCategory.RATE_LIMIT_SECONDARY
    assert resolution.retry_after == "60"


def test_401_is_authentication_and_5xx_is_upstream() -> None:
    unauthorized = _resolve_once(401, None, json.dumps({"message": "Bad credentials"}))
    upstream = _resolve_once(502, None, json.dumps({"message": "Server Error"}))

    assert unauthorized.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert unauthorized.category is EnumCommitShaUnavailableCategory.AUTHENTICATION
    assert upstream.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert upstream.category is EnumCommitShaUnavailableCategory.UPSTREAM_ERROR


def test_unrecognised_403_shape_is_not_guessed_into_a_category() -> None:
    """A confidently wrong category is worse than an unnamed one."""

    resolution = _resolve_once(403, {"x-ratelimit-remaining": "4998"}, "not json")

    assert resolution.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert resolution.category is EnumCommitShaUnavailableCategory.UNEXPECTED_STATUS
    assert resolution.api_message is None


def test_replayed_halt_says_it_never_probed_and_names_the_failing_probe() -> None:
    """The second claim in a halted session is not a measurement of itself.

    Before OMN-16360 the replay carried the FIRST probe's detail while naming
    the second claim's own repo and SHA, with the status stripped — a
    diagnostic asserting a probe that never ran. The 2026-09-20 occurrence
    emitted exactly that for a SHA in a different repository.
    """

    runner = FakeRunner(
        lambda command: _completed(
            command,
            status=1,
            stdout=_http(403, _OCCURRENCE_HEADERS, _OCCURRENCE_BODY),
        )
    )
    resolver = CommitShaResolver(runner=runner)
    probed = resolver.remote_resolution("OmniNode-ai/omnibase_infra", FULL_SHA)
    replayed = resolver.remote_resolution("OmniNode-ai/onex_change_control", OTHER_SHA)

    assert len(runner.calls) == 1, "the second claim must not spend a call"
    assert probed.category is EnumCommitShaUnavailableCategory.RATE_LIMIT_PRIMARY
    assert probed.halted_by_repo is None, "a measured probe is not a replay"

    assert replayed.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert replayed.category is EnumCommitShaUnavailableCategory.SESSION_HALTED
    assert replayed.halted_by_repo == "OmniNode-ai/omnibase_infra"
    assert replayed.halted_by_sha == FULL_SHA
    assert replayed.status_code is None, (
        "a replay must not present the halting probe's status as its own"
    )


def test_exhausted_budget_is_named_and_spends_no_process() -> None:
    runner = FakeRunner(lambda command: _completed(command, stdout=_http(200)))
    resolver = CommitShaResolver(rest_budget=0, runner=runner)
    resolution = resolver.remote_resolution("OmniNode-ai/omnibase_infra", FULL_SHA)

    assert resolution.outcome is EnumCommitShaOutcome.UNAVAILABLE
    assert resolution.category is EnumCommitShaUnavailableCategory.BUDGET_EXHAUSTED
    assert runner.calls == []


def test_positive_control_a_resolvable_sha_has_no_unavailable_category() -> None:
    """The control for every assertion above.

    Each test in this block asserts a category on a failing probe. If the
    resolver stopped classifying, or stopped probing at all, those could pass
    vacuously only if a clean probe ALSO produced a category. This is the
    input known to return rows: a healthy 200 resolves, and carries no
    category and no halt attribution.
    """

    runner = FakeRunner(
        lambda command: _completed(
            command, stdout=_http(200, {"x-ratelimit-remaining": "4999"})
        )
    )
    resolution = CommitShaResolver(runner=runner).remote_resolution(
        "OmniNode-ai/omnibase_infra", FULL_SHA
    )

    assert resolution.outcome is EnumCommitShaOutcome.REACHABLE_REMOTE
    assert resolution.category is None
    assert resolution.halted_by_repo is None
    assert resolution.rate_limit_remaining == "4999"
    assert len(runner.calls) == 1
