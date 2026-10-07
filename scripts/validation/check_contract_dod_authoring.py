# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Contract DoD-authoring hygiene gate (OMN-14767 / friction F-15).

A ticket contract's ``dod_evidence`` checks are executed verbatim by the
contract-compliance runner *before the product PR merges*. Two authoring classes
produce receipts that are dishonest or unsatisfiable — and neither is caught by
the OMN-14409 substance floor, which is a **contract-level** "at least one check
reaches L1" policy. A contract with one real check plus several placeholder or
impossible rows clears the floor while still carrying the debt. This gate is the
**per-row** complement: every declared check must be a real, presently-satisfiable
assertion.

Class 1 — no-op / TODO placeholder check_value
----------------------------------------------
``scripts/auto_scaffold_contract.py`` emits ``check_value: "# TODO: verify:
<text>"`` for a Linear DoD item that has no concrete check. Handed to ``sh -c``
that is a comment — it does nothing and exits 0, so the check passes identically
whether the work is right, wrong, or absent. A check that cannot fail is not
evidence. Empty and comment-only check_values are the same fail-open class.

Class 2 — impossible pre-merge check
------------------------------------
A check that asserts a **post-merge** state — ``PR merged to main``,
``state == MERGED``, ``.merged == true``, a non-null ``mergedAt`` — can never be
true while the product PR is still open, which is exactly when the pre-merge
compliance gate runs. Such a DoD either blocks an honest merge forever or is
"satisfied" by a backfilled receipt that proves something else (the OCC#4317
class on OMN-7906).

Policy
------
Each dod_evidence check is classified (:func:`classify_check`). A contract FAILS
if any check is a placeholder or an impossible pre-merge assertion. Legacy
contracts that predate the gate are grandfathered in
``dod_authoring_legacy_allowlist.yaml`` (a ratchet — it may only shrink; a listed
contract that now passes is reported as stale and fails the gate until delisted),
so the gate is fail-closed for every NEW contract without wedging CI on
pre-existing debt. Mirrors the OMN-14409 substance-floor shape exactly (one
authoring-hygiene family, one structure).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


class Violation(str, Enum):
    """Why a single dod_evidence check is rejected."""

    PLACEHOLDER = "placeholder"
    IMPOSSIBLE_PRE_MERGE = "impossible-pre-merge"


# ---------------------------------------------------------------------------
# Class 1 — no-op / placeholder-marker detection
# ---------------------------------------------------------------------------

# A bare placeholder marker as the whole (or leading) command — the scaffold's
# ``verify:`` stub output (rendered as a shell comment) and its hand-written
# cousins. The leading ``#`` is optional in the match because an un-commented
# marker word is just as inert once it reaches ``sh -c`` (it is not a real
# command either). The marker words below are the placeholder vocabulary the
# scaffold and human authors use for an unfinished check.
_MARKER_RE = re.compile(
    r"^(?:#\s*)?(?:TODO|FIXME|TBD|XXX|PLACEHOLDER|FILL[\s_-]?IN)\b",
    re.IGNORECASE,
)


def _is_comment_only(command: str) -> bool:
    """True when every non-blank line of ``command`` is a shell comment.

    ``sh -c`` treats a comment-only script as a no-op that exits 0, so it can
    never fail — it is not evidence. This is the exact shape of the scaffold's
    ``# TODO: verify: <text>`` placeholder.
    """
    lines = [ln.strip() for ln in command.splitlines()]
    non_blank = [ln for ln in lines if ln]
    return bool(non_blank) and all(ln.startswith("#") for ln in non_blank)


def is_placeholder(check_value: str) -> bool:
    """True when ``check_value`` is a no-op / TODO placeholder (Class 1)."""
    command = (check_value or "").strip()
    if not command:
        return True
    if _is_comment_only(command):
        return True
    return bool(_MARKER_RE.match(command))


# ---------------------------------------------------------------------------
# Class 2 — impossible pre-merge (self-PR asserts merged) detection
# ---------------------------------------------------------------------------

# The SELF PR placeholder. The compliance runner substitutes ``${PR_NUMBER}`` /
# ``$PR_NUMBER`` with the PR under gate — the PR that is still OPEN when the
# pre-merge gate runs. A check asserting THIS PR is merged can never pass then, so
# it blocks the honest merge forever. A HARDCODED other PR number (a satisfiable
# "dependency PR #123 is already merged" check) is deliberately NOT matched — the
# self-PR restriction is what keeps this fail-closed and false-positive-free.
_SELF_PR_RE = re.compile(r"\$\{?PR_NUMBER\}?")

