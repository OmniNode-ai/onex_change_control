# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Evidence-Commit SHA existence gate (OMN-15111).

Found live 2026-07-25: the informal ``Evidence-Commit: <sha>`` trailer
convention — cite the onex_change_control commit a product PR's
``Evidence-Source`` line resolves to — has been embedded fleet-wide into
receipt ``check_value`` / ``probe_command`` / ``actual_output`` text since
OMN-14494, but nothing has ever validated that the cited SHA is a real
commit. It is pure decorative text that looks authoritative but is checked
by no gate.

This gate rejects any staged/modified ``drift/dod_receipts/**/*.yaml`` file
that embeds an ``Evidence-Commit: <sha>`` citation whose SHA does not
resolve to a real commit object in **this repository's local git history**.
It is purely offline (``git cat-file -e <sha>^{commit}``, no network calls),
so it runs at pre-commit speed — the authoring-time backstop for the
humans/tooling who type these SHAs into receipt text.

The cross-repo, network-dependent check — does the cited SHA also match the
*current* binding of the product PR's ``Evidence-Source`` line? — is a
separate concern that needs GitHub API access and lives in the CI-side
companion (``omnibase_core``'s ``receipt-gate.yml`` step, backed by
``validator_evidence_commit_binding_cli``). This gate only proves the SHA is
not fabricated/truncated; it does not (and, offline, cannot) prove it is
bound to any specific product PR.

