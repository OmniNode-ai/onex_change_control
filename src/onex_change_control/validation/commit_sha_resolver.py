# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Bounded, fail-closed resolution of receipt commit SHA claims.

The receipt hardening gate must distinguish a SHA that is absent from a
repository from a resolver that cannot establish the answer.  This module is
deliberately session-scoped: one :class:`CommitShaResolver` owns one local
remote-tracking index, one REST budget, and caches for a single invocation.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


class EnumCommitShaOutcome(str, Enum):
    """Outcomes for a commit SHA claim; local reachability is only a hint."""

    REACHABLE_LOCAL = "REACHABLE_LOCAL"
    REACHABLE_REMOTE = "REACHABLE_REMOTE"
    MISSING = "MISSING"
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"


class EnumCommitShaUnavailableCategory(str, Enum):
    """Why resolution could not be established (OMN-16360).

    ``UNAVAILABLE`` is one outcome with several operationally opposite causes,
    and the remedies do not overlap: a primary rate limit is waited out, a
    permission refusal is an installation-scope change, and an upstream 5xx is
    neither. Collapsing them into one "GitHub API returned HTTP <n>" sentence
    sent the 2026-09-20 occurrence down a token/permission investigation for a
    limit that cleared on its own at the next hourly reset.

    Every member is still fail-closed. This enum names a cause; it never
    decides whether the gate passes.
    """

    #: Hourly REST quota for the calling identity is spent; wait for reset_at.
    RATE_LIMIT_PRIMARY = "RATE_LIMIT_PRIMARY"
    #: Abuse/concurrency limit; honour retry_after before retrying.
    RATE_LIMIT_SECONDARY = "RATE_LIMIT_SECONDARY"
    #: Credential is valid but is not scoped to this repository.
    PERMISSION = "PERMISSION"
    #: Credential is absent, expired or rejected.
    AUTHENTICATION = "AUTHENTICATION"
    #: GitHub answered 5xx.
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    #: A status this resolver does not classify; still halts.
    UNEXPECTED_STATUS = "UNEXPECTED_STATUS"
    #: The response could not be read as an HTTP exchange or as the object.
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    #: The subprocess timed out, could not start, or exited inconsistently.
    TRANSPORT = "TRANSPORT"
    #: This session's bounded REST budget is spent.
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    #: Never probed: an earlier probe halted the session (see halted_by_*).
    SESSION_HALTED = "SESSION_HALTED"
    #: `git rev-list` could not build the local remote-tracking index.
    LOCAL_INDEX = "LOCAL_INDEX"


@dataclass(frozen=True, slots=True)
class CommitShaResolution:
    """A resolution outcome, including bounded diagnostic metadata."""

    outcome: EnumCommitShaOutcome
    sha: str
    repo: str | None = None
    status_code: int | None = None
    reset_at: str | None = None
    retry_after: str | None = None
    detail: str | None = None
    attempted_remote: bool = False
    category: EnumCommitShaUnavailableCategory | None = None
    rate_limit_remaining: str | None = None
    #: GitHub's own ``message`` field, bounded. It is the only field that
    #: separates a spent quota from an out-of-scope installation on a 403.
    api_message: str | None = None
    #: The probe that actually halted the session, when this resolution was
    #: replayed rather than measured. Without it the replay reads as a
    #: measurement of THIS repo and sha, which is a fabricated diagnostic.
    halted_by_repo: str | None = None
    halted_by_sha: str | None = None


_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_HTTP_STATUS_RE = re.compile(r"^HTTP/\S+\s+(\d{3})\b")
_HTTP_OK = 200
_MISSING_HTTP_STATUS = 404

