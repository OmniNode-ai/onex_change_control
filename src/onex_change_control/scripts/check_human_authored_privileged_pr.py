# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Refuse a HUMAN-authored PR that touches privileged paths in this repo (OMN-18327).

WHY THIS EXISTS
---------------
Change-control PRs in this repository are supposed to be mechanical. On
2026-09-13 one was not, and the whole fleet stopped: OCC#9362 restored the
prod-promotion grants validator's self-approval refusal, went green on every
required check, and could not merge. `grants/prod_promotion_grants.yaml` is
owned by `@OmniNode-ai/platform-leads`, `dev` required a code-owner review,
and every lane in this fleet commits under ONE shared human account — so the
operator was the author of record and GitHub blocks self-approval of your own
PR. The other lead was away. Every product PR whose OCC companion had to
merge first queued behind it.

Two things came out of that. The review requirement was removed from `dev`
(operator ruling, in-session, 2026-09-13, firm, recorded verbatim at
`docs/tracking/ROLLING_WORK_LEDGER.md:7452`, read with the standing
2026-08-28 rule that no human review is a required check until there are
full-time employees). And the operator named the actual fix:

    "That's why we have OCC writer bot."

The companion publisher and the release sync already open their PRs as the
`onexbot-occ-writer` App. A validator or workflow change did not, purely
because no sanctioned path existed for it — so it arrived under the shared
human account and inherited the un-approvable shape. THIS module is the
mechanical half of closing that: a lane-authored change to this repo's
privileged surfaces should arrive as the App, through
`.github/workflows/open-pr-as-writer-app.yml`, and this check makes a
human-authored one visible instead of silent.

WHAT IT REFUSES
---------------
A PR fails when BOTH hold:

  * its author is a human account (`user.type != "Bot"`), and
  * its diff touches any of `src/`, `scripts/`, or `.github/`.

Everything else passes, including every case that matters day to day:

  * App- and bot-authored PRs — the ~70/day companions, the autobind, the
    nightly promotion, and anything opened through the writer-App dispatch
    path — pass on authorship alone, without reading the diff;
  * a human PR touching only `contracts/`, `drift/`, `grants/`, `docs/`,
    evidence or receipts — the ordinary hand-authored evidence correction;
  * a human PR that carries the escape line described below.

THE ESCAPE, AND WHY ITS LITERAL IS SPELLED THE WAY IT IS
--------------------------------------------------------
A human hotfix is a real need — the App path runs in CI, and CI is exactly
what a person is repairing when they most need to push. So a human-authored
privileged PR passes when its body carries, on a line of its own, the
human-author escape annotation naming a ticket.

Choosing that literal is not cosmetic. omni_home CLAUDE.md rule 15 records
three separate incidents in one window where a gate parsed PR-body text by
substring and prose that merely MENTIONED a trigger fired it: the OCC
evidence-source stamp spelled inside explanatory prose suppressed a PR's own
companion publish while the gate reported SUCCESS; the sentence "do not merge
until CI is confirmed green" tripped the merge-hold vocabulary and silently
no-opped an OCC mint on two repos; and spelling the skip-token prefixes
inside a sentence asserting they were ABSENT failed the Receipt Gate on
OCC#7213 — a gate firing on documentation about the gate.

The literal here therefore avoids every one of those families. It contains no
bracketed skip-token prefix, none of the merge-hold vocabulary, and no part
of the evidence-source stamp. It deliberately reuses the shape this
repository already established for a narrow, auditable, in-body waiver —
the same `# <name>-ok: <reason>` form as the raw-prod-bypass annotation — so
a reader who has seen one recognises the other.

It must be a WHOLE LINE. A substring match is what rule 15 is about: prose
that mentions the annotation while arguing it is absent must not satisfy it.

HONEST LIMITS
-------------
- **This is advisory when it lands**, by deliberate choice and in its own
  workflow file, never in `ci.yml` and never in the `CI Summary` rollup. A
  gate that goes required the same hour it is written, on a repo whose `main`
  carries `enforce_admins: true`, is how a repository wedges itself — which
  is the failure this whole ticket exists to undo. It becomes required only
  after at least one App-authored PR has merged through the dispatch path,
  proving the sanctioned route works end to end.
- **It cannot prove authorship intent.** `user.type` is what GitHub reports;
  it says who opened the PR, not who wrote the diff. A lane that opens a PR
  through the App path is still a lane. What this removes is the SILENT case
  — a privileged change arriving under the shared human account with nothing
  naming it — not the possibility of one.
