#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""
Evidence-import guardrail (OMN-16429).

The `evidence-import.yml` workflow_dispatch workflow lets a maintainer pull a
named branch/ref of evidence-only commits into `onex_change_control` on
behalf of a contributor who has no push access to this repo (the friction
this ticket fixes: OCC evidence hand-carried onto org branches). It grants no
standing write access to anyone — each run is a one-shot, maintainer-fired,
audited import that still lands as a normal PR through the normal CI gates.

This module is the safety boundary on *what* an import may bring in. It is
deliberately conservative and fails closed: two file-path shapes are always
refused, regardless of who is running the workflow or what the commit
messages say.

1. Any changed path under `.github/workflows/` — an "evidence" import must
   never be able to smuggle a workflow-file edit. Workflow files are the
   actual privilege boundary of this repo; nothing that name should ever
   ride in on a branch a non-maintainer produced.
2. Any changed path under `contracts/` or `drift/dod_receipts/` that already
   exists on the target base branch — i.e. a modification or deletion of a
   pre-existing OCC contract/receipt file, not a net-new one. This mirrors
   the OCC Append-Only Gate enforced elsewhere in this repo (see
   `docs/standards/receipt_hashing_and_supersession.md`): receipts are
   append-only, and an import path is not an exception to that rule. A
   *new* receipt file is fine; touching one that is already merged is not.

Both checks operate on file paths and statuses only (from `git diff
--name-status`), never on content — they do not need to parse YAML or know
anything about receipt semantics to be correct, which keeps them auditable
and hard to route around with a clever diff.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass

WORKFLOW_PATH_PREFIX = ".github/workflows/"
RECEIPT_PATH_PREFIXES = ("contracts/", "drift/dod_receipts/")

# git diff --name-status status letters that mean "this path already existed
# and this change touches its content or removes it" — the shapes forbidden
# for pre-existing receipt paths. "A" (added) is always allowed since that is
# exactly what a net-new evidence import is.
MUTATING_STATUSES = frozenset({"M", "D", "R", "C", "T"})


@dataclass(frozen=True)
class ChangedFile:
    """One row of `git diff --name-status` output.

    `status` is the raw single-letter (or `R100`-style rename) status code;
    `path` is the post-change path (for renames, the destination path — the
    one that matters for whether the *result* lands under a guarded prefix).
    """

    status: str
    path: str


# A rename/copy `git diff --name-status` row is `R100\told\tnew` (or `C100\t...`)
# — three tab-separated fields, versus two for every other status letter.
_RENAME_OR_COPY_FIELD_COUNT = 3


def parse_name_status(raw: str) -> list[ChangedFile]:
    """Parse `git diff --name-status -z`-free (newline-delimited) output.

    Handles plain statuses (`A`, `M`, `D`) and rename/copy statuses
    (`R100\told\tnew`, `C100\told\tnew`), taking the destination path for
    the latter since that is the path that will actually exist post-import.
    """
    changed: list[ChangedFile] = []
    for raw_line in raw.splitlines():
        stripped = raw_line.rstrip("\n")
        if not stripped.strip():
            continue
        fields = stripped.split("\t")
        status_letter = fields[0][0]
        if status_letter in ("R", "C") and len(fields) >= _RENAME_OR_COPY_FIELD_COUNT:
            path = fields[2]
        else:
            path = fields[-1]
        changed.append(ChangedFile(status=status_letter, path=path))
    return changed


def evaluate_import(changed: list[ChangedFile]) -> list[str]:
    """Return a list of refusal reasons for `changed`; empty means allowed.

    Pure function, no git/filesystem access — the caller is responsible for
    producing `changed` from a real `git diff --name-status` between the
    import branch and the base branch it will PR against.
    """
    violations: list[str] = []
    for cf in changed:
        if cf.path.startswith(WORKFLOW_PATH_PREFIX):
            violations.append(
                f"refused: import touches a workflow file ({cf.path}) — "
                "evidence imports may never modify .github/workflows/"
            )
            continue
        if cf.path.startswith(RECEIPT_PATH_PREFIXES) and cf.status in MUTATING_STATUSES:
            violations.append(
                f"refused: import {cf.status}-touches a pre-existing receipt/contract "
                f"path ({cf.path}) — OCC receipts are append-only; only net-new "
                "files under contracts/ or drift/dod_receipts/ are importable"
            )
    return violations


def _git_diff_name_status(base_ref: str, head_ref: str) -> str:
    # Fixed argv, no shell, no untrusted interpolation into the command name
    # itself — same shape src/onex_change_control/scanners/** is per-file-
    # ignored for in this repo's ruff config (subprocess-calls-git pattern).
    result = subprocess.run(  # noqa: S603
        ["git", "diff", "--name-status", f"{base_ref}...{head_ref}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        required=True,
        help="Base ref the import will be PR'd against (e.g. origin/dev).",
    )
    parser.add_argument(
        "--head-ref",
        required=True,
        help="Ref carrying the imported evidence commits (e.g. local import branch).",
    )
    args = parser.parse_args(argv)

    try:
        raw = _git_diff_name_status(args.base_ref, args.head_ref)
    except subprocess.CalledProcessError as exc:
        print(f"error: git diff failed: {exc.stderr}", file=sys.stderr)
        return 2

    changed = parse_name_status(raw)
    if not changed:
        print(
            "error: import contains zero changed files — nothing to import",
            file=sys.stderr,
        )
        return 2

    violations = evaluate_import(changed)
    if violations:
        for v in violations:
            print(f"::error::{v}", file=sys.stderr)
        return 1

    print(f"ok: {len(changed)} changed file(s), no guardrail violations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
