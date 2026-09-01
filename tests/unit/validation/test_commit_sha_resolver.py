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


def test_only_http_404_is_a_definitive_missing_result() -> None:
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
