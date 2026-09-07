# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""OMN-17943 — minting an entry hash onto a whole-file-bound receipt.

WHY THIS MIGRATION EXISTS
-------------------------
Both contract-binding validators branch entry-hash-FIRST:
``validator_receipt_gate._contract_binding_failure`` and
``check_receipt_hardening._contract_hash_violation`` each return as soon as
``contract_entry_sha256`` is present and correct, and consult the whole-file
``contract_sha256`` only as the fallback. So a whole-file-only receipt is
fragile in one specific way — appending ANY new ``dod_evidence`` item to its
contract changes the file bytes, the fallback stops matching, and previously
valid merged evidence reads as "contract mutated after this receipt was
produced".

That fragility is what makes the diff-derived behavior-proof backfill refuse
those contracts outright (``REFUSED_LEGACY_WHOLE_FILE_BINDING``): eight of the
top-40 candidates in the live 2026-09-06 window, permanently unmintable.

WHAT THE TESTS HAVE TO PIN
--------------------------
The dangerous version of this tool is the one that mints an entry hash for
every legacy receipt it finds. On a receipt whose whole-file binding is ALREADY
stale, the hash computed now describes a contract state the receipt was never
bound to — that converts a DETECTABLE stale binding into an UNDETECTABLE false
one, which is manufacturing evidence. The refusal is therefore the load-bearing
test here, exactly as it is in the backfill.

The second thing worth pinning is that the write is one line. A YAML round-trip
would reflow the whole receipt, and OCC's yamlfmt-stability rules (OMN-17794)
exist because a reflow changes a receipt's committed VALUE.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from omnibase_core.validation.validator_receipt_gate import (
    compute_contract_entry_sha256,
    compute_contract_sha256,
)

