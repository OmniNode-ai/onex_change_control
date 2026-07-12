# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the receipt hardening gate (OMN-13060, retro A-5; OMN-14411)."""

from __future__ import annotations

import copy
import hashlib
from typing import TYPE_CHECKING

import yaml
from omnibase_core.validation.validator_receipt_gate import (
    compute_contract_entry_sha256,
)

from scripts.validation.check_receipt_hardening import (
    DENYLISTED_VERIFIERS,
    check_receipt_file,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path

POST_CUTOFF_TS = "2026-06-12T03:00:00+00:00"
PRE_CUTOFF_TS = "2026-06-11T23:59:59+00:00"

CONTRACT_BODY = "ticket_id: OMN-13060\ntitle: test contract\n"

# A contract shaped with real dod_evidence entries, for OMN-14411 per-entry
# hash tests. schema_version is part of the immutable per-entry hash header
# (HEADER_FIELDS in validator_receipt_gate.compute_contract_entry_sha256).
ENTRY_CONTRACT_DATA: dict[str, object] = {
    "ticket_id": "OMN-13060",
    "schema_version": "1.0.0",
    "title": "test contract",
    "dod_evidence": [
        {
            "id": "dod-001",
            "summary": "first item",
            "checks": [{"check_type": "command"}],
        },
    ],
}


def _contract_sha(contract_path: Path) -> str:
    return f"sha256:{hashlib.sha256(contract_path.read_bytes()).hexdigest()}"


def _write_contract(tmp_path: Path, ticket: str = "OMN-13060") -> Path:
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    contract_path = contracts_dir / f"{ticket}.yaml"
    contract_path.write_text(CONTRACT_BODY)
    return contract_path


def _write_entry_contract(
    tmp_path: Path, contract_data: dict[str, object], ticket: str = "OMN-13060"
) -> Path:
    """Write a contract with a real dod_evidence list for per-entry tests."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    contract_path = contracts_dir / f"{ticket}.yaml"
    contract_path.write_text(yaml.safe_dump(contract_data))
    return contract_path


def _receipt_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-13060",
        "evidence_item_id": "dod-001",
        "check_type": "command",
        "check_value": "uv run pytest tests/ -q",
        "status": "PASS",
        "run_timestamp": POST_CUTOFF_TS,
        "commit_sha": "abc1234def",
        "runner": "worker-a",
        "verifier": "receipt-gate-ci",
        "probe_command": "uv run pytest tests/ -q",
        "probe_stdout": "37 passed",
    }
    data.update(overrides)
    return data


def _write_receipt(tmp_path: Path, data: dict[str, object]) -> Path:
    receipt_dir = tmp_path / "drift" / "dod_receipts" / "OMN-13060" / "dod-001"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "command.yaml"
    receipt_path.write_text(yaml.safe_dump(data))
    return receipt_path


def test_post_cutoff_receipt_with_matching_sha_passes(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path, _receipt_data(contract_sha256=_contract_sha(contract))
    )
    assert check_receipt_file(receipt, tmp_path / "contracts") == []


def test_post_cutoff_receipt_missing_sha_fails(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    receipt = _write_receipt(tmp_path, _receipt_data())
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "missing contract_sha256" in violations[0]


def test_pre_cutoff_receipt_is_exempt(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(run_timestamp=PRE_CUTOFF_TS, verifier="automated"),
    )
    assert check_receipt_file(receipt, tmp_path / "contracts") == []


def test_hash_mismatch_fails(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path, _receipt_data(contract_sha256=f"sha256:{'0' * 64}")
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "contract_sha256 mismatch" in violations[0]


def test_missing_contract_file_fails(tmp_path: Path) -> None:
    (tmp_path / "contracts").mkdir()
    receipt = _write_receipt(
        tmp_path, _receipt_data(contract_sha256=f"sha256:{'0' * 64}")
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "does not exist" in violations[0]


def test_denylisted_verifier_on_pass_fails(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(contract_sha256=_contract_sha(contract), verifier="automated"),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "session-local verifier alias" in violations[0]


def test_denylisted_verifier_on_fail_status_is_exempt(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            verifier="automated",
            status="FAIL",
        ),
    )
    assert check_receipt_file(receipt, tmp_path / "contracts") == []


def test_container_id_verifier_fails(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(contract_sha256=_contract_sha(contract), verifier="a1b2c3d4e5f6"),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "session-local verifier alias" in violations[0]


def test_self_attested_pass_is_demoted_not_denylist_checked(tmp_path: Path) -> None:
    """verifier == runner demotes PASS to ADVISORY at parse; the denylist
    rule only fires on receipts that remain PASS."""
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            runner="automated",
            verifier="automated",
        ),
    )
    assert check_receipt_file(receipt, tmp_path / "contracts") == []


def test_invalid_receipt_fails_model_validation(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    data = _receipt_data()
    del data["verifier"]
    receipt = _write_receipt(tmp_path, data)
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "ModelDodReceipt validation" in violations[0]


def test_minimal_supersession_record_is_not_plain_receipt_hardened(
    tmp_path: Path,
) -> None:
    """Supersession wrappers are validated by receipt-gate chain resolution."""
    _write_contract(tmp_path)
    receipt_dir = tmp_path / "drift" / "dod_receipts" / "OMN-13060" / "dod-001"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    supersession = receipt_dir / "command.supersede.0001.yaml"
    supersession.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "ticket_id": "OMN-13060",
                "evidence_item_id": "dod-001",
                "check_type": "command",
                "supersedes": "drift/dod_receipts/OMN-13060/dod-001/command.yaml",
                "reason": "test correction",
                "superseder": "pytest",
                "created_at": POST_CUTOFF_TS,
                "tombstone": False,
                "replacement": _receipt_data(),
            }
        )
    )
    assert check_receipt_file(supersession, tmp_path / "contracts") == []


def test_timestamp_less_receipt_is_exempt(tmp_path: Path) -> None:
    """No timestamp anywhere = pre-schema legacy artifact; the receipt
    gate already rejects it as NONPASS, so this hook exempts it."""
    _write_contract(tmp_path)
    data = _receipt_data()
    del data["run_timestamp"]
    receipt = _write_receipt(tmp_path, data)
    assert check_receipt_file(receipt, tmp_path / "contracts") == []


def test_nested_verified_at_fallback_enforces_post_cutoff(tmp_path: Path) -> None:
    """Legacy-shaped files with a nested post-cutoff verified_at are enforced."""
    _write_contract(tmp_path)
    data = _receipt_data()
    del data["run_timestamp"]
    data["evidence"] = {"verified_at": POST_CUTOFF_TS}
    receipt = _write_receipt(tmp_path, data)
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "ModelDodReceipt validation" in violations[0]


def test_nested_verified_at_fallback_exempts_pre_cutoff(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    data = _receipt_data()
    del data["run_timestamp"]
    data["evidence"] = {"verified_at": PRE_CUTOFF_TS}
    receipt = _write_receipt(tmp_path, data)
    assert check_receipt_file(receipt, tmp_path / "contracts") == []


def test_non_mapping_yaml_fails(tmp_path: Path) -> None:
    receipt_path = tmp_path / "command.yaml"
    receipt_path.write_text("- just\n- a\n- list\n")
    violations = check_receipt_file(receipt_path, tmp_path / "contracts")
    assert len(violations) == 1
    assert "not a mapping" in violations[0]


def test_main_exit_codes(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    good = _write_receipt(
        tmp_path, _receipt_data(contract_sha256=_contract_sha(contract))
    )
    assert main([str(good), "--contracts-dir", str(tmp_path / "contracts")]) == 0

    bad_dir = tmp_path / "drift" / "dod_receipts" / "OMN-13060" / "dod-002"
    bad_dir.mkdir(parents=True)
    bad = bad_dir / "command.yaml"
    bad.write_text(yaml.safe_dump(_receipt_data()))
    assert main([str(bad), "--contracts-dir", str(tmp_path / "contracts")]) == 1


def test_main_skips_missing_files(tmp_path: Path) -> None:
    assert (
        main(
            [
                str(tmp_path / "nope.yaml"),
                "--contracts-dir",
                str(tmp_path / "contracts"),
            ]
        )
        == 0
    )


def test_supersession_record_is_not_plain_receipt_hardened(tmp_path: Path) -> None:
    receipt_path = tmp_path / "command.supersede.0001.yaml"
    receipt_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "ticket_id": "OMN-13060",
                "supersedes": ("drift/dod_receipts/OMN-13060/dod-001/command.yaml"),
                "reason": "test supersession",
                "superseder": "codex-gpt-5",
                "created_at": POST_CUTOFF_TS,
                "tombstone": False,
                "replacement": _receipt_data(run_timestamp=POST_CUTOFF_TS),
            }
        )
    )

    assert check_receipt_file(receipt_path, tmp_path / "contracts") == []


def test_denylist_is_lowercase_canonical() -> None:
    assert all(v == v.strip().lower() for v in DENYLISTED_VERIFIERS)


# --- OMN-14411: per-entry contract hash binding -----------------------------
#
# check_receipt_hardening.py previously validated a receipt's contract binding
# against a WHOLE-FILE hash (compute_contract_sha256), even though the
# append-only gate (validator_occ_append_only) already validates PER-ENTRY
# (compute_contract_entry_sha256) and explicitly permits appending new
# dod_evidence items. Because the contract file's bytes change on every
# append, every previously-merged receipt's contract_sha256 went stale the
# moment anyone appended a new item — even though nothing about that
# receipt's own entry changed. ModelDodReceipt already carries
# contract_entry_sha256 (OMN-13888) precisely because it is append-stable;
# these tests prove the gate now binds to it correctly, and that doing so
# does not weaken the gate (edits, unknown entries, and missing hashes still
# fail closed).


def test_entry_hash_matching_passes(tmp_path: Path) -> None:
    """Baseline: a receipt bound via contract_entry_sha256 to its own,
    unmodified entry passes."""
    contract_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    contract = _write_entry_contract(tmp_path, contract_data)
    entry_hash = compute_contract_entry_sha256(contract_data, "dod-001")
    receipt = _write_receipt(tmp_path, _receipt_data(contract_entry_sha256=entry_hash))
    assert check_receipt_file(receipt, contract.parent) == []


def test_entry_hash_edited_entry_fails(tmp_path: Path) -> None:
    """Adversarial: if the attested dod_evidence entry is edited after the
    receipt was minted, the per-entry hash must change and the gate must
    FAIL — proving the new binding still detects tampering/drift on the
    entry it actually covers."""
    original_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    entry_hash = compute_contract_entry_sha256(original_data, "dod-001")
    receipt = _write_receipt(tmp_path, _receipt_data(contract_entry_sha256=entry_hash))

    edited_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    edited_data["dod_evidence"][0]["summary"] = "entry content changed"  # type: ignore[index]
    contract = _write_entry_contract(tmp_path, edited_data)

    violations = check_receipt_file(receipt, contract.parent)
    assert len(violations) == 1
    assert "contract_entry_sha256 mismatch" in violations[0]


def test_entry_hash_missing_entry_fails(tmp_path: Path) -> None:
    """Adversarial: a receipt pointing at a dod_evidence entry that does not
    exist in the contract (renamed/removed id) must FAIL, not silently pass
    because 'some hash was present'."""
    contract_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    # Hash a real entry so the value is a well-formed sha256:<hex>, then bind
    # the receipt to an evidence_item_id absent from the contract.
    entry_hash = compute_contract_entry_sha256(contract_data, "dod-001")
    contract = _write_entry_contract(tmp_path, contract_data)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            evidence_item_id="dod-999-does-not-exist",
            contract_entry_sha256=entry_hash,
        ),
    )
    violations = check_receipt_file(receipt, contract.parent)
    assert len(violations) == 1
    assert "not found in" in violations[0]


def test_missing_both_hash_fields_fails(tmp_path: Path) -> None:
    """Adversarial: a receipt carrying neither contract_sha256 nor
    contract_entry_sha256 must FAIL — no silent pass-through when both the
    legacy and current binding fields are absent."""
    contract_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    _write_entry_contract(tmp_path, contract_data)
    receipt = _write_receipt(tmp_path, _receipt_data())
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "missing contract_sha256" in violations[0]


def test_append_new_entry_does_not_invalidate_prior_receipt(tmp_path: Path) -> None:
    """Load-bearing regression test for OMN-14411.

    Mirrors the live incident on ``contracts/OMN-14400.yaml``: a receipt
    minted with BOTH ``contract_sha256`` (legacy whole-file) and
    ``contract_entry_sha256`` (OMN-13888 per-entry) set, both correct at
    mint time. Appending a brand-new, unrelated dod_evidence item is a
    supported, routine operation — ``validator_occ_append_only`` explicitly
    allows it — but it changes the contract file's bytes, so the whole-file
    hash goes stale regardless of which entry was appended. The per-entry
    hash of ``dod-001`` is untouched, because it folds in only that entry
    plus the immutable header (ticket_id, schema_version).

    Proven RED against pre-fix ``check_receipt_hardening.py``: pre-fix code
    validated only ``contract_sha256`` (``compute_contract_sha256``,
    whole-file), so after the append the gate FAILED with a
    'contract_sha256 mismatch' violation even though ``contract_entry_sha256``
    was present and still correct — reproducing the exact silent-rot failure
    mode from OMN-14411 (two independent actors hit this twice in 12
    minutes). Post-fix, ``contract_entry_sha256`` is authoritative when
    present, so the same receipt passes unchanged after the append.
    """
    original_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    entry_hash = compute_contract_entry_sha256(original_data, "dod-001")
    contract = _write_entry_contract(tmp_path, original_data)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            contract_entry_sha256=entry_hash,
        ),
    )
    # Sanity: passes against the contract as originally minted.
    assert check_receipt_file(receipt, contract.parent) == []

    # Now append a brand-new, unrelated dod_evidence item — the supported,
    # routine operation the append-only gate exists to allow.
    appended_data = copy.deepcopy(original_data)
    appended_data["dod_evidence"].append(  # type: ignore[attr-defined]
        {"id": "dod-002", "summary": "second item", "checks": []}
    )
    _write_entry_contract(tmp_path, appended_data)

    # The prior receipt, bound to dod-001's per-entry hash, must still pass:
    # its own entry did not change, only the file grew a sibling entry. Its
    # (legacy) contract_sha256 is now stale — that is exactly the condition
    # contract_entry_sha256 exists to make irrelevant.
    assert check_receipt_file(receipt, contract.parent) == []
