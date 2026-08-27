# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tripwire for the platform-leads-review assumption behind OMN-14441 (OMN-14445).

`validate_prod_promotion_grants.py` rejects a grant whose `approved_by`
equals the PR author. That check is a *complete* defense against forged
self-approval only because `@OmniNode-ai/platform-leads` has exactly one
member today: an honest `approved_by` on any grant MUST name that person,
so a mismatch is unambiguous. The moment a second platform lead joins, the
guarantee silently degrades — a PR author could write the *other* lead's
login into `approved_by` without them ever reviewing anything, and nothing
would catch it, because `required_pull_request_reviews` (CODEOWNERS
enforcement) is intentionally NOT enabled on `dev` (enabling it would make a
grant PR opened by the sole lead permanently unapprovable — GitHub blocks
self-approval of your own PR; see OMN-14445 for the operator-scoped
decision this requires, which this script does not make).

This script converts that silent, time-bombed assumption into a loud,
self-monitoring one: it fails the moment `@platform-leads` grows past one
member while CODEOWNERS review is still unenforced, instead of letting the
safety property expire unnoticed.

Usage:
    uv run check-platform-leads-review-tripwire

Exit codes:
    0: safe — either CODEOWNERS review is independently enforced, or
       platform-leads has <= 1 member (an honest approved_by has only one
       possible value).
    1: TRIPPED — platform-leads has > 1 member and CODEOWNERS review is
       still unenforced; OMN-14441's self-approval check no longer fully
       covers forged approvals.
    2: INCONCLUSIVE — could not determine one or both facts because the
       token lacks scope to read team membership or branch protection, or
       because an unclassified GitHub API failure occurred. Treated as a
       failure: an unproven safety property does not pass.

Wedge-risk note (OMN-14445 review): unlike this repo's other cross-repo `gh`
usage (which clones PUBLIC repos and works even with no token at all), the
two API reads here are ORG-PRIVATE with no unauthenticated fallback — this
job has a hard dependency on a token with `read:org` scope
(`CROSS_REPO_PAT` in CI). If that PAT is ever absent, expired, or rotated
without the replacement carrying `read:org`, this job goes INCONCLUSIVE on
EVERY PR, not just the PR that changed the grants file, because it's
unconditional. That is a real fail-closed trade-off, not a hypothetical:
GitHub also withholds repo secrets entirely from `pull_request` runs
triggered by a fork (this repo has none historically, but the code path
exists). `--credential-origin` exists so the failure message names which case
applies instead of leaving an operator to guess at 3am.

Rate-limit note (OMN-16373): the original `--credential-origin` diagnostic
named a *scope* problem as the leading hypothesis for ANY non-zero `gh`
exit. That was wrong for the single most common real failure. GitHub
returns **HTTP 403 for BOTH** "your token lacks the scope" and "you
exhausted the REST rate limit", and `CROSS_REPO_PAT` shares one 5,000
req/hr primary bucket with every other tool, agent, and workflow acting as
its owner. Every observed INCONCLUSIVE on this job to date has been the
rate-limit case (e.g. jobs 98501448095 / 98499618819, 2026-08-27:
"API rate limit exceeded for user ID 1002253 ... (HTTP 403)"), yet the
message asserted a scope regression — sending at least five separate
lanes chasing a credential that was never broken. Failures are therefore
now classified from the error BODY, not the status code, and the
retryable classes (rate limit, 5xx/network) are retried within the job's
own timeout budget before the gate gives up.

The gate remains fail-closed for policy and credential failures. Confirmed
rate-limit exhaustion is different: it proves only that the shared API bucket
is empty, not that the token lost `read:org` or that the platform-leads
assumption changed. After the bounded retry budget, the job exits 0 with a
DEFERRED diagnostic so one overloaded credential cannot wedge every PR in the
repo. Retrying a *transient* failure is not weakening a gate — reporting a
transient failure as a permanent credential defect is.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from enum import StrEnum
from typing import TYPE_CHECKING, Final, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