# Post-merge-state ASSERTIONS (a comparison that FAILS when the PR is not merged),
# not mere field reads. Detected by TOKEN CO-OCCURRENCE (order-independent, robust
# to shell/jq/grep piping) rather than brittle adjacency:
#   * ``\bMERGED\b`` — a distinctive uppercase token that appears only when
#     comparing a PR's state (``... = "MERGED"``); it never matches
#     ``mergeStateStatus`` / ``mergeable`` (different casing).
#   * a ``merged`` boolean field paired with a ``true`` literal (``.merged == true``,
#     ``--json merged ... | grep -q true``).
#   * a ``mergedAt`` / ``merged_at`` field paired with a NON-EMPTY / non-null
#     assertion (``-n "$(...)"``, ``!= null``, ``!= ""``, ``is not null``) — i.e.
#     "the merge timestamp is set". ``-z`` (asserts EMPTY = not-yet-merged) is
#     deliberately excluded: that is satisfiable pre-merge.
# A BARE ``--json state -q .state`` or ``--json mergedAt`` that only PRINTS the
# field (always exit 0) is NOT matched — that is a weak existence probe
# (substance-floor L0), not an impossible assertion.
_MERGED_STATE_RE = re.compile(r"\bMERGED\b")
_MERGED_FIELD_RE = re.compile(
    r"(?:--json[=\s]\S*\bmerged\b|\.merged\b|['\"]merged['\"])"
)
_TRUE_LITERAL_RE = re.compile(r"\btrue\b", re.IGNORECASE)
_MERGED_AT_RE = re.compile(r"\bmerged_?at\b", re.IGNORECASE)
_NONEMPTY_ASSERT_RE = re.compile(
    r"(?:(?<!\w)-n\b|!=\s*null|!=\s*(?:\"\"|'')|is\s+not\s+null)", re.IGNORECASE
)


def _asserts_merged(value: str) -> bool:
    """True when ``value`` asserts (not merely reads) a merged state."""
    if _MERGED_STATE_RE.search(value):
        return True
    if _MERGED_FIELD_RE.search(value) and _TRUE_LITERAL_RE.search(value):
        return True
    return bool(_MERGED_AT_RE.search(value) and _NONEMPTY_ASSERT_RE.search(value))


def is_impossible_pre_merge(check_value: str, description: str) -> bool:
    """True when the check asserts the SELF PR is already merged (Class 2).

    The pre-merge contract-compliance gate runs while ``${PR_NUMBER}`` is still
    open, so a self-PR merged assertion is unsatisfiable — it blocks the honest
    merge forever (or forces a dishonest backfill receipt, the OCC#4317 class).
    ``description`` is accepted for symmetry with :func:`classify_check` but is NOT
    matched: a prose DoD line like "PR merged to main" is the dominant *legacy*
    generator house style (~62% of the corpus reads that way) — hard-gating on the
    description text alone would grandfather the majority of contracts, the
    reject-everything trap the substance floor's kill-switch documents. That
    boilerplate class is deferred to a generator fix + migration (see
    :data:`GATE_MERGED_DESCRIPTION_BOILERPLATE`); this gate stays on the precise,
    forward-protective self-PR assertion.
    """
    _ = description  # intentionally unused — see docstring
    value = check_value or ""
    if not _SELF_PR_RE.search(value):
        return False
    return _asserts_merged(value)


# DEFERRED kill switch (OFF), mirroring the substance floor's GATE_SELF_REFERENTIAL.
# A dod_evidence *description* of "PR merged to <branch>" is a genuinely impossible
# pre-merge requirement, but it is the dominant LEGACY generator house style
# (~4,360 checks across ~4,400 contracts as of 2026-07-18). Hard-gating it would
# grandfather ~62% of the corpus — the reject-everything trap. The
# ``migrate_dod_contracts`` / ``auto_scaffold_contract`` generators must stop
# emitting the impossible boilerplate row first; then this can flip on and the
# gate covers the description class with no further code change.
GATE_MERGED_DESCRIPTION_BOILERPLATE = False


def classify_check(check_value: str, description: str) -> Violation | None:
    """Return the :class:`Violation` for a check, or None when it is acceptable."""
    if is_placeholder(check_value):
        return Violation.PLACEHOLDER
    if is_impossible_pre_merge(check_value, description):
        return Violation.IMPOSSIBLE_PRE_MERGE
    return None


# ---------------------------------------------------------------------------
# Contract evaluation + corpus sweep (mirrors check_contract_substance_floor)
# ---------------------------------------------------------------------------


@dataclass
class CheckFinding:
    """One rejected dod_evidence check."""

    item_id: str
    check_value: str
    violation: Violation


