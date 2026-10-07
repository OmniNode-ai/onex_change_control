# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Remove named grant entries from ``grants/prod_promotion_grants.yaml``.

WHY THIS IS A SCRIPT AND NOT AN INLINE HEREDOC. ``stage-prod-promotion-grant.yml``
renders a request bundle onto ``main`` as the onexbot-occ-writer App so the
CODEOWNER's review is a genuine second party (operator ruling 2026-09-06). The
operator ruled on 2026-09-08 that revocation is the same file, the same gate and
the same lifecycle, so it goes through the same App-authored process rather than
a hand-opened PR. Revocation, unlike staging, has real logic in it — which
entries go, which must stay, and what the resulting file is allowed to look like
— so it lives here where it is unit-testable, and the workflow calls it.

THE THREE INVARIANTS, all fail-closed.

1. EVERY NAMED ENTRY MUST BE PRESENT. A revocation that silently no-ops on an
   id that is not there reads exactly like one that worked. If any named
   ``grant_id`` is absent from the registry, this refuses and removes nothing.

2. NO EXPIRED ENTRY MAY REMAIN. OMN-13424: an expired entry left at rest fails
   the at-rest invariant for every subsequent PR to ``main``. A revocation that
   removes the live entries and leaves a lapsed one behind converts one problem
   into a permanently red branch, so the result is checked before it is written.

3. THE HEADER IS BYTE-IDENTICAL. Everything above the top-level ``entries:`` key
   is prose the operator reads when approving. The rewrite is textual and
   line-scoped precisely so a re-dump can never reflow it; the header bytes of
   the input are asserted to be a prefix of the output.

The removal itself is textual for the same reason a receipt is never
round-tripped: ``yaml.safe_dump`` of the parsed document would reorder keys,
restyle every block scalar and drop every comment, producing a diff no reviewer
can read and destroying the header this file exists to carry.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

_ENTRIES_EMPTY = "entries: []"
_ENTRIES_KEY = "entries:"
_ITEM_PREFIX = "  - "


class RevocationRefusedError(Exception):
    """The requested revocation is refused; nothing was written."""


@dataclass(frozen=True)
class RevocationResult:
    """The rendered registry text plus what the render actually did."""

    text: str
    removed_grant_ids: tuple[str, ...]
    remaining_grant_ids: tuple[str, ...]
    header: str
    at_rest: bool
    problems: list[str] = field(default_factory=list)


def _split_header(text: str) -> tuple[str, list[str]]:
    """Return (header text, body lines) split at the top-level ``entries`` key.

    The header is every byte up to and including the newline preceding the
    ``entries:`` line. It is returned verbatim and never re-rendered.
    """
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if stripped in (_ENTRIES_KEY, _ENTRIES_EMPTY):
            return "".join(lines[:index]), lines[index + 1 :]
    msg = (
        "no top-level 'entries:' key found at column 0 in the registry; refusing "
        "to guess where the header ends."
    )
    raise RevocationRefusedError(msg)


