# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Guardrail for the writer-App PR dispatch path (OMN-18327).

`open-pr-as-writer-app.yml` lets a lane open a change-control PR AS the
`onexbot-occ-writer` App instead of under the shared human account. That is
the operator's fix for the 2026-09-13 freeze, when a validator change arrived
under the human account, became un-approvable (GitHub blocks self-approval,
and every lane here commits as one person) and queued every product PR whose
OCC companion had to merge behind it. The ruling is recorded verbatim at
`docs/tracking/ROLLING_WORK_LEDGER.md:7452`.

Giving a dispatch path an App token raises exactly the question the
evidence-import guardrail already answers for its own path: **workflow files
are the real privilege boundary of this repository**, and a route that can
open a PR as a privileged identity must not become a route that quietly
edits the routes. `check_evidence_import_allowed.py` refuses `.github/
workflows/` changes outright, because an *evidence* import has no business
carrying one.

This path cannot refuse them outright — changing a workflow is legitimate
change-control work, and it is precisely the class of change that most needs
to arrive as the App rather than as an un-approvable human PR. So it takes
the same posture one notch narrower: a workflow-file change is allowed only
when **the change itself names the dispatching ticket**. The citation must be
in an ADDED line of the workflow file, not in the PR body, not in the commit
message, and not in the dispatch inputs — all three of those are supplied by
the same caller as the branch, so none of them is independent of it. A line
in the diff is reviewable afterwards by anyone reading the file, which is the
property that makes it evidence.

Fails closed throughout: an unreadable diff, an unresolvable ref, a branch
with no commits ahead of base, and a malformed ticket id are all refusals.
A refusal here is not a dead end — push the same branch with the ticket id
written into the workflow change and re-dispatch.

Usage:
    uv run check-app-pr-branch-allowed \\
        --base-ref origin/dev --head-ref my-branch --ticket OMN-18327

Exit codes:
    0: the branch may be opened as a PR by the App
    1: refused, with every offending path named
    2: inconclusive — a required fact could not be read
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from typing import Final

WORKFLOW_PATH_PREFIX: Final[str] = ".github/workflows/"
TICKET_RE: Final[re.Pattern[str]] = re.compile(r"^OMN-\d+$")


class AppPrBranchInconclusiveError(RuntimeError):
    """A fact needed for the verdict could not be read. Never a silent pass."""


def _git(args: list[str]) -> str:
    result = subprocess.run(  # noqa: S603  Why: fixed argv, no shell.
        ["git", *args],  # noqa: S607  Why: `git` from PATH, repo convention.
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown git error"
        msg = f"git {' '.join(args)} failed: {detail}"
        raise AppPrBranchInconclusiveError(msg)
    return result.stdout


def changed_workflow_paths(base_ref: str, head_ref: str) -> list[str]:
    """Workflow files the branch adds, modifies, renames or deletes."""
    raw = _git(["diff", "--name-only", f"{base_ref}...{head_ref}"])
    return sorted(
        line.strip()
        for line in raw.splitlines()
        if line.strip().startswith(WORKFLOW_PATH_PREFIX)
    )


def added_lines_for_path(base_ref: str, head_ref: str, path: str) -> list[str]:
    """The lines this branch ADDS to `path`.

    Added lines only. A ticket id that was already in the file before this
    branch existed is not this change citing its ticket — it is a previous
    change's citation, and accepting it would let any workflow edit ride in
    behind someone else's.
    """
    raw = _git(["diff", "--unified=0", f"{base_ref}...{head_ref}", "--", path])
    return [
        line[1:]
        for line in raw.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


def evaluate(
    *, ticket: str, workflow_paths: list[str], added_lines: dict[str, list[str]]
) -> tuple[bool, str]:
    """Pure decision logic, isolated from I/O so it is directly unit-testable."""
    if not TICKET_RE.match(ticket):
        return False, (
            f"REFUSED: ticket id {ticket!r} is not of the form OMN-<digits>. "
            "The dispatch path will not open a PR it cannot attribute."
        )
    if not workflow_paths:
        return True, (
            "PASS: the branch changes no file under "
            f"{WORKFLOW_PATH_PREFIX}, so the workflow-citation rule does not "
            "apply."
        )

    uncited = [
        path
        for path in workflow_paths
        if not any(ticket in line for line in added_lines.get(path, []))
    ]
    if uncited:
        return False, (
            f"REFUSED: this branch changes {len(workflow_paths)} workflow "
            f"file(s), and {len(uncited)} of them do not cite {ticket} in any "
            f"line the branch ADDS: {', '.join(uncited)}.\n"
            "Workflow files are the privilege boundary of this repository, and "
            "this dispatch path holds an App token — a route that can open a "
            "PR as a privileged identity must not become a route that quietly "
            "edits the routes. The citation has to live in the diff, not in "
            "the PR body, the commit message, or the dispatch inputs: those "
            "are all supplied by the same caller as the branch, so none of "
            "them is independent evidence.\n"
            f"Fix: write {ticket} into the workflow change itself (a comment "
            "on the changed block is enough) and re-dispatch. This is the "
            "same posture check_evidence_import_allowed.py takes one notch "
            "wider, where a workflow change is refused outright."
        )
    return True, (
        f"PASS: all {len(workflow_paths)} changed workflow file(s) cite "
        f"{ticket} in a line this branch adds."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--ticket", required=True)
    args = parser.parse_args(argv)

    try:
        workflow_paths = changed_workflow_paths(args.base_ref, args.head_ref)
        added = {
            path: added_lines_for_path(args.base_ref, args.head_ref, path)
            for path in workflow_paths
        }
    except AppPrBranchInconclusiveError as exc:
        print(f"APP-PR BRANCH GUARDRAIL INCONCLUSIVE: {exc}", file=sys.stderr)
        return 2

    allowed, message = evaluate(
        ticket=args.ticket, workflow_paths=workflow_paths, added_lines=added
    )
    print(message, file=sys.stdout if allowed else sys.stderr)
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