# OMN-17502: GitHub's ``GET /repos/{owner}/{repo}/commits/{ref}`` does NOT
# answer 404 when a well-formed 40-hex SHA is simply absent from the
# repository. It answers **422** with the body
# ``{"message": "No commit found for SHA: <sha>"}`` — 404 is reserved for a
# repository that is missing or invisible to the token. Treating 422 as an
# unclassified error made "this SHA is not in THIS repo" indistinguishable from
# an outage, and because an unavailable outcome is terminal for the session,
# the OCC-first / product-repo-hint fallback the caller documents could never
# be reached: every autobind receipt carrying a PRODUCT repository's head SHA
# failed closed on the first probe. Observed live 2026-09-02 on OCC#8018,
# whose omninode_infra head SHA returns exactly that 422 against OCC.
#
# The widening is deliberately narrow. Only 422 responses whose body message
# is GitHub's own "no commit found" sentence are read as MISSING; any other
# 422 (a real validation error) still halts the session fail-closed.
_SHA_ABSENT_HTTP_STATUS = 422
_SHA_ABSENT_MESSAGE_RE = re.compile(r"^No commit found for SHA\b", re.IGNORECASE)
_INVALID_BUDGET_MESSAGE = "rest_budget must be non-negative"
_INVALID_TIMEOUT_MESSAGE = "timeout_seconds must be positive"

_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_FLOOR = 500

# OMN-16360: GitHub's own sentences are the discriminator on a 403. A spent
# quota and an out-of-scope installation are the same status code with the
# same headers apart from x-ratelimit-remaining, and the remedies are
# opposite, so the body is read rather than guessed at.
_SECONDARY_LIMIT_MESSAGE_RE = re.compile(r"secondary rate limit", re.IGNORECASE)
_PRIMARY_LIMIT_MESSAGE_RE = re.compile(r"\brate limit exceeded\b", re.IGNORECASE)
_PERMISSION_MESSAGE_RE = re.compile(
    r"not accessible by integration|resource not accessible|must have|"
    r"forbidden|permission",
    re.IGNORECASE,
)
# A diagnostic is read by a human in a CI log; an unbounded API body is not.
_MAX_API_MESSAGE_CHARS = 200


def _api_message(body: str) -> str | None:
    """Return GitHub's own ``message`` field from a response body, bounded."""

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    collapsed = " ".join(message.split())
    if len(collapsed) > _MAX_API_MESSAGE_CHARS:
        return collapsed[:_MAX_API_MESSAGE_CHARS] + "…"
    return collapsed


def _classify_unavailable_status(
    status_code: int,
    *,
    retry_after: str | None,
    rate_limit_remaining: str | None,
    api_message: str | None,
) -> EnumCommitShaUnavailableCategory:
    """Name the cause of a non-resolving HTTP status from observed signals.

    Classification is derived from what the response actually carried — the
    status, ``retry-after``, ``x-ratelimit-remaining`` and GitHub's own
    ``message`` — never from which repository was asked for. An unrecognised
    shape falls to ``UNEXPECTED_STATUS`` rather than to a plausible guess,
    because a confidently wrong category is worse than an unnamed one.
    """

    if status_code == _HTTP_UNAUTHORIZED:
        return EnumCommitShaUnavailableCategory.AUTHENTICATION
    if status_code >= _HTTP_SERVER_ERROR_FLOOR:
        return EnumCommitShaUnavailableCategory.UPSTREAM_ERROR
    if status_code not in (_HTTP_FORBIDDEN, _HTTP_TOO_MANY_REQUESTS):
        return EnumCommitShaUnavailableCategory.UNEXPECTED_STATUS

    message = api_message or ""
    # Ordered most-specific first. The secondary-limit signals come before
    # the primary ones because a secondary limit can be served with a spent
    # quota, and retry_after is the field that decides how to wait.
    rules: tuple[tuple[bool, EnumCommitShaUnavailableCategory], ...] = (
        (
            bool(_SECONDARY_LIMIT_MESSAGE_RE.search(message)),
            EnumCommitShaUnavailableCategory.RATE_LIMIT_SECONDARY,
        ),
        (
            retry_after is not None,
            EnumCommitShaUnavailableCategory.RATE_LIMIT_SECONDARY,
        ),
        (
            rate_limit_remaining == "0"
            or bool(_PRIMARY_LIMIT_MESSAGE_RE.search(message)),
            EnumCommitShaUnavailableCategory.RATE_LIMIT_PRIMARY,
        ),
        (
            # 429 is a limit by definition even when the body is unhelpful.
            status_code == _HTTP_TOO_MANY_REQUESTS,
            EnumCommitShaUnavailableCategory.RATE_LIMIT_PRIMARY,
        ),
        (
            bool(_PERMISSION_MESSAGE_RE.search(message)),
            EnumCommitShaUnavailableCategory.PERMISSION,
        ),
    )
    for matched, category in rules:
        if matched:
            return category
    return EnumCommitShaUnavailableCategory.UNEXPECTED_STATUS


