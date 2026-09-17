# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18641: refuse a DoD check that expires by construction.

What this gate exists to stop
-----------------------------
A check that fetches an artifact at a **pinned commit sha** and then asserts
that artifact is recent **relative to now** is true on the day it is authored
and false forever afterwards. The sha cannot change, so the artifact cannot get
newer, so the predicate can only rot. No action anywhere restores it.

This is not a hypothetical shape. ``contracts/OMN-13034.yaml`` carried three
such pairs, and on 2026-09-17 the newest crossed its seven-day horizon and took
`Contract Compliance Check` to ``2/17 PASS, 13 WARN, 2 BLOCK``. Every OCC
companion bound to that ticket blocked, which killed ``omnibase_infra#3726``
and would have killed every successor of it.

The contract's own prose shows the shape was understood and accepted:

    The 2846-pinned probe now reads an 18-day-old snapshot and BLOCKS by
    construction -- each refresh of the census must re-point this probe at its
    own head, which is what this item does.

That is a treadmill, not a fix: every refresh hand-authors a fresh pair which
begins expiring immediately. This gate ends the treadmill by refusing the shape
at authoring time, where a human is present to be told, rather than a week
later on somebody else's unrelated PR.

Why pinning is not the problem
------------------------------
Pinning is correct and this gate never discourages it — a pinned ref is what
makes evidence reproducible. The defect is only the **combination** with a
now-relative predicate. The repair is to keep the pin and make the assertion
about the artifact itself: its exact ``emitted_at``, or its age measured
against the pinned commit's own date. Both are immutable, so both stay true.

Two shapes are therefore explicitly allowed, and the tests keep them allowed:

* a pinned ref with an **exact-value** assertion (no clock at all);
* a now-relative assertion against an **unpinned** ref, which reads live state
  and is supposed to track the calendar.

Exit codes: ``0`` clean, ``1`` at least one expiring check.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_REPO = Path(__file__).resolve().parents[2]
_CONTRACTS = _REPO / "contracts"

# A 40-hex object name bound to a ref parameter. This is the "frozen artifact"
# half: whatever comes back cannot change between runs.
PINNED_REF = re.compile(r"[?&]ref=([0-9a-f]{40})\b")

# The "relative to now" half. Each entry is a way of reading the wall clock, or
# of handing the decision to something that reads it. `--max-age-days` belongs
# here because the product checker it drives resolves `now` internally, so the
# invocation is clock-dependent even though no clock appears in the command.
NOW_RELATIVE: tuple[tuple[str, str], ...] = (
    (r"\$\(\s*date\b", "shells out to `date`"),
    (r"\bdate\s+-u\s+\+%s", "reads the wall clock as epoch seconds"),
    (r"--max-age-days\b", "drives a checker that resolves `now` internally"),
    (r"\bdatetime\.now\(", "calls datetime.now()"),
    (r"\bdatetime\.utcnow\(", "calls datetime.utcnow()"),
    (r"\btime\.time\(", "calls time.time()"),
    (r"\bnow\s*\|\s*tonumber\b", "compares against a jq-injected now"),
)


# The six entries that already carry the shape, recorded as DEBT rather than
# silently tolerated. This list may only SHRINK -- anything not on it fails.
#
# WHY THEY ARE NOT REPAIRED IN THE SAME CHANGE. A DoD receipt is hash-bound to
# the contract entry it attests (`contract_entry_sha256`), so editing these
# entries invalidates ten receipts, and
# `check_contract_change_rebinds_receipts.py` correctly refuses that: a receipt
# whose expected hash is edited to match a changed check now attests to a check
# that never ran, which is the false-attestation failure this whole evidence
# plane exists to prevent. The documented repair is SUPERSESSION -- mint new
# receipts for the changed entries -- which is its own change in an append-only
# corpus, tracked on OMN-18641.
#
# Being on this list is not permission. It is a countable obligation, and the
# gate below still refuses every occurrence that is not on it, which is what
# stops the treadmill: each census refresh used to hand-author a fresh expiring
# pair, and a seventh one now fails at authoring time.
KNOWN_DEBT: frozenset[tuple[str, str]] = frozenset(
    {
        ("OMN-13034.yaml", "occ-census-freshness-live-2691"),
        ("OMN-13034.yaml", "occ-census-local-verify-2691"),
        ("OMN-13034.yaml", "occ-census-freshness-live-2846"),
        ("OMN-13034.yaml", "occ-census-local-verify-2846"),
        ("OMN-13034.yaml", "occ-census-freshness-live-3394"),
        ("OMN-13034.yaml", "occ-census-local-verify-3394"),
    }
)


