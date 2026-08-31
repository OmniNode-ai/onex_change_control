#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Advisory governance-file gate for pull requests (OMN-16117, second vector).

Why this exists
----------------
Prod-promotion grants land in ``grants/prod_promotion_grants.yaml`` on
``onex_change_control@main`` (the OMN-13418 trust anchor). Twice now,
automation has come within one command of merging a grant PR purely
because every CI check reported green. ``auto-merge.yml``'s own
governance-path exclusion (OMN-16117, ``scripts/ci/check_governance_paths.py``)
stops *this repo's own* ``auto-merge.yml`` from arming auto-merge on such a
PR, but it says nothing to a driver that reads "all checks green" as its
own signal to run ``gh pr merge`` directly, bypassing ``auto-merge.yml``
entirely -- a distinct vector from the one that fix closes.

This module (and the workflow that calls it,
``.github/workflows/governance-file-advisory-gate.yml``) closes that
*signal*, not the merge path itself: it is a standalone check that
HARD-FAILS whenever a PR's diff touches a guarded governance path, so "all
checks green" is never true for such a PR. Any automated driver gating a
merge decision on all-green will see this check red and skip the PR.

DELIBERATELY ADVISORY -- read this twice before wiring it into anything else
------------------------------------------------------------------------------
This check MUST NEVER become a required status check, directly (branch
protection / ``.github/required-checks.yaml``) or transitively (folded into
``CI Summary`` via ``STRICT_GATE_JOBS`` / ``SKIPPABLE_GATE_JOBS`` in
``scripts/ci/ci_summary_gate.py``). ``onex_change_control@main`` has
``enforce_admins: true`` -- a REQUIRED failing check there blocks merges for
administrators too, which would permanently wedge every future
prod-promotion grant landing, including emergency prod recovery. This
check's job is to break the automated all-green signal, not to block a
human decision. See omni_home ``CLAUDE.md`` rules 2a and 12 (the
prod-promotion grant gate).

No second source of truth
--------------------------
This module performs no network I/O and defines no governance-path list of
its own. It imports ``GOVERNANCE_PATHS`` and ``touches_governance_path``
directly from ``scripts/ci/check_governance_paths.py`` (OMN-16117's first
vector). Two independent governance-path lists would drift -- one updated
without the other -- which is exactly the failure mode this reuse exists to
prevent. The workflow resolves the PR's changed-file set via the GitHub
API (the same ``gh api --paginate | jq -s`` pagination-safe pattern
``auto-merge.yml`` uses) and passes it in as JSON.

Usage
-----
    # Resolved, safe:
    python3 -m scripts.ci.governance_advisory_gate check \\
        --changed-files-json '["contracts/OMN-16117.yaml"]'

    # Resolved, touches a governance file -- prints the human-readable
    # failure message and exits non-zero:
    python3 -m scripts.ci.governance_advisory_gate check \\
        --changed-files-json '["grants/prod_promotion_grants.yaml"]'

    # Undetermined (API error) -- fails closed, same as above:
    python3 -m scripts.ci.governance_advisory_gate check \\
        --changed-files-json 'null'

Exit codes
----------
    0: the changed-file set resolved and no governance file was touched.
    1: a governance file was touched, OR the changed-file set could not be
       determined (fail closed).
"""

from __future__ import annotations

import argparse
import sys

from scripts.ci.check_governance_paths import GOVERNANCE_PATHS as GOVERNANCE_PATHS
from scripts.ci.check_governance_paths import _load_changed_files
from scripts.ci.check_governance_paths import (
    touches_governance_path as touches_governance_path,
)

_EXIT_PASS = 0
_EXIT_FAIL = 1

_FAILURE_MESSAGE_TEMPLATE = """\
============================================================================
 GOVERNANCE-FILE ADVISORY GATE -- FAILED (OMN-16117)
============================================================================

This pull request touches a prod-promotion governance surface:

{touched_or_undetermined}

Guarded governance paths (from scripts/ci/check_governance_paths.py):
{governance_paths}

WHAT THIS MEANS
----------------
Prod-promotion grant PRs land ONLY by explicit human decision -- never by
automation, never by an agent, regardless of how the rest of CI reports.

A green CI run is NOT authorization to merge this PR. This check exists
precisely so that "all checks green" is never true for a PR like this one,
so that any automated driver gating a merge decision on all-green sees this
check red and skips it.

GOVERNING RULE
---------------
See omni_home CLAUDE.md rule 2a ("Prod promotion ALWAYS requires a fresh,
CODEOWNERS-approved grant (OMN-13418)") and rule 12 ("Prod promotion
requires a fresh, CODEOWNERS-approved grant (OMN-13418)"). A grant only
takes effect once it lands on onex_change_control@main via the governed
merge path on explicit operator word.

NOTE ON THIS CHECK'S OWN STATUS
--------------------------------
This check is deliberately ADVISORY, not a required status check -- it does
not block a human from merging this PR through the normal review path. It
exists only to deny automation the "all green" signal. If you are a human
who has confirmed this PR should land, this red check is not itself a
blocker; if you are automation reading this, STOP -- do not merge.
============================================================================
"""

# Not a credential -- a status string containing the word "PASS" (noqa: S105 below).
_PASS_MESSAGE = (
    "Governance-file advisory gate: PASS -- no guarded governance path "  # noqa: S105
    "touched (OMN-16117)."
)


def _format_failure_message(changed_files: list[str] | None) -> str:
    if changed_files is None:
        touched = (
            "  UNDETERMINED -- the changed-file set could not be resolved via "
            "the GitHub API (fail-closed: undetermined is treated identically "
            'to "touches a governance file").'
        )
    else:
        touched_paths = sorted(
            f for f in changed_files if f.strip().lstrip("./") in GOVERNANCE_PATHS
        )
        touched = "\n".join(f"  - {p}" for p in touched_paths) or "  (none listed)"

    governance_paths = "\n".join(f"  - {p}" for p in GOVERNANCE_PATHS)
    return _FAILURE_MESSAGE_TEMPLATE.format(
        touched_or_undetermined=touched,
        governance_paths=governance_paths,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI surface for the workflow.

    Subcommand:
        check --changed-files-json <json|null>
            Prints a pass message (exit 0) or the full human-readable
            failure message (exit 1).
    """
    parser = argparse.ArgumentParser(
        description="Advisory governance-file gate for pull requests (OMN-16117)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser(
        "check", help="Fail if the PR's diff touches a guarded governance file"
    )
    p_check.add_argument(
        "--changed-files-json",
        default=None,
        help=(
            "JSON array of changed file paths from the PR diff, or the "
            "literal 'null' (or omit this flag) when the changed-file set "
            "could not be determined -- fails closed."
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "check":
        try:
            changed_files = _load_changed_files(args.changed_files_json)
        except (ValueError, TypeError) as exc:
            sys.stdout.write(_format_failure_message(None))
            sys.stderr.write(
                f"could not parse --changed-files-json: {exc}; failing closed\n"
            )
            return _EXIT_FAIL

        if touches_governance_path(changed_files):
            sys.stdout.write(_format_failure_message(changed_files))
            return _EXIT_FAIL

        sys.stdout.write(_PASS_MESSAGE + "\n")
        return _EXIT_PASS

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