DEFAULT_ORG = "OmniNode-ai"
DEFAULT_TEAM = "platform-leads"
DEFAULT_REPO = "OmniNode-ai/onex_change_control"
DEFAULT_BRANCH = "dev"

#: Total attempts (initial + retries) for a retryable `gh api` failure.
GH_MAX_ATTEMPTS: Final[int] = 4
#: Exponential-backoff base for transient (5xx / network) failures.
GH_BACKOFF_BASE_SECONDS: Final[float] = 2.0
#: Hard ceiling on a single rate-limit wait. The job's timeout-minutes is 45;
#: this keeps the worst case (3 waits) well inside it rather than burning the
#: whole budget on one sleep and dying without a diagnostic.
GH_MAX_RATE_LIMIT_WAIT_SECONDS: Final[float] = 600.0
#: Cushion added to a reported reset epoch — GitHub's reset is a boundary, and
#: retrying on the exact second reliably returns another 403.
GH_RATE_LIMIT_RESET_CUSHION_SECONDS: Final[float] = 5.0


class EnumGhFailureClass(StrEnum):
    """Why a `gh api` call failed — classified from the error body, not the status.

    HTTP 403 is ambiguous by itself: GitHub uses it for both "insufficient
    scope" and "rate limit exceeded". Branching on the status code is what
    produced the OMN-16373 misdiagnosis; branching on the message does not.
    """

    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    PERMISSION = "permission"
    UNCLASSIFIED = "unclassified"


#: Substrings that identify a rate-limit rejection (primary or secondary).
_RATE_LIMIT_SIGNATURES: Final[tuple[str, ...]] = (
    "rate limit exceeded",
    "secondary rate limit",
    "exceeded a secondary rate limit",
    "was submitted too quickly",
    "you have triggered an abuse detection mechanism",
)

#: Substrings that identify a transient server-side or network failure.
_TRANSIENT_SIGNATURES: Final[tuple[str, ...]] = (
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "no server is currently available",
    "server error",
    "connection reset",
    "unexpected eof",
    "timeout awaiting",
    "i/o timeout",
    "temporary failure in name resolution",
)

#: Substrings that identify a genuine credential/permission rejection — the
#: only class for which "check the token's scopes" is the right advice.
_PERMISSION_SIGNATURES: Final[tuple[str, ...]] = (
    "http 401",
    "bad credentials",
    "requires authentication",
    "resource not accessible",
    "not accessible by integration",
    "must have admin rights",
    "insufficient scope",
    "token has not been granted",
)


class GiveUpContext(NamedTuple):
    """How hard this call actually tried before giving up.

    Bundled rather than passed loose so `_diagnose` keeps a readable
    signature, and so "how many attempts" and "how far away was the reset"
    can never drift apart in the message.
    """

    attempts_made: int
    reset_wait_seconds: float | None = None


class TripwireInconclusiveError(RuntimeError):
    """Raised when a required live fact could not be determined."""


class TripwireDeferredRateLimitError(TripwireInconclusiveError):
    """Raised when GitHub rate limiting, not policy state, blocks the live read."""


