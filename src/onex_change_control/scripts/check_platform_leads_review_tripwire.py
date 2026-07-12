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
    2: INCONCLUSIVE — could not determine one or both facts (e.g. the
       token lacks scope to read team membership or branch protection).
       Treated as a failure: an unproven safety property does not pass.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_ORG = "OmniNode-ai"
DEFAULT_TEAM = "platform-leads"
DEFAULT_REPO = "OmniNode-ai/onex_change_control"
DEFAULT_BRANCH = "dev"


class TripwireInconclusiveError(RuntimeError):
    """Raised when a required live fact could not be determined."""


def _run_gh(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  Why: command args are fixed by caller, no shell.
        ["gh", *args],  # noqa: S607  Why: `gh` resolved from PATH, matching repo convention.
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def get_team_member_count(org: str, team_slug: str) -> int:
    """Return the live member count of `org/team_slug`, or raise if unreadable."""
    result = _run_gh(["api", f"orgs/{org}/teams/{team_slug}/members", "--jq", "length"])
    if result.returncode != 0:
        msg = (
            f"could not read membership of {org}/{team_slug}: "
            f"{result.stderr.strip() or 'unknown gh api error'}"
        )
        raise TripwireInconclusiveError(msg)
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        msg = f"unexpected member-count output for {org}/{team_slug}: {result.stdout!r}"
        raise TripwireInconclusiveError(msg) from exc


def is_review_required(repo: str, branch: str) -> bool:
    """Return True if `required_pull_request_reviews` is configured on `repo@branch`."""
    result = _run_gh(
        [
            "api",
            f"repos/{repo}/branches/{branch}/protection",
            "--jq",
            'has("required_pull_request_reviews")',
        ]
    )
    if result.returncode != 0:
        msg = (
            f"could not read branch protection for {repo}@{branch}: "
            f"{result.stderr.strip() or 'unknown gh api error'}"
        )
        raise TripwireInconclusiveError(msg)
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
    args = parser.parse_args(argv)

    try:
        member_count = get_team_member_count(args.org, args.team)
        review_required = is_review_required(args.repo, args.branch)
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