def _is_sha_absent_body(body: str) -> bool:
    """Return whether a 422 body is GitHub's "commit is not in this repo" answer.

    Only the API's own sentence counts. A 422 whose body is unparseable, has no
    ``message``, or carries a different message is a real validation error and
    must keep halting the session fail-closed (OMN-17502).
    """

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    message = payload.get("message")
    return (
        isinstance(message, str) and _SHA_ABSENT_MESSAGE_RE.match(message) is not None
    )


def is_full_commit_sha(value: str) -> bool:
    """Return whether ``value`` is exactly one full hexadecimal Git SHA-1."""

    return _FULL_SHA_RE.fullmatch(value) is not None


def _normalize_full_commit_sha(value: str) -> str:
    """Return the canonical lowercase spelling of a previously validated SHA."""

    return value.lower()


class CommitShaResolver:
    """Resolve commit claims without unbounded process or network work.

    ``runner`` is injectable so unit tests exercise every outcome without
    consulting GitHub.  It is intentionally one callable for both Git and
    GitHub CLI commands, which makes call-count assertions precise.
    """

    def __init__(
        self,
        *,
        rest_budget: int = 64,
        timeout_seconds: float = 15.0,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if rest_budget < 0:
            raise ValueError(_INVALID_BUDGET_MESSAGE)
        if timeout_seconds <= 0:
            raise ValueError(_INVALID_TIMEOUT_MESSAGE)
        self._rest_budget = rest_budget
        self._timeout_seconds = timeout_seconds
        self._runner = runner
        self._local_index: frozenset[str] | None = None
        self._local_index_error: str | None = None
        self._local_by_sha: dict[str, CommitShaResolution] = {}
        self._remote_by_repo_sha: dict[tuple[str, str], CommitShaResolution] = {}
        self._remote_calls = 0
        self._remote_halted_reason: str | None = None
        self._remote_halted_category: EnumCommitShaUnavailableCategory | None = None
        self._remote_halted_probe: tuple[str, str] | None = None

    @property
    def remote_calls(self) -> int:
        """Number of GitHub API processes launched in this session."""

        return self._remote_calls

    @property
    def rest_budget(self) -> int:
        """Maximum GitHub API processes allowed in this session."""

        return self._rest_budget

    def local_resolution(self, sha: str) -> CommitShaResolution:
        """Index a SHA against local ``origin`` refs as a non-authoritative hint.

        A ref can be stale without a fetch, so this result never proves remote
        reachability. :meth:`resolve` always obtains the bounded REST result
        before returning an authoritative reachable outcome.
        """

        if not is_full_commit_sha(sha):
            return CommitShaResolution(EnumCommitShaOutcome.INVALID, sha)
        sha = _normalize_full_commit_sha(sha)
        cached = self._local_by_sha.get(sha)
        if cached is not None:
            return cached

        self._ensure_local_index()
        if self._local_index_error is not None:
            resolution = CommitShaResolution(
                EnumCommitShaOutcome.UNAVAILABLE,
                sha,
                detail=(
                    "local remote-tracking index unavailable: "
                    f"{self._local_index_error}"
                ),
                category=EnumCommitShaUnavailableCategory.LOCAL_INDEX,
            )
        elif sha in self._local_index_or_empty():
            resolution = CommitShaResolution(EnumCommitShaOutcome.REACHABLE_LOCAL, sha)
        else:
            resolution = CommitShaResolution(EnumCommitShaOutcome.MISSING, sha)
        self._local_by_sha[sha] = resolution
        return resolution

    def resolve(self, sha: str, repos: Iterable[str]) -> CommitShaResolution:
        """Resolve a SHA against unique repositories in authoritative order.

        The local origin index is built once only to retain an inventory hint;
        it does not avoid REST confirmation because stale tracking refs are
        not evidence of current remote reachability. A definitive ``MISSING``
        can continue to a later, trusted repository hint. Any remote
        ``UNAVAILABLE`` is terminal for this session: later remote claims
        receive the cached fail-closed condition without another process or
        retry.
        """

        local = self.local_resolution(sha)
        if local.outcome is EnumCommitShaOutcome.INVALID:
            return local

        seen: set[str] = set()
        attempted = False
        for repo in repos:
            if not repo or repo in seen:
                continue
            seen.add(repo)
            remote = self.remote_resolution(repo, local.sha)
            attempted = attempted or remote.attempted_remote
            if remote.outcome is EnumCommitShaOutcome.REACHABLE_REMOTE:
                return remote
            if remote.outcome is EnumCommitShaOutcome.UNAVAILABLE:
                return remote

        if local.outcome is EnumCommitShaOutcome.UNAVAILABLE and not attempted:
            return local
        return CommitShaResolution(EnumCommitShaOutcome.MISSING, local.sha)

    def remote_resolution(  # noqa: C901, PLR0911, PLR0912
        self, repo: str, sha: str
    ) -> CommitShaResolution:
        """Resolve a valid SHA via GitHub's commits endpoint within budget."""

        if not is_full_commit_sha(sha):
            return CommitShaResolution(EnumCommitShaOutcome.INVALID, sha, repo=repo)
        sha = _normalize_full_commit_sha(sha)
        cache_key = (repo, sha)
        cached = self._remote_by_repo_sha.get(cache_key)
        if cached is not None:
            return cached
        if self._remote_halted_reason is not None:
            # OMN-16360: this claim was never probed. Saying so, and naming
            # the probe that did halt the session, is the difference between
            # a diagnostic and a fabricated measurement of this repo and sha.
            halted_repo, halted_sha = self._remote_halted_probe or (None, None)
            return CommitShaResolution(
                EnumCommitShaOutcome.UNAVAILABLE,
                sha,
                repo=repo,
                detail=self._remote_halted_reason,
                category=EnumCommitShaUnavailableCategory.SESSION_HALTED,
                halted_by_repo=halted_repo,
                halted_by_sha=halted_sha,
            )
        if self._remote_calls >= self._rest_budget:
            return self._halt_remote(
                repo,
                sha,
                f"remote REST budget exhausted ({self._rest_budget} calls)",
                category=EnumCommitShaUnavailableCategory.BUDGET_EXHAUSTED,
                attempted_remote=False,
            )

        self._remote_calls += 1
        try:
            completed = self._runner(
                [
                    "gh",
                    "api",
                    "--include",
                    f"repos/{repo}/commits/{sha}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return self._halt_remote(
                repo,
                sha,
                "GitHub API request timed out",
                category=EnumCommitShaUnavailableCategory.TRANSPORT,
            )
        except OSError as exc:
            return self._halt_remote(
                repo,
                sha,
                f"GitHub API process unavailable: {exc}",
                category=EnumCommitShaUnavailableCategory.TRANSPORT,
            )

        status_code, headers, body = _parse_http_response(completed.stdout)
        if status_code is None:
            return self._halt_remote(
                repo,
                sha,
                "GitHub API response did not contain a parseable HTTP status",
                category=EnumCommitShaUnavailableCategory.MALFORMED_RESPONSE,
            )
        if completed.returncode != 0 and status_code == _HTTP_OK:
            return self._halt_remote(
                repo,
                sha,
                f"GitHub API process exited {completed.returncode} despite HTTP 200",
                status_code=status_code,
                category=EnumCommitShaUnavailableCategory.TRANSPORT,
            )
        reset_at = headers.get("x-ratelimit-reset")
        retry_after = headers.get("retry-after")
        rate_limit_remaining = headers.get("x-ratelimit-remaining")
        if status_code == _HTTP_OK:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                return self._halt_remote(
                    repo,
                    sha,
                    "GitHub API HTTP 200 response body was not valid JSON",
                    status_code=status_code,
                    reset_at=reset_at,
                    retry_after=retry_after,
                    rate_limit_remaining=rate_limit_remaining,
                    category=EnumCommitShaUnavailableCategory.MALFORMED_RESPONSE,
                )
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("sha"), str)
                or not is_full_commit_sha(payload["sha"])
                or _normalize_full_commit_sha(payload["sha"]) != sha
                or "message" in payload
            ):
                return self._halt_remote(
                    repo,
                    sha,
                    "GitHub API HTTP 200 response was not an unambiguous "
                    "requested full commit SHA object",
                    status_code=status_code,
                    reset_at=reset_at,
                    retry_after=retry_after,
                    rate_limit_remaining=rate_limit_remaining,
                    category=EnumCommitShaUnavailableCategory.MALFORMED_RESPONSE,
                )
            resolution = CommitShaResolution(
                EnumCommitShaOutcome.REACHABLE_REMOTE,
                sha,
                repo=repo,
                status_code=status_code,
                reset_at=reset_at,
                retry_after=retry_after,
                rate_limit_remaining=rate_limit_remaining,
                attempted_remote=True,
            )
        elif status_code == _MISSING_HTTP_STATUS or (
            status_code == _SHA_ABSENT_HTTP_STATUS and _is_sha_absent_body(body)
        ):
            resolution = CommitShaResolution(
                EnumCommitShaOutcome.MISSING,
                sha,
                repo=repo,
                status_code=status_code,
                reset_at=reset_at,
                retry_after=retry_after,
                rate_limit_remaining=rate_limit_remaining,
                attempted_remote=True,
            )
        else:
            api_message = _api_message(body)
            return self._halt_remote(
                repo,
                sha,
                f"GitHub API returned HTTP {status_code}",
                status_code=status_code,
                reset_at=reset_at,
                retry_after=retry_after,
                rate_limit_remaining=rate_limit_remaining,
                api_message=api_message,
                category=_classify_unavailable_status(
                    status_code,
                    retry_after=retry_after,
                    rate_limit_remaining=rate_limit_remaining,
                    api_message=api_message,
                ),
            )

        self._remote_by_repo_sha[cache_key] = resolution
        return resolution

    def _ensure_local_index(self) -> None:
        if self._local_index is not None or self._local_index_error is not None:
            return
        try:
            completed = self._runner(
                ["git", "rev-list", "--remotes=origin"],
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            self._local_index_error = "git rev-list timed out"
            return
        except OSError as exc:
            self._local_index_error = f"git rev-list could not start: {exc}"
            return
        if completed.returncode != 0:
            self._local_index_error = (
                f"git rev-list exited {completed.returncode}: "
                f"{completed.stderr.strip()}"
            )
            return
        self._local_index = frozenset(
            line.strip().lower()
            for line in completed.stdout.splitlines()
            if line.strip()
        )

    def _local_index_or_empty(self) -> frozenset[str]:
        return self._local_index if self._local_index is not None else frozenset()

    def _halt_remote(  # noqa: PLR0913
        self,
        repo: str,
        sha: str,
        detail: str,
        *,
        status_code: int | None = None,
        reset_at: str | None = None,
        retry_after: str | None = None,
        category: EnumCommitShaUnavailableCategory,
        rate_limit_remaining: str | None = None,
        api_message: str | None = None,
        attempted_remote: bool = True,
    ) -> CommitShaResolution:
        self._remote_halted_reason = detail
        self._remote_halted_category = category
        self._remote_halted_probe = (repo, sha)
        resolution = CommitShaResolution(
            EnumCommitShaOutcome.UNAVAILABLE,
            sha,
            repo=repo,
            status_code=status_code,
            reset_at=reset_at,
            retry_after=retry_after,
            detail=detail,
            attempted_remote=attempted_remote,
            category=category,
            rate_limit_remaining=rate_limit_remaining,
            api_message=api_message,
        )
        self._remote_by_repo_sha[(repo, sha)] = resolution
        return resolution


def _parse_http_response(stdout: str) -> tuple[int | None, dict[str, str], str]:
    """Extract the final HTTP response status, headers, and body."""

    blocks = re.split(r"\r?\n\r?\n", stdout)
    for index in range(len(blocks) - 1, -1, -1):
        block = blocks[index]
        lines = block.splitlines()
        if not lines:
            continue
        match = _HTTP_STATUS_RE.match(lines[0].strip())
        if match is None:
            continue
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        body = blocks[index + 1] if index + 1 < len(blocks) else ""
        return int(match.group(1)), headers, body
    return None, {}, ""