- **It reads paths, never content.** `src/`, `scripts/` and `.github/` are
  prefix matches on `git diff --name-only`, so it needs no YAML parsing and
  no knowledge of what a given file does. That is what keeps it auditable and
  hard to route around with a clever diff.

Usage:
    uv run check-human-authored-privileged-pr \\
        --pr-author-type Bot --changed-files-from - < files.txt
    uv run check-human-authored-privileged-pr \\
        --pr 9404 --repo OmniNode-ai/onex_change_control

Exit codes:
    0: allowed — bot/App author, no privileged path, or a valid escape line
    1: refused — human author + privileged path, with no escape line
    2: inconclusive — a required fact could not be read. Fails closed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Final

#: Path prefixes whose change makes a PR "privileged". `.github/` is included
#: whole rather than only `.github/workflows/`: an action, a CODEOWNERS file
#: and a required-checks manifest are each as load-bearing as a workflow.
PRIVILEGED_PATH_PREFIXES: Final[tuple[str, ...]] = (
    "src/",
    "scripts/",
    ".github/",
)

#: How many privileged paths the refusal message names before it elides.
MAX_NAMED_PATHS: Final[int] = 10

#: The in-body escape, matched as a WHOLE LINE (leading/trailing whitespace
#: tolerated) with a ticket id. Built from parts so this module's own source
#: does not contain the assembled literal on one line — a file that spells its
#: own trigger is the rule-15 defect, and this module is scanned by the same
#: tooling as everything else in the repo.
_ESCAPE_NAME: Final[str] = "human-author" + "-ok"
ESCAPE_LINE_RE: Final[re.Pattern[str]] = re.compile(
    rf"^[ \t]*#[ \t]*{re.escape(_ESCAPE_NAME)}:[ \t]*(OMN-\d+)[ \t]*$",
    re.MULTILINE,
)


class PrivilegedPrCheckInconclusiveError(RuntimeError):
    """A fact needed for the verdict could not be read. Never a silent pass."""


def is_bot_author(author_type: str) -> bool:
    """True when GitHub reports the PR's author as a Bot (App) identity.

    Compared case-insensitively: the REST payload says ``Bot``/``User``, but
    an author_type threaded through a shell variable is easy to lower-case by
    accident, and getting this wrong in the strict direction would refuse
    every companion in the repo.
    """
    return author_type.strip().casefold() == "bot"


def privileged_paths(changed_files: list[str]) -> list[str]:
    """Return the changed paths that fall under a privileged prefix."""
    return [
        path
        for path in changed_files
        if any(path.startswith(prefix) for prefix in PRIVILEGED_PATH_PREFIXES)
    ]


def escape_ticket(body: str) -> str | None:
    """Return the ticket named by a whole-line escape annotation, or None.

    Whole-line by construction. Prose that merely mentions the annotation
    mid-sentence does not match, which is the rule-15 property this check has
    to have: a sentence asserting the escape is absent must not supply it.
    """
    match = ESCAPE_LINE_RE.search(body)
    return match.group(1) if match else None


def evaluate(
    *, author_type: str, changed_files: list[str], body: str
) -> tuple[bool, str]:
    """Pure decision logic, isolated from I/O so it is directly unit-testable.

    Returns (allowed, message).
    """
    if is_bot_author(author_type):
        return True, (
            "PASS: the PR is authored by a Bot/App identity, which is the "
            "sanctioned shape for a change-control PR in this repo. The diff "
            "was not read — authorship alone settles it."
        )

    touched = privileged_paths(changed_files)
    if not touched:
        return True, (
            "PASS: human-authored, but the diff touches no privileged path "
            f"({', '.join(PRIVILEGED_PATH_PREFIXES)}). Hand-authored evidence, "
            "contract and receipt changes are unaffected by this check."
        )

    ticket = escape_ticket(body)
    if ticket is not None:
        return True, (
            f"PASS (escape): human-authored and privileged, waived by the "
            f"human-author escape line naming {ticket}. Recorded rather than "
            "refused — a person repairing CI cannot be told to use CI."
        )

    return False, (
        "REFUSED: this PR is authored by a human account and its diff touches "
        f"{len(touched)} privileged path(s): "
        f"{', '.join(sorted(touched)[:MAX_NAMED_PATHS])}"
        f"{' and more' if len(touched) > MAX_NAMED_PATHS else ''}.\n"
        "Change-control PRs on privileged surfaces are opened AS the "
        "onexbot-occ-writer App, not under the shared human account — every "
        "lane here commits as one person, so a human-authored PR on an "
        "owned path is un-approvable by construction (GitHub blocks "
        "self-approval), which is what froze the fleet behind OCC#9362 on "
        "2026-09-13.\n"
        "Push your branch, then dispatch "
        ".github/workflows/open-pr-as-writer-app.yml with that branch and "
        "your ticket id; it opens the PR as the App.\n"
        "For a genuine human hotfix, add the human-author escape annotation "
        "on a line of its own in the PR body, naming the ticket "
        "(see this module's docstring for the exact form — it is deliberately "
        "not spelled in prose that a body-parsing gate might read)."
    )