@dataclass
class ContractResult:
    """DoD-authoring verdict for a single contract."""

    path: Path
    ticket_id: str
    findings: list[CheckFinding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.findings

    def failure_message(self) -> str:
        rows = "\n".join(
            f"      - [{f.violation.value}] {f.item_id}: {f.check_value[:100]!r}"
            for f in self.findings
        )
        return (
            f"{self.path}: DoD AUTHORING FAILED — {self.ticket_id} declares "
            f"{len(self.findings)} dishonest/unsatisfiable dod_evidence check(s):\n"
            f"    Offending checks:\n{rows}\n"
            f"    Fix: replace each placeholder (`# TODO: verify: ...`) with a real "
            f"check that could fail if the work were wrong, and remove any "
            f"post-merge assertion (`PR merged to main`, `state == MERGED`, "
            f"`.merged == true`) — the compliance gate runs BEFORE the PR merges."
        )


def evaluate_contract(path: Path) -> ContractResult:
    """Evaluate one contract YAML against the DoD-authoring policy."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result = ContractResult(path=path, ticket_id=str(raw.get("ticket_id") or path.stem))
    for item in raw.get("dod_evidence") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "<unnamed>")
        description = str(item.get("description") or "")
        for check in item.get("checks") or []:
            if not isinstance(check, dict):
                continue
            check_value = str(check.get("check_value") or "")
            violation = classify_check(check_value, description)
            if violation is not None:
                result.findings.append(
                    CheckFinding(
                        item_id=item_id,
                        check_value=check_value,
                        violation=violation,
                    )
                )
    return result


_ALLOWLIST_PATH = Path(__file__).parent / "dod_authoring_legacy_allowlist.yaml"


def load_legacy_allowlist(path: Path = _ALLOWLIST_PATH) -> set[str]:
    """Return the grandfathered ticket ids (OMN-14767 backfill).

    These contracts predate the gate and carry placeholder/impossible rows. They
    are exempt so the gate does not wedge CI on pre-existing debt — it stays
    fail-closed for every NEW contract. The list is a ratchet: it may only shrink
    (see :func:`main`).
    """
    if not path.exists():
        return set()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(t) for t in (raw.get("legacy_contracts") or [])}


@dataclass
class SweepReport:
    """Partitioned verdicts over a set of contracts."""

    failures: list[ContractResult] = field(default_factory=list)
    grandfathered: list[str] = field(default_factory=list)
    stale_allowlist: list[str] = field(default_factory=list)


def sweep(paths: list[Path], allowlist: set[str]) -> SweepReport:
    """Partition ``paths`` into new failures, grandfathered legacy, and stale
    allowlist entries."""
    report = SweepReport()
    for path in paths:
        if not path.exists():
            continue
        result = evaluate_contract(path)
        listed = result.ticket_id in allowlist
        if result.passed:
            if listed:
                report.stale_allowlist.append(result.ticket_id)
        elif listed:
            report.grandfathered.append(result.ticket_id)
        else:
            report.failures.append(result)
    return report


def _resolve_paths(args: argparse.Namespace) -> list[Path]:
    if args.all or not args.paths:
        return sorted(Path("contracts").glob("*.yaml"))
    # CI feeds the changed-file list, which can include non-contract paths.
    return [
        p for p in args.paths if p.suffix == ".yaml" and p.parent.name == "contracts"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Contract YAML files to check. Defaults to all of contracts/.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check the entire contracts/ corpus (used by the CI sweep).",
    )
    args = parser.parse_args(argv)

    paths = _resolve_paths(args)
    report = sweep(paths, load_legacy_allowlist())

    if report.grandfathered:
        print(
            f"dod-authoring: {len(report.grandfathered)} grandfathered legacy "
            f"contract(s) skipped (OMN-14767 backfill)"
        )

    exit_code = 0

    if report.failures:
        print(
            f"\n{len(report.failures)} contract(s) failed the DoD-authoring gate "
            f"(OMN-14767 / F-15):\n"
        )
        for result in report.failures:
            print(f"  {result.failure_message()}\n")
        exit_code = 1

    if report.stale_allowlist:
        listed = ", ".join(sorted(report.stale_allowlist))
        print(
            f"\n{len(report.stale_allowlist)} contract(s) now PASS the DoD-authoring "
            f"gate but are still grandfathered. The allowlist is a ratchet — it may "
            f"only shrink.\n    Remove from "
            f"scripts/validation/dod_authoring_legacy_allowlist.yaml: {listed}\n"
        )
        exit_code = 1

    if exit_code == 0:
        print(f"dod-authoring OK: {len(paths)} contract(s) checked")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