from onex_change_control.scripts.migrate_legacy_receipt_entry_binding import (
    EnumEntryBindingDecision,
    insert_contract_entry_sha256,
    main,
    run,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_CONTRACT = """\
---
schema_version: "1.0.0"
ticket_id: "OMN-15425"
title: "Autobind OCC evidence for OMN-15425"
dod_evidence:
  - id: "dod-OmniNode-ai-omnibase_infra-pr-3014"
    description: "PR #3014 on OmniNode-ai/omnibase_infra — Evidence-Source autobind."
    source: "generated"
    checks:
      - check_type: "command"
        check_value: "gh pr view 3014 --repo OmniNode-ai/omnibase_infra"
"""

_RECEIPT_TEMPLATE = """\
---
schema_version: "1.0.0"
ticket_id: "OMN-15425"
evidence_item_id: "dod-OmniNode-ai-omnibase_infra-pr-3014"
check_type: "command"
check_value: |-
  gh pr view 3014 --repo OmniNode-ai/omnibase_infra --json number,state
contract_sha256: "{whole_file}"
status: PASS
run_timestamp: "2026-08-30T00:29:46.671392+00:00"
commit_sha: "e9b01ae6de5f792021d5e95c65b12d68d4429092"
runner: "node_occ_companion_compute"
verifier: "occ-evidence-source-autobind"
probe_command: "gh pr view 3014 --repo OmniNode-ai/omnibase_infra --json number,state"
exit_code: 0
pr_number: 3014
"""


def _seed(root: Path, *, whole_file: str | None = None) -> Path:
    """An OCC tree with one contract and one whole-file-bound receipt."""
    contracts = root / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    contract_path = contracts / "OMN-15425.yaml"
    contract_path.write_text(_CONTRACT, encoding="utf-8")

    binding = whole_file or f"sha256:{compute_contract_sha256(contract_path)}"
    receipt_dir = (
        root
        / "drift"
        / "dod_receipts"
        / "OMN-15425"
        / "dod-OmniNode-ai-omnibase_infra-pr-3014"
    )
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "command.yaml"
    receipt_path.write_text(
        _RECEIPT_TEMPLATE.format(whole_file=binding), encoding="utf-8"
    )
    return receipt_path


def test_a_still_valid_whole_file_binding_is_migrated(tmp_path: Path) -> None:
    """The entry hash is minted, and it is the hash of the receipt's OWN entry.

    The precondition that makes this sound is checked, not assumed: the
    receipt's ``contract_sha256`` still equals ``sha256(contract file)``, so the
    contract is byte-identical to the state the receipt was bound to and the
    entry it names is provably unchanged.
    """
    receipt_path = _seed(tmp_path)

    report = run(occ_root=tmp_path, tickets=["OMN-15425"], apply=True)

    assert report["counts"] == {EnumEntryBindingDecision.MIGRATED.value: 1}
    body = yaml.safe_load(receipt_path.read_text(encoding="utf-8"))
    expected = compute_contract_entry_sha256(
        yaml.safe_load((tmp_path / "contracts" / "OMN-15425.yaml").read_text()),
        "dod-OmniNode-ai-omnibase_infra-pr-3014",
    )
    assert body["contract_entry_sha256"] == expected
    # The historical whole-file statement is kept: it was true when minted, no
    # validator reads it once the entry hash is present, and deleting it would
    # destroy a record rather than fix anything.
    assert body["contract_sha256"] is not None


def test_an_already_stale_whole_file_binding_is_refused(tmp_path: Path) -> None:
    """THE LOAD-BEARING REFUSAL.

    A receipt whose whole-file binding no longer matches was bound to a
    contract state that is gone. Minting an entry hash from today's contract
    would assert a binding the receipt never had, turning a binding a validator
    can CATCH into one it cannot — the tool must refuse and say so.
    """
    receipt_path = _seed(tmp_path, whole_file="sha256:" + "a" * 64)

    report = run(occ_root=tmp_path, tickets=["OMN-15425"], apply=True)

    assert report["counts"] == {
        EnumEntryBindingDecision.REFUSED_BINDING_ALREADY_STALE.value: 1
    }
    assert report["migrated"] == 0
    body = yaml.safe_load(receipt_path.read_text(encoding="utf-8"))
    assert "contract_entry_sha256" not in body
    assert "supersession" in report["outcomes"][0]["reason"]


def test_an_entry_that_left_the_contract_is_refused(tmp_path: Path) -> None:
    """No hash is fabricated for a `dod_evidence` item that no longer exists."""
    _seed(tmp_path)
    contract_path = tmp_path / "contracts" / "OMN-15425.yaml"
    contract_path.write_text(
        _CONTRACT.replace("dod-OmniNode-ai-omnibase_infra-pr-3014", "dod-renamed"),
        encoding="utf-8",
    )
    # Re-bind the receipt to the REWRITTEN contract so the stale-binding refusal
    # cannot be what fires here; the entry lookup is the only thing left to fail.
    receipt_path = (
        tmp_path
        / "drift"
        / "dod_receipts"
        / "OMN-15425"
        / "dod-OmniNode-ai-omnibase_infra-pr-3014"
        / "command.yaml"
    )
    receipt_path.write_text(
        _RECEIPT_TEMPLATE.format(
            whole_file=f"sha256:{compute_contract_sha256(contract_path)}"
        ),
        encoding="utf-8",
    )

    report = run(occ_root=tmp_path, tickets=["OMN-15425"], apply=True)

    assert report["counts"] == {
        EnumEntryBindingDecision.REFUSED_ENTRY_NOT_IN_CONTRACT.value: 1
    }


def test_a_receipt_that_already_carries_an_entry_hash_is_left_alone(
    tmp_path: Path,
) -> None:
    """Idempotence: a second run over a migrated tree writes nothing."""
    _seed(tmp_path)
    run(occ_root=tmp_path, tickets=["OMN-15425"], apply=True)

    second = run(occ_root=tmp_path, tickets=["OMN-15425"], apply=True)

    assert second["counts"] == {EnumEntryBindingDecision.SKIPPED_NOT_LEGACY.value: 1}
    assert second["migrated"] == 0


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    """A report must not change the tree it reports on."""
    receipt_path = _seed(tmp_path)
    before = receipt_path.read_bytes()

    report = run(occ_root=tmp_path, tickets=["OMN-15425"], apply=False)

    assert report["would_migrate"] == 1
    assert report["migrated"] == 0
    assert receipt_path.read_bytes() == before


def test_the_write_adds_exactly_one_line_and_reflows_nothing() -> None:
    """A YAML round-trip would rewrite every other field's formatting.

    OCC's yamlfmt-stability work (OMN-17794) exists because a reflow changes a
    receipt's committed VALUE — a block scalar losing a keep indicator, a line
    marker eating a newline. So the write is textual, and the diff is one line.
    """
    original = _RECEIPT_TEMPLATE.format(whole_file="sha256:" + "d" * 64)

    migrated = insert_contract_entry_sha256(original, "sha256:" + "e" * 64)

    added = [
        line for line in migrated.splitlines() if line not in original.splitlines()
    ]
    assert added == ['contract_entry_sha256: "sha256:' + "e" * 64 + '"']
    assert migrated.splitlines()[migrated.splitlines().index(added[0]) - 1].startswith(
        "contract_sha256:"
    )
    assert len(migrated.splitlines()) == len(original.splitlines()) + 1


def test_a_receipt_with_no_whole_file_anchor_refuses_rather_than_guessing() -> None:
    """Appending the field somewhere a reader would not look is not a fix."""
    with pytest.raises(ValueError, match="no top-level `contract_sha256:` line"):
        insert_contract_entry_sha256("---\nticket_id: OMN-1\n", "sha256:" + "f" * 64)


def test_the_cli_refuses_a_ticket_list_that_names_no_ids(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty selection is an operator error, not a silent no-op success."""
    assert main(["--tickets", "nothing here"]) == 2
    assert "named no OMN ids" in capsys.readouterr().err
