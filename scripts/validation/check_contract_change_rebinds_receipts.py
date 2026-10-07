#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""A contract change re-verifies every per-entry binding of its ticket (OMN-18304).

THE HOLE THIS CLOSES. The Receipt Hardening Gate validates the files in the
diff. A changed ``contracts/<TICKET>.yaml`` is routed to the contract-shaped
check and is never expanded into that ticket's receipts, so an edit that moves
or renames a ``dod_evidence`` entry leaves every receipt bound to it stale while
the receipt's own bytes are untouched and therefore unexamined. The unit of
verification was the changed FILE; the unit of damage is the changed TICKET.

MEASURED, 2026-09-13. Resolving a same-ticket companion collision on
``onex_change_control#9327`` renamed one evidence item in
``contracts/OMN-18291.yaml`` and left the pre-rename ``contract_entry_sha256``
on its receipt. Nothing local objected. It surfaced in CI as
``occ-preflight / eligibility`` reporting ``contract_hash_mismatch``, was
recomputed by hand, and cost the lane a round. The collision-resolving union is
a documented hand procedure with no code home, so there is no producer to fix:
the enforceable repair is to make the omission visible where it is made.

WHAT IS CHECKED, and deliberately what is not.

For every receipt under a changed contract's ticket:

* **A declared per-entry hash must recompute.** ``contract_entry_sha256`` is
  recomputed with ``compute_contract_entry_sha256`` — the same function
  occ-preflight and the Receipt Gate use, imported, never reimplemented.
* **A per-entry hash must still name a live entry.** A receipt carrying an
  entry hash for an id the contract no longer declares is a rename that moved
  the entry out from under it. That is reported as a rename rather than as a
  generic orphan, because the fix is different: rebind and move the receipt,
  not declare a new item.

SCOPED TO THE ENTRIES THIS CHANGE TOUCHES, and that scoping is the design, not
a concession. The gate compares the contract's per-entry hashes BEFORE and AFTER
the change and only re-verifies receipts bound to an entry that was ADDED,
REMOVED, or whose hash MOVED in this diff. A rename is all three at once, so the
measured defect is squarely inside the scope; a binding this change did not
disturb is outside it.

The alternative — re-verifying every binding of the ticket unconditionally — was
built first and MEASURED against the whole corpus before being rejected: 13
findings across 8,870 tickets, of which 10 are genuinely stale per-entry
bindings on receipts merged long ago (OMN-14702, OMN-14703, OMN-16322,
OMN-17462, OMN-17982). Those cannot be repaired in place — the OCC Append-Only
Gate rejects an edit to a merged receipt as ``receipt_file_mutated`` — so an
unscoped gate would have needed a suppression baseline on day one, and a gate
that ships with a suppression list is a gate nobody trusts. Scoping by what the
diff moves removes the need for one entirely: there is no exemption file here
and no annotation that turns a finding off.

``--all`` keeps the unscoped sweep available as MEASUREMENT. It is not the gate
and is not wired to anything; it is how the 13 above were counted and how a
future lane can re-count them.

NOT checked here, on purpose: the whole-file ``contract_sha256``. That hash is
invalidated by every append to the contract by any lane, so a merged corpus is
full of stale ones by construction; they are grandfathered by the consumer gates
and are what OMN-13888 and OMN-18304 shrink from the producer side. A receipt
with no per-entry hash at all is likewise out of scope: a union cannot have
staled something the receipt does not carry.

