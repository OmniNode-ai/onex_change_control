# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Refuse a prod-promotion grant approved by the identity that requested it.

OMN-18945. This is the ``main``-branch half of the OMN-17157 authoring-time
self-approval control, and it exists because that control was built on the
wrong branch.

WHY A SECOND COPY EXISTS, stated plainly so nobody deletes it as duplication.
``dev`` carries ``validate_prod_promotion_grants.py``, whose
``_check_self_approval`` refuses exactly this condition, and ``ci.yml`` there
passes ``--requester``. Neither reaches a grant. Prod-promotion grants are
authored by ``hotfix/*`` pull requests targeting **this** branch — the shapes
``main-target-guard.yml`` admits are ``dev`` with a promotion receipt and
``hotfix/*`` with hotfix evidence, and every recent grant PR is the second.
``main`` carries no ``validate_prod_promotion_grants.py`` at all, so the
refusal, and the behavioural tripwire built to prove the refusal had not been
removed, were both absent from the only branch on which a grant is written.
A control and its own positive control sharing one blind spot is how the gap
survived: everything reported green, on a branch no grant travels through.

This module does NOT replace anything. Three refusals of the same condition
now exist and none is redundant:

  * ``dev``'s validator compares ``approved_by`` against the pull-request
    author on the development branch, and is the one covered by the
    behavioural tripwire there;
  * this module compares ``approved_by`` against the pull-request author on
    the branch the grant is actually authored on; and
  * ``omninode_infra``'s dispatch-time gate compares ``approved_by`` against
    the DEPLOY DISPATCHER, an identity that is not knowable when the grant is
    written and that neither authoring-time check can see.

SCOPE, and what it deliberately is not. This governs the grants file only.
Schema shape is the inline heredoc validator's job and its mirror in
``tests/test_prod_promotion_grants.py``; stability-proven digest, the change
control receipt, the declared rollback target, the ``@main`` fetch, CODEOWNERS
review, absolute expiry, single-use consumption and the health-conditional
waiver are all enforced elsewhere and are untouched here.

THE CHECK IS REQUESTER-SCOPED AND DIFF-SCOPED.

  * ``--requester`` is REQUIRED, not optional. On ``dev`` it is optional and a
    behavioural tripwire fact exists solely to prove CI remembers to pass it,
    because a validator invoked without one runs the self-approval check over
    nothing and reports success. Requiring it makes that failure mode
    structurally unavailable rather than separately policed.
  * With a ``--base-file`` the check fires only on entries this change ADDS,
    identified by ``grant_id``. An unrelated pull request opened by someone
    who happens to share a login with the approver of an untouched entry is
    never refused.
  * A missing, unreadable or malformed base file fails CLOSED: every entry is
    then treated as new. An unreadable base is not evidence that an entry is
    pre-existing.

HONEST LIMIT, unchanged from every other half of this control. No file proves
a human said the words behind an ``approved_by``. This enforces blast radius —
that the approver and the requester are two different accounts — not operator
authenticity.

Usage::

    validate-prod-promotion-grant-self-approval \\
        --file grants/prod_promotion_grants.yaml --requester <login>
    validate-prod-promotion-grant-self-approval \\
        --file grants/prod_promotion_grants.yaml --requester <login> \\
        --base-file /tmp/base_grants.yaml

Exit codes:
    0: no new entry is self-approved
    1: at least one new entry is self-approved, or the grants file could not
       be read (both are refusals; neither is a silent pass)
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: The reason string ``omninode_infra``'s promotion-time gate emits for this
#: same condition (``EnumGrantOutcome.SELF_GRANTED``), and the one ``dev``'s
#: validator emits. Spelled identically in all three so the halves of one
#: control are findable with a single grep and cannot drift into differently
#: worded refusals of the same thing.
SELF_GRANTED_REASON = "self_granted"

#: Emitted when the grants file itself cannot be read or parsed. A separate
#: token from the one above: "I could not look" and "I looked and it is
#: self-approved" are different facts, and collapsing them would make an
#: unreadable anchor indistinguishable from a caught violation in the log.
UNREADABLE_GRANTS_REASON = "unreadable_grants_file"


@dataclass(frozen=True)
class ModelSelfApprovalResult:
    """Outcome of one self-approval evaluation.

    ``passed`` is carried explicitly rather than derived from ``errors`` so a
    caller never has to remember ``bool(errors)``; ``checked_entry_count`` is
    the number of entries the check actually considered NEW, which is the
    number that makes a green result meaningful. A pass over zero considered
    entries and a pass over three are different facts.
    """

    passed: bool
    errors: list[str]
    checked_entry_count: int


def load_base_grant_ids(base_file: Path | None) -> frozenset[str] | None:
    """Grant ids in the grants file AS IT EXISTS ON THE BASE REF.

    Returns ``None`` when newness cannot be established at all — no base file
    supplied, or the supplied one is missing, unreadable or malformed.
    Callers MUST read ``None`` as "treat every entry as new".
    """
    if base_file is None:
        return None
    try:
        with base_file.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    entries = data.get("entries")
    if not isinstance(entries, list):
        return None
    return frozenset(
        entry["grant_id"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("grant_id"), str)
    )


def check_self_approval(
    entries: list[Any],
    *,
    requester: str,
    base_grant_ids: frozenset[str] | None,
) -> tuple[list[str], int]:
    """Refuse entries NEW in this change whose ``approved_by`` is the requester.

    Returns the errors and the number of entries considered new. GitHub logins
    are case-insensitive, so the comparison is casefolded: without that, a
    change of capitalisation would launder a self-approval past this check
    while resolving to the same account everywhere else in GitHub.
    """
    errors: list[str] = []
    considered = 0
    requester_key = requester.casefold()
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            # Entry shape is the schema validator's job. Reporting it here too
            # would put one defect in two checks' output.
            continue
        grant_id = entry.get("grant_id")
        is_new = (
            base_grant_ids is None
            or not isinstance(grant_id, str)
            or grant_id not in base_grant_ids
        )
        if not is_new:
            continue
        considered += 1
        approved_by = entry.get("approved_by")
        if not isinstance(approved_by, str):
            continue
        if approved_by.casefold() == requester_key:
            errors.append(
                f"Entry[{idx}]: {SELF_GRANTED_REASON} — approved_by "
                f"{approved_by!r} is the identity that requested this grant "
                f"({requester!r}). A prod-promotion grant must be approved by "
                "someone other than the person requesting it. This is the "
                "same condition omninode_infra's promotion-time gate refuses "
                "as 'self_granted'; refusing it here means it is caught while "
                "the grant is being written, on the branch it is written on, "
                "instead of at promotion time."
            )
    return errors, considered


def evaluate(
    grants_file: Path,
    *,
    requester: str,
    base_file: Path | None,
) -> ModelSelfApprovalResult:
    """Read the grants file and evaluate the self-approval condition over it.

    An unreadable or malformed grants file is a REFUSAL, not a skip. This runs
    on the branch grants are authored on; "I could not parse the trust anchor"
    must never read as "the trust anchor is fine".
    """
    try:
        with grants_file.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        return ModelSelfApprovalResult(
            passed=False,
            errors=[f"{UNREADABLE_GRANTS_REASON}: cannot read {grants_file}: {exc}"],
            checked_entry_count=0,
        )
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return ModelSelfApprovalResult(
            passed=False,
            errors=[
                f"{UNREADABLE_GRANTS_REASON}: {grants_file} is not a mapping "
                "with a list under 'entries'"
            ],
            checked_entry_count=0,
        )
    errors, considered = check_self_approval(
        data["entries"],
        requester=requester,
        base_grant_ids=load_base_grant_ids(base_file),
    )
    return ModelSelfApprovalResult(
        passed=not errors, errors=errors, checked_entry_count=considered
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Refuse a prod-promotion grant entry added by this change whose "
            "approved_by is the identity requesting it (OMN-18945)."
        )
    )
    parser.add_argument(
        "--file",
        required=True,
        type=Path,
        help="the grants file as it stands on this branch",
    )
    parser.add_argument(
        "--requester",
        required=True,
        help=(
            "the identity requesting the grant — the pull-request author. "
            "REQUIRED: a run without one would check nothing and report "
            "success."
        ),
    )
    parser.add_argument(
        "--base-file",
        type=Path,
        default=None,
        help=(
            "the same grants file as it exists on the base ref, so the check "
            "fires only on entries this change adds. Omitted, missing or "
            "unreadable means every entry is treated as new (fail closed)."
        ),
    )
    args = parser.parse_args(argv)

    result = evaluate(args.file, requester=args.requester, base_file=args.base_file)
    if not result.passed:
        print(
            f"FAIL: {args.file} — {len(result.errors)} self-approval "
            f"violation(s) against requester {args.requester!r}:"
        )
        for err in result.errors:
            print(f"  - {err}")
        return 1
    print(
        f"PASS: {args.file} — no self-approved grant among "
        f"{result.checked_entry_count} entr(y/ies) new in this change, "
        f"against requester {args.requester!r}."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - console-script entry
    sys.exit(main())
