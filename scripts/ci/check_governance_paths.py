#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Governance-path exclusion for auto-merge arming (OMN-16117).

Why this exists
----------------
`auto-merge.yml` arms squash auto-merge on every PR authored by
`jonahgabriel`, gated only on `occ-preflight / eligibility` success. Agent
sessions in this org share that GitHub identity, so an agent-opened PR that
touches `grants/prod_promotion_grants.yaml` (the OMN-13418 prod-promotion
trust anchor) or `allowlists/skip_token_approvals.yaml` (the skip-token
approval registry) could be armed and merged unattended, with zero human
review (`onex_change_control@main` has `requiresApprovingReviews=false`).

Live near-miss 2026-08-17 ~05:30Z: `auto-merge.yml` armed a freshly opened
grant PR within seconds of open; containment was manual
(`convertPullRequestToDraft`).

This is a DIFFERENT closure from OMN-14919's
`check_bot_authored_authz_guard` in `ci.yml`, which rejects BOT-authored
(GitHub author type == Bot) PRs touching `grants/**` / `allowlists/**`. That
guard does not fire here because an agent session acting as `jonahgabriel`
has author type `User`, not `Bot`. This module closes the actor-gate hole
directly: regardless of actor identity or `occ-preflight` outcome, a PR
whose diff touches a governance file must never be armed for auto-merge or
enqueued.

Fail-closed semantics
----------------------
The workflow resolves the PR's changed-file set via the GitHub API before
calling this module. Three distinct outcomes are possible:

* The API call succeeds and returns one or more governance-file paths ->
  EXCLUDE (skip arming).
* The API call succeeds and returns a changed-file set with NO governance
  file present (including a genuinely empty set, e.g. an empty commit) ->
  safe to arm.
* The API call fails, the PR number is missing, or the result cannot be
  parsed as a JSON array -> the changed-file set is UNDETERMINED -> EXCLUDE
  (fail closed). An undetermined query and a genuinely clean diff look
  identical as far as arming behavior is concerned; they must not be
  conflated. The CLI distinguishes them by requiring the caller to pass the
  literal JSON `null` (or omit the flag entirely) for the undetermined case
  and a JSON array (possibly `[]`) for the resolved case.

This module performs **no** network I/O. The workflow is responsible for
fetching the PR's changed-file set via `gh api` and passing it in; this
module only classifies and decides -- the same separation of concerns as
`scripts/ci/merge_queue_enqueue.py` (OMN-13214).

Usage
-----
    # Resolved, safe:
    python3 scripts/ci/check_governance_paths.py check \
        --changed-files-json '["contracts/OMN-16117.yaml"]'

    # Resolved, touches a governance file:
    python3 scripts/ci/check_governance_paths.py check \
        --changed-files-json '["grants/prod_promotion_grants.yaml"]'

    # Undetermined (API error) -- fail closed:
    python3 scripts/ci/check_governance_paths.py check \
        --changed-files-json 'null'

Exit codes
----------
    0: SAFE_TO_ARM -- changed-file set resolved and no governance file touched.
    1: EXCLUDE      -- a governance file was touched, OR the changed-file set
                       could not be determined (fail closed).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Policy constant — the exact authorization surfaces a PR must never be
# auto-merge-armed while touching, regardless of actor identity or
# occ-preflight outcome. Kept as a single tuple so future governance paths
# are added in exactly one place (per OMN-16117's fix shape).
# ---------------------------------------------------------------------------

GOVERNANCE_PATHS: tuple[str, ...] = (
    "grants/prod_promotion_grants.yaml",
    "allowlists/skip_token_approvals.yaml",
)

_EXIT_SAFE_TO_ARM = 0
_EXIT_EXCLUDE = 1


# ---------------------------------------------------------------------------
# Pure decision logic — no I/O, directly unit-testable.
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> str:
    return path.strip().lstrip("./")


def is_governance_path(path: str) -> bool:
    """Return True if ``path`` is exactly one of the guarded governance files."""
    return _normalize_path(path) in GOVERNANCE_PATHS


def touches_governance_path(changed_files: Sequence[str] | None) -> bool:
    """Decide whether a PR must be excluded from auto-merge arming.

    ``changed_files`` is the PR's changed-file paths, resolved by the caller,
    or ``None`` when the changed-file set could not be determined at all
    (API error, missing PR number). ``None`` is NOT the same as an empty
    sequence: an empty sequence means "the query succeeded and the PR
    genuinely touches nothing" (safe), while ``None`` means "we don't know"
    (fail closed -> excluded).
    """
    if changed_files is None:
        return True
    return any(is_governance_path(f) for f in changed_files)


# ---------------------------------------------------------------------------
# CLI surface — the workflow shells out to this.
# ---------------------------------------------------------------------------


def _load_changed_files(raw: str | None) -> list[str] | None:
    """Parse the ``--changed-files-json`` CLI value into a resolved list or None.

    ``raw`` is the JSON text the workflow captured from
    ``gh api repos/.../pulls/<n>/files``, or the literal string ``"null"``
    when the workflow's own fetch failed, or ``None``/empty string when the
    flag was omitted entirely (also undetermined). Any of these forms of
    "we don't know" collapse to ``None`` -- fail closed -- rather than being
    silently treated as an empty (and therefore "safe") changed-file list.
    """
    if raw is None or raw.strip() == "":
        return None

    parsed = json.loads(raw)  # raises json.JSONDecodeError on malformed input
    if parsed is None:
        return None
    if not isinstance(parsed, list):
        msg = "changed-files-json must be a JSON array or the literal null"
        raise TypeError(msg)
    return [str(item) for item in parsed]


def main(argv: list[str] | None = None) -> int:
    """CLI surface for the workflow.

    Subcommand:
        check --changed-files-json <json|null>
            Prints ``safe_to_arm`` (exit 0) or ``exclude`` (exit 1).
    """
    parser = argparse.ArgumentParser(
        description="Governance-path auto-merge exclusion check (OMN-16117)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser(
        "check", help="Decide whether a PR is excluded from auto-merge arming"
    )
    p_check.add_argument(
        "--changed-files-json",
        default=None,
        help=(
            "JSON array of changed file paths from the PR diff, or the "
            "literal 'null' (or omit this flag) when the changed-file set "
            "could not be determined -- fails closed to 'exclude'."
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "check":
        try:
            changed_files = _load_changed_files(args.changed_files_json)
        except (json.JSONDecodeError, TypeError) as exc:
            sys.stderr.write(
                f"could not parse --changed-files-json: {exc}; failing closed\n"
            )
            sys.stdout.write("exclude\n")
            return _EXIT_EXCLUDE

        if touches_governance_path(changed_files):
            sys.stdout.write("exclude\n")
            return _EXIT_EXCLUDE

        sys.stdout.write("safe_to_arm\n")
        return _EXIT_SAFE_TO_ARM

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