Append-only correction path (OMN-16007)
---------------------------------------
A *merged* receipt carrying a garbled SHA cannot simply be edited: the OCC
Append-Only Gate (``omnibase_core.validation.validator_occ_append_only``)
rejects any ``M``/``D``/``R``/``C`` on ``drift/dod_receipts/**``, and says so
explicitly — "merged receipts are immutable. Corrections must be net-new
``.supersede.<NNNN>.yaml`` add-only files." Before OMN-16007 this gate had no
notion of that path, so the two gates were in direct conflict: the only way to
satisfy this one was an edit the other forbids.

This gate therefore honors the same supersession mechanism
``check_receipt_hardening.py`` already honors (its ``_supersession_candidates``
+ ``_valid_supersession_replacement`` pair, which likewise stops re-litigating
a base receipt's own content once a valid replacement sibling exists). A cited
SHA that does not resolve is *excused* only when a net-new sibling
supersession record retires it — see ``_superseded_shas`` for the three
conditions, all required. The exemption is deliberately narrow and cannot fail
open:

* it is keyed to the exact ``(receipt file, SHA)`` pair — a *different*
  unresolvable SHA in the same file still fails;
* the record must actually retire the SHA (its ``replacement`` may not still
  cite it), so a record cannot launder a SHA it re-binds;
* ``*.supersede.*.yaml`` files get **no** exemption themselves. They are
  net-new, so nothing excuses a bad SHA in one — which keeps the corpus
  shrink-only: existing garbled bindings can be corrected, new ones cannot be
  introduced through the correction path.

Exit codes: 0 = every cited Evidence-Commit SHA resolves locally (or is
retired by a supersession record); 1 = violations found.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable

# Matches "Evidence-Commit: <sha>" wherever it appears in the file's raw
# text (it is typically embedded inside a YAML string value — a
# check_value/probe_command/actual_output field — not a top-level YAML key),
# capturing a well-formed 7-40 char hex SHA immediately after the colon.
_EVIDENCE_COMMIT_RE = re.compile(r"Evidence-Commit:\s*([0-9a-fA-F]{7,40})\b")

# Filename infix that marks a net-new append-only correction record, per
# ModelReceiptSupersession: <check_type>.supersede.<NNNN>.yaml
_SUPERSEDE_TOKEN = ".supersede."


def _commit_exists_locally(sha: str) -> bool:
    """True iff ``sha`` is a real commit reachable in the local git object store."""
    result = subprocess.run(  # noqa: S603
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _is_supersession_record(path: Path) -> bool:
    """True for a net-new ``<stem>.supersede.<NNNN>.yaml`` correction record."""
    return _SUPERSEDE_TOKEN in path.name


def _supersession_candidates(path: Path) -> list[Path]:
    """Return append-only supersession records that may replace ``path``.

    Mirrors ``check_receipt_hardening._supersession_candidates`` so the two
    gates resolve the same correction chain for a given base receipt.
    """
    if path.suffix != ".yaml":
        return []
    stem = path.name[: -len(path.suffix)]
    return sorted(path.parent.glob(f"{stem}{_SUPERSEDE_TOKEN}*{path.suffix}"))


def _superseded_shas(path: Path) -> frozenset[str]:
    """Return the SHAs a sibling supersession record retires for ``path``.

    A merged receipt is immutable under the OCC Append-Only Gate, so a garbled
    ``Evidence-Commit`` in one can only be corrected by a net-new sibling
    record. This resolves which SHAs such a record has actually retired. All
    three conditions are required, and anything unparseable or ambiguous
    yields no exemption (fail closed):

    1. the record names *this* receipt in ``supersedes:`` (same item directory,
       same filename) — a record for a neighbouring item excuses nothing;
    2. it is not a tombstone and carries a ``replacement:`` — a tombstone
       invalidates the key rather than re-binding it, so it asserts no
       corrected SHA;
    3. the record's ``reason`` names the SHA verbatim, AND the SHA no longer
       appears anywhere in the ``replacement``. Both halves are load-bearing.
       The ``reason`` requirement is what keys the exemption to the specific
       SHA a human documented as garbled: without it, *every* unresolvable SHA
       in the base — including ones the correction never looked at — would be
       excused merely by being absent from the replacement, which is a
       fail-open hole. The ``replacement`` requirement is what proves the
       correction actually happened: a record that still binds the garbled
       value has corrected nothing and must not be able to launder it.
       ``ModelReceiptSupersession`` is ``extra="forbid"``, so ``reason`` — the
       field that already exists to explain the correction — is where this
       provenance belongs; a bespoke ``retires:`` key would invalidate the
       record itself.

    Supersession records are never themselves exempt; see ``check_file``.
    """
    retired: set[str] = set()
    for candidate in _supersession_candidates(path):
        try:
            record = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue  # unreadable/malformed record excuses nothing
        if not isinstance(record, dict):
            continue

        declared = record.get("supersedes")
        if not isinstance(declared, str):
            continue
        declared_path = PurePosixPath(declared)
        if (
            declared_path.name != path.name
            or declared_path.parent.name != path.parent.name
        ):
            continue

        if record.get("tombstone") is True:
            continue
        replacement = record.get("replacement")
        if not isinstance(replacement, dict):
            continue

        reason = record.get("reason")
        if not isinstance(reason, str):
            continue
        reason_text = reason.lower()

        # Any occurrence anywhere in the replacement counts as "still cited",
        # not just an Evidence-Commit trailer: a replacement that re-binds the
        # garbled value in commit_sha or a probe argument has not retired it.
        replacement_text = yaml.safe_dump(
            replacement, default_flow_style=False
        ).lower()
        for sha in _EVIDENCE_COMMIT_RE.findall(path.read_text(encoding="utf-8")):
            lowered = sha.lower()
            if lowered in reason_text and lowered not in replacement_text:
                retired.add(sha)
    return frozenset(retired)


def check_file(
    path: Path, *, commit_exists: Callable[[str], bool] = _commit_exists_locally
) -> list[str]:
    """Return violations for each unresolvable Evidence-Commit SHA in ``path``."""
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    shas = _EVIDENCE_COMMIT_RE.findall(text)

    # A supersession record is itself net-new, so nothing excuses a bad SHA in
    # one. Only an immutable base receipt can be corrected by a sibling record.
    retired = frozenset() if _is_supersession_record(path) else _superseded_shas(path)

    for sha in sorted(set(shas)):
        if commit_exists(sha) or sha in retired:
            continue
        violations.append(
            f"{path}: Evidence-Commit '{sha}' does not resolve to a real "
            "commit in this repository's local git history (fabricated, "
            "truncated, or the local clone is missing the commit — run "
            "`git fetch` and retry before assuming it is fabricated). If this "
            "receipt is already merged it is immutable under the OCC "
            "Append-Only Gate: correct it with a net-new "
            f"'{path.stem}{_SUPERSEDE_TOKEN}<NNNN>{path.suffix}' record whose "
            "replacement cites the real commit — never by editing this file."
        )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evidence-Commit SHA existence gate: every 'Evidence-Commit: <sha>' "
            "citation embedded in a staged receipt YAML must resolve to a real "
            "commit in this repo's local git history (OMN-15111)."
        )
    )
    parser.add_argument(
        "files", nargs="*", help="Receipt YAML paths (from pre-commit)."
    )
    args = parser.parse_args(argv)

    all_violations: list[str] = []
    for file_arg in args.files:
        path = Path(file_arg)
        if not path.is_file():
            continue  # deleted/renamed paths are not this gate's concern
        all_violations.extend(check_file(path))

    if all_violations:
        print(
            f"Evidence-Commit SHA existence gate: {len(all_violations)} violation(s):\n"
        )
        for violation in all_violations:
            print(f"  {violation}")
        print(
            "\nFix the receipt (cite the real onex_change_control commit that "
            "the bound Evidence-Source actually resolves to); never bypass the "
            "gate."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
