#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regenerate the OMN-14436 DoD-runner grandfather ratchet.

The allowlist freezes the contract corpus that was authored while
``run_contract_compliance_check.py`` executed check_values against the
onex_change_control clone rather than the product checkout. Those contracts
could only ever reach the receipt store, so they are reported but not enforced.

This script exists so the artifact is reproducible and diffable, NOT so it can
be re-run to absorb new debt. The list is a ratchet: it may only shrink.
Re-running it after new contracts land would silently grandfather them and
defeat the gate -- ``tests/test_dod_runner_ratchet.py`` fails if the count grows.

Usage:
    python scripts/ci/generate_dod_legacy_allowlist.py [--check]

``--check`` verifies the committed allowlist is a superset-free, sorted, valid
file without rewriting it (used by CI).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

_TICKET_RE = re.compile(r"^OMN-\d+$")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTRACTS_DIR = _REPO_ROOT / "contracts"
_ALLOWLIST = _REPO_ROOT / "scripts" / "ci" / "dod_runner_legacy_allowlist.txt"


def read_allowlist(path: Path = _ALLOWLIST) -> list[tuple[str, str | None]]:
    """Return (ticket, digest) pairs from the committed allowlist, in file order.

    ``digest`` is None for a legacy ticket-only line (pre-OMN-14436-digest
    format). ``--stamp-digests`` converts those; the runner REJECTS them.
    """
    entries: list[tuple[str, str | None]] = []
    for line in path.read_text().splitlines():
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        parts = entry.split()
        entries.append((parts[0], parts[1] if len(parts) > 1 else None))
    return entries


def _digest(ticket: str) -> str | None:
    """sha256 of the ticket's contract on disk, or None if it no longer exists."""
    f = _CONTRACTS_DIR / f"{ticket}.yaml"
    if not f.exists():
        return None
    return hashlib.sha256(f.read_bytes()).hexdigest()


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the committed allowlist instead of rewriting it.",
    )
    parser.add_argument(
        "--stamp-digests",
        action="store_true",
        help=(
            "One-time migration: bind each ALREADY-LISTED ticket to its current "
            "contract digest. Never adds a ticket; drops entries whose contract "
            "is gone."
        ),
    )
    args = parser.parse_args()

    entries = read_allowlist()
    tickets = [t for t, _ in entries]

    if args.stamp_digests:
        # ONE-TIME migration to the digest-bound format. This NEVER adds a
        # ticket -- it only annotates tickets already on the list with the
        # contract content they were grandfathered for. Entries whose contract
        # no longer exists are DROPPED (the ratchet may shrink, never grow).
        kept: list[str] = []
        dropped: list[str] = []
        for ticket, _old in entries:
            d = _digest(ticket)
            if d is None:
                dropped.append(ticket)
                continue
            kept.append(f"{ticket} {d}")
        header = [
            ln
            for ln in _ALLOWLIST.read_text().splitlines()
            if ln.lstrip().startswith("#")
        ]
        _ALLOWLIST.write_text("\n".join([*header, *kept]) + "\n")
        print(
            f"[STAMPED] {len(kept)} entries bound to their contract digest; "
            f"{len(dropped)} dropped (contract gone). Tickets added: 0."
        )
        return 0

    malformed = [t for t in tickets if not _TICKET_RE.match(t)]
    if malformed:
        print(f"[FAIL] malformed entries: {malformed[:10]}", file=sys.stderr)
        return 1

    if len(set(tickets)) != len(tickets):
        print("[FAIL] allowlist contains duplicate ticket ids", file=sys.stderr)
        return 1

    undigested = [t for t, d in entries if d is None]
    if undigested:
        print(
            f"[FAIL] {len(undigested)} allowlist entries carry no contract digest "
            f"(e.g. {undigested[:5]}). A digest-less entry is a ticket-keyed "
            "exemption -- it would let new circular evidence be appended under an "
            "old ticket id and inherit its grandfather forever. Run "
            "--stamp-digests.",
            file=sys.stderr,
        )
        return 1

    if args.check:
        print(
            f"[PASS] allowlist valid: {len(tickets)} grandfathered tickets, "
            "each bound to its contract digest (modification revokes the exemption)"
        )
        return 0

    print(
        "[REFUSED] This allowlist is a ratchet and is already populated.\n"
        "Regenerating it would grandfather every contract added since the cutoff\n"
        "and silently defeat the gate. To EXEMPT a legacy ticket, it is already\n"
        "listed. To ENFORCE one, delete its line. Never add lines.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