def _run_gh(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  Why: command args are fixed by caller, no shell.
        ["gh", *args],  # noqa: S607  Why: `gh` resolved from PATH, matching repo convention.
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def classify_gh_failure(stderr: str) -> EnumGhFailureClass:
    """Classify a failed `gh api` call from its error text.

    Order matters: rate-limit and transient signatures are checked before
    permission ones, because a rate-limit body also carries "HTTP 403" and a
    naive status-code match would swallow it.
    """
    lowered = stderr.lower()
    if any(sig in lowered for sig in _RATE_LIMIT_SIGNATURES):
        return EnumGhFailureClass.RATE_LIMITED
    if any(sig in lowered for sig in _TRANSIENT_SIGNATURES):
        return EnumGhFailureClass.TRANSIENT
    if any(sig in lowered for sig in _PERMISSION_SIGNATURES):
        return EnumGhFailureClass.PERMISSION
    return EnumGhFailureClass.UNCLASSIFIED


def seconds_until_core_reset(now: float | None = None) -> float | None:
    """Seconds until the core REST bucket resets, or None if unreadable.

    `GET /rate_limit` is documented as not counting against the rate limit,
    so this stays safe to call from inside a rate-limited state.
    """
    result = _run_gh(["api", "rate_limit"])
    if result.returncode != 0:
        return None
    try:
        reset = float(json.loads(result.stdout)["resources"]["core"]["reset"])
    except (ValueError, KeyError, TypeError):
        return None
    return max(0.0, reset - (time.time() if now is None else now))


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff for a transient failure on `attempt` (1-based)."""
    return GH_BACKOFF_BASE_SECONDS * float(2 ** (attempt - 1))


def _rate_limit_delay(reset_wait: float | None, attempt: int) -> float | None:
    """Seconds to wait out a rate limit, or None if waiting is futile.

    `reset_wait is None` means the core bucket's reset could not be read —
    typically a *secondary* rate limit, which has no published reset. Plain
    backoff is the right response there; giving up would turn a few seconds
    of throttling into a failed gate.
    """
    backoff = _backoff_seconds(attempt)
    if reset_wait is None:
        return backoff
    if reset_wait > GH_MAX_RATE_LIMIT_WAIT_SECONDS:
        return None
    return max(backoff, reset_wait + GH_RATE_LIMIT_RESET_CUSHION_SECONDS)


def _run_gh_checked(
    args: Sequence[str],
    *,
    action: str,
    credential_origin: str,
    sleep: Callable[[float], None] = time.sleep,
) -> subprocess.CompletedProcess[str]:
    """Run `gh api`, retrying retryable failures, or raise INCONCLUSIVE.

    Only rate-limit and transient failures are retried. A permission failure
    is returned immediately — retrying a revoked token wastes the job's
    budget and delays the operator seeing the real message.
    """
    for attempt in range(1, GH_MAX_ATTEMPTS + 1):
        result = _run_gh(args)
        if result.returncode == 0:
            return result
        failure = classify_gh_failure(result.stderr)
        reset_wait: float | None = None
        delay: float | None = None
        if attempt < GH_MAX_ATTEMPTS:
            if failure is EnumGhFailureClass.TRANSIENT:
                delay = _backoff_seconds(attempt)
            elif failure is EnumGhFailureClass.RATE_LIMITED:
                reset_wait = seconds_until_core_reset()
                delay = _rate_limit_delay(reset_wait, attempt)
        if delay is None:
            error_type: type[TripwireInconclusiveError]
            error_type = (
                TripwireDeferredRateLimitError
                if failure is EnumGhFailureClass.RATE_LIMITED
                else TripwireInconclusiveError
            )
            raise error_type(
                _diagnose(
                    action,
                    result.stderr,
                    credential_origin,
                    failure,
                    give_up=GiveUpContext(attempt, reset_wait),
                )
            )
        print(
            f"{action}: {failure.value} failure on attempt {attempt}/"
            f"{GH_MAX_ATTEMPTS}; retrying in {delay:.0f}s",
            file=sys.stderr,
        )
        sleep(delay)
    # Unreachable: the final attempt always raises above.
    raise TripwireInconclusiveError(  # pragma: no cover
        _diagnose(action, "retry loop exhausted", credential_origin, None)
    )


def _describe_give_up(give_up: GiveUpContext | None) -> str:
    """Say exactly why this call stopped retrying — no rounder-sounding fiction.

    An earlier draft of this message claimed "already retried up to 4 times"
    even when it had given up on the first attempt because the bucket's reset
    was hours away. Overstating the effort in a diagnostic is the same class
    of defect as misnaming the cause, so the numbers here are the real ones.
    """
    if give_up is None:
        return ""
    attempts_made = give_up.attempts_made
    reset_wait = give_up.reset_wait_seconds
    plural = "" if attempts_made == 1 else "s"
    detail = f" Gave up after {attempts_made} attempt{plural}"
    if reset_wait is not None and reset_wait > GH_MAX_RATE_LIMIT_WAIT_SECONDS:
        detail += (
            f": the bucket does not reset for ~{reset_wait / 60:.0f} min, "
            f"beyond this job's {GH_MAX_RATE_LIMIT_WAIT_SECONDS / 60:.0f} min wait "
            "budget, so waiting inside the job would only burn its timeout"
        )
    elif attempts_made >= GH_MAX_ATTEMPTS:
        detail += f" (the full retry budget of {GH_MAX_ATTEMPTS})"
    return detail + "."


def _diagnose(
    action: str,
    stderr: str,
    credential_origin: str,
    failure: EnumGhFailureClass | None = None,
    *,
    give_up: GiveUpContext | None = None,
) -> str:
    """Build a legible diagnostic that names the real cause before anything else.

    OMN-14445 review: a fail-closed gate whose failure can't be decoded at
    3am invites `--no-verify` habits. This is explicit about the most likely
    cause FIRST, then the raw gh error, so "this is a token problem, not a
    policy violation" is the first thing an operator reads.

    OMN-16373: when the failure is classifiable from the error body, that
    classification WINS over the credential-origin heuristic. Naming a scope
    regression for what is actually a shared-bucket rate limit is worse than
    saying nothing — it sends people to rotate a working credential.
    """
    raw = stderr.strip() or "unknown gh api error"
    if failure is EnumGhFailureClass.RATE_LIMITED:
        cause = (
            "RATE LIMIT, NOT A SCOPE PROBLEM: GitHub rejected this read "
            "because the REST rate-limit bucket for this token's owner was "
            "exhausted — GitHub returns HTTP 403 for this exactly as it does "
            "for a scope failure, so the status code alone does not "
            "distinguish them. The token's scopes are NOT implicated: this "
            "same call succeeds on other PRs in the same window."
            f"{_describe_give_up(give_up)} Fix: "
            "re-run this job once the bucket resets, and reduce the "
            "concurrent API load sharing that identity. Do NOT rotate the "
            "PAT on this evidence."
        )
        return f"{cause}\n{action}: {raw}"
    if failure is EnumGhFailureClass.TRANSIENT:
        cause = (
            "TRANSIENT GITHUB API FAILURE, NOT A SCOPE PROBLEM: GitHub "
            "returned a server-side or network error, which says nothing "
            "about this token's scopes or about platform-leads membership."
            f"{_describe_give_up(give_up)} Fix: "
            "check githubstatus.com, then re-run this job."
        )
        return f"{cause}\n{action}: {raw}"
    if credential_origin == "fallback":
        cause = (
            "TOKEN PROBLEM, NOT A POLICY VIOLATION: CROSS_REPO_PAT was not "
            "available for this run (unset, expired/revoked, or withheld by "
            "GitHub for a fork-originated PR) — this job fell back to the "
            "default GITHUB_TOKEN, which cannot read org-private resources "
            "(org team membership / branch protection) by GitHub design. "
            "Fix: restore a valid CROSS_REPO_PAT with read:org scope in "
            "repo secrets. This is NOT evidence that platform-leads grew or "
            "that anyone did anything wrong."
        )
    elif credential_origin == "cross_repo_pat":
        cause = (
            "TOKEN PROBLEM, NOT A POLICY VIOLATION: CROSS_REPO_PAT was "
            "present but the API call still failed, and the error body "
            "matched no known rate-limit or transient signature. The "
            "remaining candidates are that it lacks read:org scope or lost "
            "org access. Fix: verify the PAT's scopes and that its owner is "
            "still an org member — but FIRST confirm the raw error below is "
            "not a rate-limit message this classifier failed to recognise "
            "(OMN-16373), because a working PAT has been misdiagnosed as an "
            "expired one on exactly that mistake."
        )
    else:
        cause = (
            "Could not determine whether CROSS_REPO_PAT was available for "
            "this run; treat as a possible token problem before assuming a "
            "policy violation."
        )
    return f"{cause}\n{action}: {raw}"


def get_team_member_count(
    org: str, team_slug: str, *, credential_origin: str = "unknown"
) -> int:
    """Return the live member count of `org/team_slug`, or raise if unreadable."""
    result = _run_gh_checked(
        ["api", f"orgs/{org}/teams/{team_slug}/members", "--jq", "length"],
        action=f"could not read membership of {org}/{team_slug}",
        credential_origin=credential_origin,
    )
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        msg = f"unexpected member-count output for {org}/{team_slug}: {result.stdout!r}"
        raise TripwireInconclusiveError(msg) from exc


def is_review_required(
    repo: str, branch: str, *, credential_origin: str = "unknown"
) -> bool:
    """Return True if `required_pull_request_reviews` is configured on `repo@branch`."""
    result = _run_gh_checked(
        [
            "api",
            f"repos/{repo}/branches/{branch}/protection",
            "--jq",
            'has("required_pull_request_reviews")',
        ],
        action=f"could not read branch protection for {repo}@{branch}",
        credential_origin=credential_origin,
    )
    return result.stdout.strip() == "true"


def evaluate(
    *, member_count: int, review_required: bool, org: str, team: str
) -> tuple[bool, str]:
    """Pure decision logic, isolated from I/O so it is directly unit-testable.

    Returns (safe, message).
    """
    if review_required:
        return (
            True,
            f"PASS: required_pull_request_reviews is enabled — CODEOWNERS review "
            f"enforces approver identity independently of @{org}/{team} team size "
            f"({member_count} member(s)).",
        )
    if member_count > 1:
        return (
            False,
            f"TRIPWIRE TRIPPED: @{org}/{team} has {member_count} members but "
            "required_pull_request_reviews is not enabled — OMN-14441's "
            "approved_by != PR-author check can no longer distinguish an honest "
            "approval from a forged one (a PR author could name any other "
            "platform lead without them reviewing anything). See OMN-14445.",
        )
    return (
        True,
        f"PASS (by construction): @{org}/{team} has {member_count} member(s); an "
        "honest approved_by has only one possible value. This holds only until "
        "a second platform lead joins — see OMN-14445 for the operator decision "
        "needed before that happens.",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", default=DEFAULT_ORG)
    parser.add_argument("--team", default=DEFAULT_TEAM)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument(
        "--credential-origin",
        default="unknown",
        choices=["cross_repo_pat", "fallback", "unknown"],
        help=(
            "Which token supplied GH_TOKEN for this run — 'cross_repo_pat' if "
            "the elevated secret was present, 'fallback' if it fell back to "
            "the default GITHUB_TOKEN. Enriches the INCONCLUSIVE diagnostic; "
            "does not change pass/fail logic."
        ),
    )
    args = parser.parse_args(argv)

    try:
        member_count = get_team_member_count(
            args.org, args.team, credential_origin=args.credential_origin
        )
        review_required = is_review_required(
            args.repo, args.branch, credential_origin=args.credential_origin
        )
    except TripwireDeferredRateLimitError as exc:
        print(f"TRIPWIRE DEFERRED: {exc}", file=sys.stderr)
        return 0
    except TripwireInconclusiveError as exc:
        print(f"TRIPWIRE INCONCLUSIVE: {exc}", file=sys.stderr)
        return 2

    print(f"@{args.org}/{args.team} member count: {member_count}")
    print(
        f"required_pull_request_reviews on {args.repo}@{args.branch}: {review_required}"
    )
    safe, message = evaluate(
        member_count=member_count,
        review_required=review_required,
        org=args.org,
        team=args.team,
    )
    print(message, file=sys.stderr if not safe else sys.stdout)
    return 0 if safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
