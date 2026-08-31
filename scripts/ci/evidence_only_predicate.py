# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Exact-allowlist predicate: is this PR's diff evidence-only?

OMN-16285. OCC absorbs an evidence-companion PR (append-only YAML under
``contracts/`` + ``drift/dod_receipts/``) for nearly every product PR in the
org, and every one of them currently runs the FULL CI suite regardless of
diff shape. This predicate answers one narrow question -- does the diff
touch ONLY the evidence surfaces a companion PR is allowed to carry -- so
``ci.yml`` can skip the heavy jobs that validate CODE (not evidence content)
when the answer is yes. Jobs that validate evidence content itself (Receipt
Honesty, Append-Only, Contract Shape/Compliance, the corpus-wide ratchets,
...) are NEVER gated on this predicate -- they stay unconditional because an
evidence-only diff is exactly the diff shape they exist to check.

Exact allowlist, not a substring heuristic (hard constraint, OMN-16285): a
diff touching ``scripts/`` or ``.github/`` is NEVER evidence-only, even if it
ALSO touches ``contracts/``. Derived from a live audit of 5 recently merged
OCC companion PRs (#6685, #6735, #6676, #6757, #6755, 2026-08-20 -- see the
OMN-16285 PR body): every file touched matched exactly one of the two
patterns below -- ``contracts/*.yaml`` (or the ``contracts/v1/*.yaml`` shape
``check-contract-shape-v1`` already recognizes, OMN-15669) and
``drift/dod_receipts/**/*.yaml`` (including ``*.supersede.<N>.yaml``
correction files, still under the same tree).

An empty or unresolved changed-file list is NOT evidence-only (fail closed):
a diff this predicate cannot prove is narrow must never short-circuit the
full suite. The caller (ci.yml's ``evidence-only-predicate`` job) is itself
unconditional and registered CLASSIFICATION_ONLY (not soft-allowlisted) in
``ci_summary_gate.py`` -- if the job that computes this predicate fails
outright (e.g. the git-diff step), every job gated on its output cascades to
``skipped``, and that cascade is caught by the default-deny sweep, not
tolerated as a pass. See that module's module docstring for the mechanism
(the same one already proven for ``zone-filter`` / ``docs_only``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Exact allowlist. `.ya?ml` covers both `.yaml` and `.yml`; no other
# extension or directory qualifies, and every pattern is anchored end-to-end
# (`^...$`) so a partial/substring match can never pass.
_EVIDENCE_ONLY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # contracts/OMN-XXXX.yaml, and the contracts/v1/OMN-XXXX.yaml shape
    # OMN-15669's check-contract-shape-v1 already recognizes -- no other
    # subdirectory or nesting depth is evidence.
    re.compile(r"^contracts/(v1/)?[^/]+\.ya?ml$"),
    # drift/dod_receipts/<TICKET>/<ITEM_ID>/<run_timestamp>.yaml (the
    # canonical receipt location, docs/RECEIPT_LOCATIONS.md) at any depth,
    # including *.supersede.<N>.yaml correction files.
    re.compile(r"^drift/dod_receipts/.+\.ya?ml$"),
)


def is_evidence_path(path: str) -> bool:
    """True iff ``path`` matches the exact evidence-surface allowlist."""

    return any(pattern.match(path) for pattern in _EVIDENCE_ONLY_PATTERNS)


def is_evidence_only_diff(changed_files: list[str]) -> bool:
    """True iff every changed file is an evidence path AND the diff is non-empty.

    Fail-closed on the boundary case: zero changed files (an unresolved diff,
    or a genuinely empty diff on an unsupported event) is NOT evidence-only
    -- the caller must run the full suite whenever it cannot prove narrowing
    is safe.
    """

    if not changed_files:
        return False
    return all(is_evidence_path(f) for f in changed_files)


def parse_changed_files(raw: str) -> list[str]:
    """Split a newline-delimited changed-file blob, dropping blank lines."""

    return [line.strip() for line in raw.splitlines() if line.strip()]


def main() -> int:
    changed_files = parse_changed_files(os.environ.get("CHANGED_FILES", ""))
    evidence_only = is_evidence_only_diff(changed_files)
    value = "true" if evidence_only else "false"

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as fh:
            fh.write(f"evidence_only={value}\n")

    print(f"evidence_only={value}")
    print(f"changed files ({len(changed_files)}):")
    for f in changed_files:
        marker = "evidence" if is_evidence_path(f) else "NON-EVIDENCE"
        print(f"  [{marker}] {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
