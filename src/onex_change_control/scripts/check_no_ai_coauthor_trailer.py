#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Fail a pull request whose commits carry an AI co-author trailer.

Usage:
    gh api "repos/$REPO/pulls/$N/commits" --paginate --slurp \
      | python3 src/onex_change_control/scripts/check_no_ai_coauthor_trailer.py

    python3 src/onex_change_control/scripts/check_no_ai_coauthor_trailer.py \
      --message-file msg.txt

Exit 0 = clean.  Exit 1 = at least one commit carries the trailer.

WHY THIS EXISTS (OMN-18426)

The `strip-ai-coauthor-trailer` pre-commit hook is declared in all fifteen
repos that have a `.pre-commit-config.yaml`, on the `commit-msg` stage, with a
byte-identical entry. It works: run through pre-commit it strips the trailer
and leaves human co-authors alone.

It still cannot keep the trailer off a default branch. Measured 2026-09-19:
986 commits across fourteen repos carry `Co-authored-by: … <noreply@anthropic
.com>`, the most recent dated that same day. Every recent one has
`committer=GitHub <noreply@github.com>` — they are server-side squash merges.
GitHub composes the squash message from the branch's commits and re-emits
their trailers, and a `commit-msg` hook runs on `git commit`, on a
workstation. It is not in that path.

So the trailer has to be caught while it is still on the branch, which is what
this check does.

MACHINE-IDENTITY LIST

The maintained machine identities currently in scope are the assistant at both
of its observed addresses, Cursor, and the Codex family. Matching is by exact
email address, not by display name: a human named Claude at another address is
still a human co-author and remains untouched.

The pattern below is the same one the hook's perl entry uses:

    perl -i -ne 'print unless /^co-authored-by:.*<(?:noreply\\@anthropic\\.com|'
    'claude\\@omninode\\.ai|cursoragent\\@cursor\\.com|codex\\@omninode\\.ai)>/i'

Deliberately identical, including the `^` anchor. A gate stricter than the
hook would fail PRs the hook considers clean, and an author who fixed the
commit locally would have no way to satisfy CI. tests/ asserts the parity by
running the real perl entry over the same corpus.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Same identity list as the pre-commit hook's perl entry, same anchor and
# case-insensitivity. Do not tighten one without the other.
_AI_COAUTHOR_RE = re.compile(
    r"^co-authored-by:.*<(?:noreply@anthropic\.com|claude@omninode\.ai|cursoragent@cursor\.com|codex@omninode\.ai)>",
    re.IGNORECASE,
)

_REMEDIATION = """
A Co-authored-by trailer matching the maintained machine-identity list is present on
a commit in this pull request.

It matters here and not only locally: a squash merge composes its message from
these commits and re-emits their trailers, so the trailer lands on the default
branch even though every repo installs a commit-msg hook that would have
stripped it at `git commit` time.

To fix, rewrite the offending commit messages on the branch:

    git rebase -i <base>     # reword each commit listed above
    git push --force-with-lease

If the hook never ran locally, install it once per clone — it is already
declared in this repo's .pre-commit-config.yaml:

    pre-commit install
"""


def offending_lines(message: str) -> list[str]:
    """Return the trailer lines in `message` that the commit-msg hook would strip."""
    return [line for line in message.splitlines() if _AI_COAUTHOR_RE.match(line)]


def normalise(payload: object) -> list[dict[str, str]]:
    """Flatten whatever `gh api --paginate --slurp` produced into {sha, message}.

    Three shapes reach this, and jq is not used to pre-shape them because it is
    not guaranteed on a self-hosted runner and `gh api --slurp` refuses `--jq`
    anyway:
      * a list of GitHub commit objects        {sha, commit: {message}}
      * a list of pages, each such a list      [[{...}], [{...}]]
      * the already-simplified {sha, message}  (what the tests and --message-file use)
    """
    if not isinstance(payload, list):
        msg = "commit payload must be a JSON array"
        raise TypeError(msg)
    flat: list[object] = []
    for item in payload:
        flat.extend(item) if isinstance(item, list) else flat.append(item)

    commits: list[dict[str, str]] = []
    for item in flat:
        if not isinstance(item, dict):
            msg = f"commit entry is not an object: {item!r}"
            raise TypeError(msg)
        inner = item.get("commit")
        message = item.get("message")
        if message is None and isinstance(inner, dict):
            message = inner.get("message")
        commits.append(
            {"sha": str(item.get("sha") or "(no sha)"), "message": str(message or "")}
        )
    return commits


def check_commits(commits: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Return (sha, line) for every offending trailer across every commit."""
    findings: list[tuple[str, str]] = []
    for commit in commits:
        sha = str(commit.get("sha", "") or "(no sha)")
        for line in offending_lines(str(commit.get("message", "") or "")):
            findings.append((sha, line))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--message-file",
        type=Path,
        help="check one commit message file instead of reading JSON on stdin",
    )
    args = parser.parse_args(argv)

    if args.message_file is not None:
        commits = [
            {"sha": str(args.message_file), "message": args.message_file.read_text()}
        ]
    else:
        raw = sys.stdin.read().strip()
        if not raw:
            # Fail closed: an empty payload means the caller fetched nothing,
            # not that the pull request is clean.
            sys.stderr.write(
                "[FAIL] no commit payload on stdin — refusing to report a clean "
                "result from an empty fetch\n"
            )
            return 2
        try:
            commits = normalise(json.loads(raw))
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"[FAIL] commit payload is not valid JSON: {exc}\n")
            return 2
        except TypeError as exc:
            sys.stderr.write(f"[FAIL] {exc}\n")
            return 2

    findings = check_commits(commits)
    if not findings:
        print(f"[PASS] {len(commits)} commit(s) checked, no AI co-author trailer")
        return 0

    print(
        f"[FAIL] {len(findings)} AI co-author trailer(s) on {len(commits)} commit(s):"
    )
    for sha, line in findings:
        print(f"  {sha[:12]}  {line.strip()}")
    print(_REMEDIATION)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
