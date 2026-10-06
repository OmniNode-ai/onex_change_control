# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tell a lane about the writer-App path at PUSH time, not at PR time (OMN-18804).

WHY THIS EXISTS
---------------
`check-human-authored-privileged-pr` (OMN-18327) refuses a human-authored PR
whose diff touches `src/`, `scripts/` or `.github/`, because every lane in
this fleet commits under ONE shared human account, so such a PR is
un-approvable by construction — the shape that froze the fleet behind
OCC#9362 on 2026-09-13. That rule is right. Its DISCOVERABILITY is the
defect this module closes: nothing on the local path says a word until the
PR is already open, and the first signal is the gate's refusal.

Three lanes in one window each paid 10-15 minutes for that: they opened a
human PR, read the refusal, closed the PR, dispatched
`open-pr-as-writer-app.yml`, and in one case had to merge `dev` to pick up an
OCC companion that had been minted against the now-closed PR number
(OCC#10338 to OCC#10341; OCC#10339 to OCC#10342; and the CI Summary lane).

WHY THIS IS NOT A BLANKET REFUSAL OF THE PUSH
---------------------------------------------
`open-pr-as-writer-app.yml` creates no branch and pushes nothing. It takes a
branch ALREADY PUSHED to this repo and opens a PR for it as the App — its own
resolve step fails with "branch ... does not exist on origin - push it
first". So the privileged push is the FIRST HALF OF THE SANCTIONED PATH, and
a hook that refused it would refuse the very thing its message tells the lane
to do. There is no carve-out for "a branch the App workflow creates", because
the workflow creates none.

What IS unrecoverable in place is a privileged branch whose open PR is
human-authored: the App workflow EDITS an existing open PR rather than
re-authoring it (`gh pr edit` when one exists), so the author of record stays
human and the gate keeps refusing. That state needs the PR closed first, and
that is the one state this hook exits non-zero on.

    no open PR                  -> advise, exit 0  (the sanctioned first step)
    open PR, App/bot author     -> silent, exit 0  (already on the path)
    open PR, human author       -> REFUSE, exit 1  (close it, then dispatch)
    PR state unreadable         -> advise, exit 0  (see below)

HONEST LIMITS
-------------
- **This is a hint, not a gate.** The mechanical gate is
  `check-human-authored-privileged-pr` in CI. A pre-push hook that blocked
  pushes whenever `gh` was unreachable or the network was down would be
  routed around within a day, and rule 17 is explicit that routing around a
  hook is never the remedy — so an unreadable PR state advises rather than
  refuses. It fails OPEN on purpose, and says so in its own output.
- **It reads paths, never content**, and it reads the SAME prefix tuple the
  gate refuses on, imported rather than restated so the two cannot drift.
- **It cannot know the lane's intent.** A lane that genuinely needs a human
  hotfix PR uses the gate's own whole-line escape annotation; this hook
  points at the App path because that is the sanctioned default, not because
  the escape is unavailable.

Usage (the pre-push hook passes the changed files as positional arguments):

    uv run check-privileged-path-push src/onex_change_control/scripts/x.py
    uv run check-privileged-path-push --branch my-branch --no-remote src/x.py

Exit codes:
    0: allowed — nothing privileged, already App-authored, or advised
    1: refused — a privileged push onto a branch whose open PR is human-authored
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Final

from onex_change_control.scripts.check_human_authored_privileged_pr import (
    PRIVILEGED_PATH_PREFIXES,
    RECOVERY_COMMAND_TEMPLATE,
    TICKET_PLACEHOLDER,
)

__all__ = [
    "PRIVILEGED_PATH_PREFIXES",
    "RECOVERY_COMMAND_TEMPLATE",
    "ModelOpenPrFacts",
    "evaluate",
    "main",
    "privileged_paths",
    "ticket_from_branch",
]

# The recovery command and the ticket placeholder are IMPORTED, never
# restated: the gate's refusal and this hint must render one literal, or a
# lane greps for the string it read in one place and does not find it in the
# other.

_TICKET_RE: Final[re.Pattern[str]] = re.compile(r"omn-(\d+)", re.IGNORECASE)

DEFAULT_REPO: Final[str] = "OmniNode-ai/onex_change_control"

#: How many privileged paths the output names before it elides.
MAX_NAMED_PATHS: Final[int] = 10


@dataclass(frozen=True)
class ModelOpenPrFacts:
    """The two facts about an open PR that decide this hook's verdict."""

    number: int
    author_type: str


def privileged_paths(changed_files: list[str]) -> list[str]:
    """Return the changed paths under a privileged prefix, gate-identical."""
    return [
        path
        for path in changed_files
        if any(path.startswith(prefix) for prefix in PRIVILEGED_PATH_PREFIXES)
    ]


def ticket_from_branch(branch: str) -> str:
    """Read an OMN id out of a branch name, or return the placeholder."""
    match = _TICKET_RE.search(branch)
    return f"OMN-{match.group(1)}" if match else TICKET_PLACEHOLDER


def recovery_command(branch: str) -> str:
    """Render the dispatch command for this branch."""
    return RECOVERY_COMMAND_TEMPLATE.format(
        branch=branch or "<branch>", ticket=ticket_from_branch(branch)
    )


def _is_bot(author_type: str) -> bool:
    """True when GitHub reports a Bot/App author.

    Case- and whitespace-tolerant for the same reason the gate is: getting
    this wrong in the strict direction would refuse every companion.
    """
    return author_type.strip().casefold() == "bot"


def _named(touched: list[str]) -> str:
    shown = ", ".join(sorted(touched)[:MAX_NAMED_PATHS])
    return f"{shown}{' and more' if len(touched) > MAX_NAMED_PATHS else ''}"


def evaluate(
    *,
    branch: str,
    changed_files: list[str],
    open_pr: ModelOpenPrFacts | None,
    pr_state_readable: bool = True,
) -> tuple[bool, str]:
    """Pure decision logic, isolated from I/O so it is directly unit-testable.

    Returns (allowed, message). An empty message means there is nothing worth
    interrupting the push to say.
    """
    touched = privileged_paths(changed_files)
    if not touched:
        return True, ""

    if open_pr is not None and _is_bot(open_pr.author_type):
        return True, ""

    command = recovery_command(branch)

    if open_pr is not None:
        return False, (
            "REFUSED: this push touches "
            f"{len(touched)} privileged path(s) ({_named(touched)}) on a branch "
            f"whose open PR #{open_pr.number} is authored by a human account.\n"
            "`check-human-authored-privileged-pr` refuses that PR: every lane "
            "here commits as one person, so a human-authored PR on an owned "
            "path is un-approvable by construction (GitHub blocks "
            "self-approval), which is what froze the fleet behind OCC#9362 on "
            "2026-09-13.\n"
            "Re-dispatching alone will NOT fix it — the App workflow edits an "
            "existing open PR rather than re-authoring it, so the author of "
            "record stays human. Close it first, then dispatch:\n\n"
            f"    gh pr close {open_pr.number} --repo {DEFAULT_REPO}\n"
            f"    {command}\n\n"
            "For a genuine human hotfix, add the human-author escape "
            "annotation on a line of its own in the PR body naming the ticket "
            "(the exact form is in check_human_authored_privileged_pr's "
            "docstring; it is deliberately not spelled in prose a body-parsing "
            "gate might read)."
        )

    unreadable = (
        ""
        if pr_state_readable
        else (
            "The open-PR state for this branch could not be read, so this is "
            "advice rather than a verdict — the mechanical gate is CI.\n"
        )
    )
    return True, (
        f"NOTE: this push touches {len(touched)} privileged path(s) "
        f"({_named(touched)}).\n"
        f"{unreadable}"
        "A change-control PR on a privileged surface is opened AS the "
        "onexbot-occ-writer App, not with `gh pr create` — a human-authored "
        "one is refused by check-human-authored-privileged-pr and costs a "
        "close-and-reopen. This push is the sanctioned path's first step; the "
        "second is:\n\n"
        f"    {command}\n"
    )


def _read_open_pr(branch: str, repo: str) -> tuple[ModelOpenPrFacts | None, bool]:
    """Return (facts_or_None, readable). Never raises: this is a hint."""
    owner = repo.split("/", 1)[0]
    try:
        result = subprocess.run(  # noqa: S603  Why: fixed argv, no shell.
            [  # noqa: S607  Why: `gh` from PATH, repo convention.
                "gh",
                "api",
                f"repos/{repo}/pulls?state=open&head={owner}:{branch}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None, False
    if result.returncode != 0:
        return None, False
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, False
    if not isinstance(payload, list):
        return None, False
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        user = entry.get("user")
        author_type = user.get("type", "") if isinstance(user, dict) else ""
        number = entry.get("number")
        if isinstance(number, int):
            return ModelOpenPrFacts(number=number, author_type=str(author_type)), True
    return None, True


def _current_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="changed paths, from the hook.")
    parser.add_argument(
        "--branch", help="branch being pushed; read from git if omitted."
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/repo to query.")
    parser.add_argument(
        "--no-remote",
        action="store_true",
        help="skip the open-PR lookup; advise on the paths alone.",
    )
    args = parser.parse_args(argv)

    branch = args.branch if args.branch is not None else _current_branch()

    if not privileged_paths(args.files):
        return 0

    if args.no_remote:
        open_pr, readable = None, True
    else:
        open_pr, readable = _read_open_pr(branch, args.repo)

    allowed, message = evaluate(
        branch=branch,
        changed_files=args.files,
        open_pr=open_pr,
        pr_state_readable=readable,
    )
    if message:
        print(message, file=sys.stderr)
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