def _split_entry_blocks(body: list[str]) -> list[list[str]]:
    """Group the lines after ``entries:`` into one block per list item."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in body:
        if line.startswith(_ITEM_PREFIX):
            if current is not None:
                blocks.append(current)
            current = [line]
            continue
        if current is None:
            if line.strip() == "":
                continue
            msg = f"unexpected content before the first entry in the registry: {line!r}"
            raise RevocationRefusedError(msg)
        current.append(line)
    if current is not None:
        blocks.append(current)
    return blocks


def _block_grant_id(block: list[str]) -> str:
    parsed = yaml.safe_load("".join(block))
    if (
        not isinstance(parsed, list)
        or len(parsed) != 1
        or not isinstance(parsed[0], dict)
    ):
        msg = "a registry list item did not parse to exactly one mapping; refusing."
        raise RevocationRefusedError(msg)
    grant_id = parsed[0].get("grant_id")
    if not isinstance(grant_id, str) or not grant_id:
        msg = "a registry entry carries no 'grant_id'; refusing."
        raise RevocationRefusedError(msg)
    return grant_id


def _expiry(block: list[str]) -> datetime | None:
    parsed = yaml.safe_load("".join(block))
    raw = parsed[0].get("expires_at")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _assert_manifest_well_formed(grant_ids: list[str]) -> None:
    """INVARIANT 0. A revocation must name something, exactly once."""
    if not grant_ids:
        msg = (
            "the revocation manifest names no grant ids; a revocation that removes "
            "nothing is not a revocation."
        )
        raise RevocationRefusedError(msg)
    if len(set(grant_ids)) != len(grant_ids):
        msg = f"duplicate grant ids in the manifest: {grant_ids!r}"
        raise RevocationRefusedError(msg)


def _assert_all_present(present: set[str], grant_ids: list[str]) -> None:
    """INVARIANT 1. Every named entry must actually be in the registry."""
    missing = sorted(set(grant_ids) - present)
    if missing:
        msg = (
            f"grant id(s) {missing} named by the revocation manifest are NOT present "
            f"in the registry (it currently holds {sorted(present)}). Refusing "
            "fail-closed: a revocation that silently no-ops on an absent id is "
            "indistinguishable from one that worked."
        )
        raise RevocationRefusedError(msg)


def _assert_no_expired_remains(kept: list[list[str]], now: datetime) -> None:
    """INVARIANT 2 (OMN-13424). No expired entry may be left at rest."""
    stale = [
        _block_grant_id(block)
        for block in kept
        if (expires := _expiry(block)) is not None and expires <= now
    ]
    if stale:
        msg = (
            f"the revocation would leave expired entry(ies) {sorted(stale)} at rest. "
            "OMN-13424: an expired entry fails the at-rest invariant for every "
            "subsequent PR to main. Revoke those in the same action or reconcile "
            "the registry first."
        )
        raise RevocationRefusedError(msg)


def revoke_entries(
    current: str,
    grant_ids: list[str],
    *,
    now: datetime | None = None,
) -> RevocationResult:
    """Remove ``grant_ids`` from the registry text, or raise ``RevocationRefusedError``.

    ``now`` is injectable so the OMN-13424 remaining-entry expiry check is
    testable without waiting for a real clock to pass a real timestamp.
    """
    _assert_manifest_well_formed(grant_ids)

    now = now or datetime.now(UTC)
    header, body = _split_header(current)
    blocks = _split_entry_blocks(body)
    present = {_block_grant_id(block) for block in blocks}
    _assert_all_present(present, grant_ids)

    doomed = set(grant_ids)
    kept = [block for block in blocks if _block_grant_id(block) not in doomed]
    _assert_no_expired_remains(kept, now)

    if kept:
        kept_text = "".join(line for block in kept for line in block)
        rendered = header + _ENTRIES_KEY + "\n" + kept_text
    else:
        rendered = header + _ENTRIES_EMPTY + "\n"

    if not rendered.startswith(header):
        msg = (
            "internal: the rendered registry does not carry the original header bytes."
        )
        raise RevocationRefusedError(msg)

    parsed = yaml.safe_load(rendered)
    if set(parsed) != {"entries"}:
        msg = (
            f"the rendered registry has top-level keys {sorted(parsed)}, expected "
            "exactly ['entries']."
        )
        raise RevocationRefusedError(msg)
    remaining = parsed["entries"] or []
    if not isinstance(remaining, list):
        msg = "the rendered registry's 'entries' is not a list."
        raise RevocationRefusedError(msg)
    remaining_ids = [entry["grant_id"] for entry in remaining]
    still_there = sorted(set(grant_ids) & set(remaining_ids))
    if still_there:
        msg = f"internal: grant id(s) {still_there} survived the removal."
        raise RevocationRefusedError(msg)

    return RevocationResult(
        text=rendered,
        removed_grant_ids=tuple(grant_ids),
        remaining_grant_ids=tuple(remaining_ids),
        header=header,
        at_rest=not remaining_ids,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove named prod-promotion grant entries from the registry, "
            "preserving the header bytes exactly."
        )
    )
    parser.add_argument(
        "--grants-file",
        type=Path,
        default=Path("grants/prod_promotion_grants.yaml"),
        help="registry to rewrite in place",
    )
    parser.add_argument(
        "--grant-id",
        action="append",
        default=[],
        dest="grant_ids",
        help="grant id to remove; repeatable. Every id must be present.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the rendered registry to stdout and write nothing",
    )
    args = parser.parse_args(argv)

    current = args.grants_file.read_text(encoding="utf-8")
    try:
        result = revoke_entries(current, list(args.grant_ids))
    except RevocationRefusedError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        sys.stdout.write(result.text)
        return 0

    args.grants_file.write_text(result.text, encoding="utf-8")
    print(
        f"Removed {len(result.removed_grant_ids)} grant entry(ies): "
        f"{', '.join(result.removed_grant_ids)}. "
        + (
            "Registry is now at rest (entries: [])."
            if result.at_rest
            else f"Remaining: {', '.join(result.remaining_grant_ids)}."
        )
    )
    print(f"Header preserved byte-identical: {len(result.header)} bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