@dataclass(frozen=True)
class Finding:
    contract: str
    check_id: str
    sha: str
    reason: str

    def render(self) -> str:
        return (
            f"  {self.contract} :: {self.check_id}\n"
            f"      pins ref={self.sha}\n"
            f"      and {self.reason}\n"
            f"      -> the artifact is frozen and the predicate is not, so this "
            f"check expires by construction.\n"
            f"      Repair: assert the artifact's exact value at that sha, or "
            f"measure its age against the PINNED COMMIT's own date."
        )


def now_relative_reason(text: str) -> str | None:
    """Why ``text`` depends on the wall clock, or ``None`` if it does not."""
    for pattern, reason in NOW_RELATIVE:
        if re.search(pattern, text):
            return reason
    return None


def scan_check_value(contract: str, check_id: str, value: str) -> list[Finding]:
    """A check is expiring iff it pins a ref AND reads the clock."""
    pins = PINNED_REF.findall(value)
    if not pins:
        return []
    reason = now_relative_reason(value)
    if reason is None:
        return []
    return [Finding(contract, check_id, pins[0], reason)]


def _iter_checks(doc: Any) -> list[tuple[str, str]]:
    """Yield ``(check_id, check_value)`` for every command check in a contract."""
    out: list[tuple[str, str]] = []
    if not isinstance(doc, dict):
        return out
    for item in doc.get("dod_evidence") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", "<unnamed>"))
        for check in item.get("checks") or []:
            if not isinstance(check, dict):
                continue
            value = check.get("check_value")
            if isinstance(value, str):
                out.append((item_id, value))
    return out


def scan_contract(path: Path) -> list[Finding]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        # Fail loud: a contract this gate cannot read has not passed it.
        print(f"::error::cannot read {path}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    findings: list[Finding] = []
    for check_id, value in _iter_checks(doc):
        findings.extend(scan_check_value(path.name, check_id, value))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="contracts to scan (default: every contracts/*.yaml)",
    )
    args = parser.parse_args(argv)

    paths = args.paths or sorted(_CONTRACTS.glob("*.yaml"))
    if not paths:
        print(
            "::error::no contracts matched; a gate that scanned nothing has not "
            "passed, it has not run.",
            file=sys.stderr,
        )
        return 1

    all_findings: list[Finding] = []
    for path in paths:
        all_findings.extend(scan_contract(path))

    debt = [f for f in all_findings if (f.contract, f.check_id) in KNOWN_DEBT]
    new_findings = [
        f for f in all_findings if (f.contract, f.check_id) not in KNOWN_DEBT
    ]

    # A debt entry that has been repaired must leave the list. Reporting it as
    # outstanding forever is how a shrink-only list stops shrinking.
    repaired = KNOWN_DEBT - {(f.contract, f.check_id) for f in all_findings}
    if repaired:
        print(
            "::error title=STALE-DEBT-BASELINE::these entries no longer carry the "
            "shape and must be removed from KNOWN_DEBT (the list is shrink-only): "
            + ", ".join(f"{c}::{i}" for c, i in sorted(repaired)),
            file=sys.stderr,
        )
        return 1

    if new_findings:
        print(
            f"::error title=EXPIRING-DOD-CHECK::{len(new_findings)} check(s) expire "
            f"by construction (OMN-18641):",
            file=sys.stderr,
        )
        for finding in new_findings:
            print(finding.render(), file=sys.stderr)
        return 1

    print(
        f"OK: {len(paths)} contract(s) scanned, no NEW expiring DoD checks.\n"
        f"    {len(debt)} recorded on the shrink-only debt baseline (OMN-18641): "
        f"repair needs a receipt supersession pass, not an entry edit."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