def _gh_json(args: list[str]) -> object:
    result = subprocess.run(  # noqa: S603  Why: fixed argv, no shell.
        ["gh", *args],  # noqa: S607  Why: `gh` from PATH, repo convention.
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        msg = f"gh {' '.join(args)} failed: {result.stderr.strip()}"
        raise PrivilegedPrCheckInconclusiveError(msg)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        msg = f"gh {' '.join(args)} returned unparseable JSON: {exc}"
        raise PrivilegedPrCheckInconclusiveError(msg) from exc


def read_pr_facts(pr: str, repo: str) -> tuple[str, list[str], str]:
    """Read (author_type, changed_files, body) for a PR from the GitHub API."""
    payload = _gh_json(["api", f"repos/{repo}/pulls/{pr}"])
    if not isinstance(payload, dict):
        msg = f"unexpected PR payload shape for {repo}#{pr}"
        raise PrivilegedPrCheckInconclusiveError(msg)
    user = payload.get("user")
    author_type = user.get("type", "") if isinstance(user, dict) else ""
    body = payload.get("body") or ""

    # `--paginate` so a diff spilling past one page cannot silently read as a
    # PR that touches nothing privileged. `--jq` emits newline-delimited
    # strings rather than one JSON document, so this call is read as text.
    files_result = subprocess.run(  # noqa: S603  Why: fixed argv, no shell.
        [  # noqa: S607  Why: `gh` from PATH, repo convention.
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/pulls/{pr}/files",
            "--jq",
            ".[].filename",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if files_result.returncode != 0:
        detail = files_result.stderr.strip()
        msg = f"could not read changed files for {repo}#{pr}: {detail}"
        raise PrivilegedPrCheckInconclusiveError(msg)
    changed = [
        line.strip() for line in files_result.stdout.splitlines() if line.strip()
    ]
    return str(author_type), changed, str(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", help="PR number to read from the GitHub API.")
    parser.add_argument(
        "--repo",
        default="OmniNode-ai/onex_change_control",
        help="owner/repo the PR belongs to.",
    )
    parser.add_argument(
        "--pr-author-type",
        help="Bot or User — supply with --changed-files-from to skip the API.",
    )
    parser.add_argument(
        "--changed-files-from",
        help="File of newline-separated changed paths, or '-' for stdin.",
    )
    parser.add_argument(
        "--body-from",
        help="File carrying the PR body, or '-' for stdin. Empty when omitted.",
    )
    args = parser.parse_args(argv)

    try:
        if args.pr is not None:
            author_type, changed, body = read_pr_facts(args.pr, args.repo)
        else:
            if args.pr_author_type is None or args.changed_files_from is None:
                parser.error(
                    "supply --pr, or both --pr-author-type and --changed-files-from"
                )
            author_type = args.pr_author_type
            raw_files = (
                sys.stdin.read()
                if args.changed_files_from == "-"
                else open(args.changed_files_from, encoding="utf-8").read()  # noqa: SIM115, PTH123
            )
            changed = [line.strip() for line in raw_files.splitlines() if line.strip()]
            if args.body_from == "-":
                body = sys.stdin.read()
            elif args.body_from:
                with open(args.body_from, encoding="utf-8") as fh:  # noqa: PTH123
                    body = fh.read()
            else:
                body = ""
    except (PrivilegedPrCheckInconclusiveError, OSError) as exc:
        print(f"PRIVILEGED-PR CHECK INCONCLUSIVE: {exc}", file=sys.stderr)
        return 2

    print(f"author type: {author_type or '(unreadable)'}")
    print(f"changed paths: {len(changed)}")
    allowed, message = evaluate(
        author_type=author_type, changed_files=changed, body=body
    )
    print(message, file=sys.stdout if allowed else sys.stderr)
    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