Supersession records are read at their ``replacement`` binding, since that is
the receipt a consumer resolves to.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml
from omnibase_core.validation.validator_receipt_gate import (
    ContractEntryNotFoundError,
    compute_contract_entry_sha256,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONTRACTS_DIR = "contracts"
_DEFAULT_RECEIPTS_ROOT = "drift/dod_receipts"

#: The unrebound authoring sentinel. A producer writes it and its own rebind
#: pass replaces it; one that reaches a commit is an unfinished mint, not a
#: binding, so it is reported separately from a wrong hash.
_PENDING = "sha256:PENDING"


#: Hash strings are compared as VALUES, with surrounding whitespace stripped. A
#: hash rendered through a YAML block scalar carries a trailing newline, which is
#: a rendering artifact of the file, not a different digest; three receipts in
#: the live corpus (all under OMN-15320) are written that way. Comparing the raw
#: scalar would report them as mismatches whose "expected" and "actual" differ by
#: an invisible character, which is a worse finding than none.


@dataclass(frozen=True)
class _Binding:
    """One receipt's per-entry binding, wherever in the file it lives."""

    evidence_item_id: str
    contract_entry_sha256: str
    where: str


def _extract_bindings(document: object) -> list[_Binding]:
    """Every per-entry binding a receipt document carries.

    A plain receipt carries one at the top level. A supersession record carries
    its binding on the nested ``replacement`` — the receipt a consumer actually
    resolves to — so reading only the top level would skip exactly the records a
    union emits.
    """
    if not isinstance(document, dict):
        return []
    bindings: list[_Binding] = []
    top_hash = document.get("contract_entry_sha256")
    top_id = document.get("evidence_item_id")
    if isinstance(top_hash, str) and isinstance(top_id, str):
        bindings.append(_Binding(top_id, top_hash.strip(), "contract_entry_sha256"))
    replacement = document.get("replacement")
    if isinstance(replacement, dict):
        nested_hash = replacement.get("contract_entry_sha256")
        nested_id = replacement.get("evidence_item_id") or top_id
        if isinstance(nested_hash, str) and isinstance(nested_id, str):
            bindings.append(
                _Binding(
                    nested_id,
                    nested_hash.strip(),
                    "replacement.contract_entry_sha256",
                )
            )
    return bindings


def _display_path(path: Path) -> str:
    """The repo-relative path when resolvable, else the path as given.

    Findings are read by a person fixing a file, so the message names the path
    they would open. A temporary fixture outside the repo prints as-is rather
    than raising.
    """
    try:
        return path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _declared_ids(contract_data: object) -> set[str]:
    if not isinstance(contract_data, dict):
        return set()
    items = contract_data.get("dod_evidence")
    if not isinstance(items, list):
        return set()
    return {
        item["id"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _entry_hashes(contract_data: object) -> dict[str, str]:
    """Every declared entry's canonical per-entry hash, keyed by id."""
    hashes: dict[str, str] = {}
    for evidence_id in _declared_ids(contract_data):
        try:
            hashes[evidence_id] = compute_contract_entry_sha256(
                contract_data, evidence_id
            )
        except ContractEntryNotFoundError:  # pragma: no cover - id came from the list
            continue
    return hashes


def touched_entry_ids(before: object, after: object) -> set[str]:
    """The entry ids this change added, removed, or moved.

    A rename is all three at once — the old id disappears and the new one
    appears — which is why a rename is always inside the scope. An entry whose
    canonical hash is byte-identical across the change is outside it: this
    change cannot have invalidated a binding to it, and reporting one would be
    reporting somebody else's pre-existing defect against this author.
    """
    before_hashes = _entry_hashes(before)
    after_hashes = _entry_hashes(after)
    return {
        evidence_id
        for evidence_id in before_hashes.keys() | after_hashes.keys()
        if before_hashes.get(evidence_id) != after_hashes.get(evidence_id)
    }


def check_ticket(  # noqa: C901 - one violation class per branch, each named
    ticket_id: str,
    *,
    contracts_dir: Path,
    receipts_root: Path,
    scope: set[str] | None = None,
) -> list[str]:
    """Re-verify this ticket's per-entry bindings.

    ``scope`` limits the check to the entry ids a change touched. ``None`` means
    every binding — the unscoped MEASUREMENT sweep, never the gate.
    """
    contract_path = contracts_dir / f"{ticket_id}.yaml"
    if not contract_path.is_file():
        return []
    try:
        contract_data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{contract_path}: unparseable contract ({exc})"]

    declared = _declared_ids(contract_data)
    violations: list[str] = []
    ticket_dir = receipts_root / ticket_id
    if not ticket_dir.is_dir():
        return violations

    for receipt_path in sorted(ticket_dir.rglob("*.yaml")):
        try:
            document = yaml.safe_load(receipt_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            violations.append(f"{receipt_path}: unparseable receipt ({exc})")
            continue
        for binding in _extract_bindings(document):
            if scope is not None and binding.evidence_item_id not in scope:
                continue
            rel = _display_path(receipt_path)
            if binding.contract_entry_sha256 == _PENDING:
                violations.append(
                    f"{rel}: {binding.where} is still the unrebound "
                    f"{_PENDING!r} sentinel for {binding.evidence_item_id!r} — "
                    "the mint's rebind pass did not run, or ran before the item "
                    "was declared"
                )
                continue
            if binding.evidence_item_id not in declared:
                violations.append(
                    f"{rel}: {binding.where} binds "
                    f"{binding.evidence_item_id!r}, which "
                    f"{contract_path.name} no longer declares. An entry was "
                    "renamed or moved out from under this receipt; rebind it to "
                    "the entry's new id and file it under the matching directory."
                )
                continue
            try:
                expected = compute_contract_entry_sha256(
                    contract_data, binding.evidence_item_id
                )
            except (
                ContractEntryNotFoundError
            ) as exc:  # pragma: no cover - guarded above
                violations.append(f"{rel}: {exc}")
                continue
            if binding.contract_entry_sha256 != expected:
                violations.append(
                    f"{rel}: {binding.where} is "
                    f"{binding.contract_entry_sha256!r} but "
                    f"dod_evidence[{binding.evidence_item_id!r}] now hashes to "
                    f"{expected!r}. The entry changed after the receipt was "
                    "minted; recompute the binding rather than editing the "
                    "expected value."
                )
    return violations


def _tickets_from_paths(paths: list[Path], contracts_dir: Path) -> list[str]:
    """The ticket ids whose contract appears among ``paths``."""
    prefix = f"{contracts_dir.as_posix()}/"
    tickets: list[str] = []
    for path in paths:
        posix = path.as_posix()
        if posix.startswith(prefix) and path.suffix in {".yaml", ".yml"}:
            tickets.append(path.stem)
    return sorted(dict.fromkeys(tickets))


def contract_at_revision(revision: str, contract_path: Path) -> object:
    """The contract as of ``revision``, or ``None`` when it did not exist there.

    ``None`` is the correct answer for a contract this change CREATES: every one
    of its entries is then new, so every binding to it is in scope. Failing open
    here would exempt exactly the fresh-companion case.
    """
    # S603: the revision is interpolated into a FIXED argv with no shell, so it
    # can only ever name a git object; it is never a command. S607: git resolves
    # from PATH here exactly as it does in every sibling validator.
    completed = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            "show",
            f"{revision}:{contract_path.as_posix()}",
        ],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        return yaml.safe_load(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return None


def _staged_paths() -> tuple[list[Path], str | None]:
    try:
        completed = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT"],  # noqa: S607
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        return [], str(exc)
    return [
        Path(os.fsdecode(item)) for item in completed.stdout.split(b"\0") if item
    ], None


def _paths_from_file0(path: Path) -> list[Path]:
    return [Path(os.fsdecode(item)) for item in path.read_bytes().split(b"\0") if item]


def _self_test(contracts_dir: Path, receipts_root: Path) -> int:
    """Positive control: build the measured rename and require findings.

    Reproduces ``onex_change_control#9327`` in miniature — a contract whose
    evidence item is renamed while the receipt keeps the pre-rename hash — and
    exercises the SCOPED path the gate actually runs, so the control proves the
    gate rather than a sibling code path. It fails if the scoped check reports
    clean on the rename, or if it reports anything on the pre-rename state. A
    zero from a check that cannot fail is not a zero (rule 16).
    """
    del contracts_dir, receipts_root
    ticket = "OMN-0000"
    before_id = "dod-occ-proof-before"
    after_id = "dod-occ-proof-after"
    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root)
        contracts = root / _DEFAULT_CONTRACTS_DIR
        receipts = root / _DEFAULT_RECEIPTS_ROOT
        (receipts / ticket / before_id).mkdir(parents=True)
        contracts.mkdir(parents=True)
        contract_path = contracts / f"{ticket}.yaml"

        def contract_text(item_id: str) -> str:
            return (
                "---\n"
                'schema_version: "1.0.0"\n'
                f'ticket_id: "{ticket}"\n'
                "dod_evidence:\n"
                f'  - id: "{item_id}"\n'
                '    source: "generated"\n'
                "    checks:\n"
                '      - check_type: "test_passes"\n'
                '        check_value: "uv run pytest tests/test_thing.py -q"\n'
            )

        before = yaml.safe_load(contract_text(before_id))
        contract_path.write_text(contract_text(before_id), encoding="utf-8")
        (receipts / ticket / before_id / "test_passes.yaml").write_text(
            "---\n"
            'schema_version: "1.0.0"\n'
            f'ticket_id: "{ticket}"\n'
            f'evidence_item_id: "{before_id}"\n'
            'check_type: "test_passes"\n'
            f'contract_entry_sha256: "'
            f'{compute_contract_entry_sha256(before, before_id)}"\n'
            "status: PASS\n",
            encoding="utf-8",
        )

        unchanged_scope = touched_entry_ids(before, before)
        if unchanged_scope:
            print(
                "POSITIVE CONTROL FAILED: an unchanged contract reported "
                f"{len(unchanged_scope)} touched entr(ies), so the gate is not "
                "scoped to what a change moves.",
                file=sys.stderr,
            )
            return 1
        clean = check_ticket(
            ticket,
            contracts_dir=contracts,
            receipts_root=receipts,
            scope=touched_entry_ids(before, before),
        )
        if clean:
            print(
                "POSITIVE CONTROL FAILED: the correctly bound pre-rename "
                f"fixture reported {len(clean)} finding(s): {clean}",
                file=sys.stderr,
            )
            return 1

        # The union renames the entry and leaves the receipt's hash behind.
        after = yaml.safe_load(contract_text(after_id))
        contract_path.write_text(contract_text(after_id), encoding="utf-8")
        scope = touched_entry_ids(before, after)
        if {before_id, after_id} - scope:
            print(
                "POSITIVE CONTROL FAILED: a rename did not put both the old and "
                f"the new id in scope; got {sorted(scope)}.",
                file=sys.stderr,
            )
            return 1
        findings = check_ticket(
            ticket, contracts_dir=contracts, receipts_root=receipts, scope=scope
        )
        if not findings:
            print(
                "POSITIVE CONTROL FAILED: a renamed evidence item with a "
                "pre-rename contract_entry_sha256 reported clean, so a clean "
                "run of this gate proves nothing.",
                file=sys.stderr,
            )
            return 1

    print(f"positive control OK: {len(findings)} finding(s) on the rename fixture.")
    return 0


def _check_tickets(
    tickets: list[str],
    *,
    contracts_dir: Path,
    receipts_root: Path,
    base_rev: str | None,
) -> tuple[list[str], int]:
    """Run the check over each ticket, scoped unless ``base_rev`` is ``None``.

    ``None`` selects the unscoped MEASUREMENT sweep. The gate always passes a
    revision.
    """
    violations: list[str] = []
    scoped_entries = 0
    for ticket in tickets:
        contract_path = contracts_dir / f"{ticket}.yaml"
        scope: set[str] | None = None
        if base_rev is not None:
            after = (
                yaml.safe_load(contract_path.read_text(encoding="utf-8"))
                if contract_path.is_file()
                else None
            )
            scope = touched_entry_ids(
                contract_at_revision(base_rev, contract_path), after
            )
            scoped_entries += len(scope)
            if not scope:
                continue
        violations.extend(
            check_ticket(
                ticket,
                contracts_dir=contracts_dir,
                receipts_root=receipts_root,
                scope=scope,
            )
        )
    return violations, scoped_entries


def main(  # noqa: C901, PLR0912 - argparse source selection is one branch per mode
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--staged", action="store_true", help="read staged paths from the git index"
    )
    source.add_argument(
        "--paths-file0", help="read NUL-delimited changed paths from this file"
    )
    source.add_argument(
        "--all",
        action="store_true",
        help="verify every ticket in the corpus (measurement, not the gate)",
    )
    source.add_argument(
        "--self-test",
        action="store_true",
        help="positive control: require findings on a synthesized rename",
    )
    parser.add_argument("files", nargs="*", help="explicit changed paths")
    parser.add_argument("--contracts-dir", default=_DEFAULT_CONTRACTS_DIR)
    parser.add_argument("--receipts-root", default=_DEFAULT_RECEIPTS_ROOT)
    parser.add_argument(
        "--base-rev",
        default="HEAD",
        help=(
            "the revision this change is measured against; entries whose "
            "canonical hash is unchanged since it are out of scope "
            "(default: HEAD, which is correct for the staged pre-commit case)"
        ),
    )
    args = parser.parse_args(argv)

    contracts_dir = Path(args.contracts_dir)
    receipts_root = Path(args.receipts_root)

    if args.self_test:
        return _self_test(contracts_dir, receipts_root)

    if args.all:
        tickets = sorted(path.stem for path in contracts_dir.glob("*.yaml"))
    else:
        if args.staged:
            paths, error = _staged_paths()
            if error is not None:
                print(f"contract-rebind gate infrastructure unavailable: {error}")
                return 2
        elif args.paths_file0:
            try:
                paths = _paths_from_file0(Path(args.paths_file0))
            except OSError as exc:
                print(f"contract-rebind gate infrastructure unavailable: {exc}")
                return 2
        else:
            paths = [Path(item) for item in args.files]
        tickets = _tickets_from_paths(paths, contracts_dir)

    if not tickets:
        print("No changed contract: nothing to re-verify.")
        return 0

    violations, scoped_entries = _check_tickets(
        tickets,
        contracts_dir=contracts_dir,
        receipts_root=receipts_root,
        base_rev=None if args.all else args.base_rev,
    )

    if violations:
        print(
            f"Contract-change rebind gate: {len(violations)} violation(s) across "
            f"{len(tickets)} changed ticket(s):\n"
        )
        for violation in violations:
            print(f"  {violation}")
        print(
            "\nA contract entry moved while a receipt kept its pre-move binding. "
            "Recompute the binding with compute_contract_entry_sha256; never "
            "edit the expected value to match."
        )
        return 1
    if args.all:
        print(
            f"Contract-change rebind sweep OK (MEASUREMENT, not the gate): every "
            f"per-entry binding across {len(tickets)} ticket(s) re-verifies."
        )
    else:
        print(
            f"Contract-change rebind gate OK: {scoped_entries} touched dod_evidence "
            f"entr(ies) across {len(tickets)} changed ticket(s); every receipt bound "
            "to one of them re-verifies."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
