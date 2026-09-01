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


_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_HTTP_STATUS_RE = re.compile(r"^HTTP/\S+\s+(\d{3})\b")
_HTTP_OK = 200
_MISSING_HTTP_STATUS = 404
_INVALID_BUDGET_MESSAGE = "rest_budget must be non-negative"
_INVALID_TIMEOUT_MESSAGE = "timeout_seconds must be positive"


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
            return CommitShaResolution(
                EnumCommitShaOutcome.UNAVAILABLE,
                sha,
                repo=repo,
                detail=self._remote_halted_reason,
            )
        if self._remote_calls >= self._rest_budget:
            self._remote_halted_reason = (
                f"remote REST budget exhausted ({self._rest_budget} calls)"
            )
            return CommitShaResolution(
                EnumCommitShaOutcome.UNAVAILABLE,
                sha,
                repo=repo,
                detail=self._remote_halted_reason,
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
            return self._halt_remote(repo, sha, "GitHub API request timed out")
        except OSError as exc:
            return self._halt_remote(
                repo, sha, f"GitHub API process unavailable: {exc}"
            )

        status_code, headers, body = _parse_http_response(completed.stdout)
        if status_code is None:
            return self._halt_remote(
                repo,
                sha,
                "GitHub API response did not contain a parseable HTTP status",
            )
        if completed.returncode != 0 and status_code == _HTTP_OK:
            return self._halt_remote(
                repo,
                sha,
                f"GitHub API process exited {completed.returncode} despite HTTP 200",
                status_code=status_code,
            )
        reset_at = headers.get("x-ratelimit-reset")
        retry_after = headers.get("retry-after")
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
                )
            resolution = CommitShaResolution(
                EnumCommitShaOutcome.REACHABLE_REMOTE,
                sha,
                repo=repo,
                status_code=status_code,
                reset_at=reset_at,
                retry_after=retry_after,
                attempted_remote=True,
            )
        elif status_code == _MISSING_HTTP_STATUS:
            resolution = CommitShaResolution(
                EnumCommitShaOutcome.MISSING,
                sha,
                repo=repo,
                status_code=status_code,
                reset_at=reset_at,
                retry_after=retry_after,
                attempted_remote=True,
            )
        else:
            return self._halt_remote(
                repo,
                sha,
                f"GitHub API returned HTTP {status_code}",
                status_code=status_code,
                reset_at=reset_at,
                retry_after=retry_after,
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
    ) -> CommitShaResolution:
        self._remote_halted_reason = detail
        resolution = CommitShaResolution(
            EnumCommitShaOutcome.UNAVAILABLE,
            sha,
            repo=repo,
            status_code=status_code,
            reset_at=reset_at,
            retry_after=retry_after,
            detail=detail,
            attempted_remote=True,
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
