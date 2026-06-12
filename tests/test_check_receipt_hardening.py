# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the receipt hardening gate (OMN-13060, retro A-5)."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import yaml

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


def _contract_sha(contract_path: Path) -> str:
    return f"sha256:{hashlib.sha256(contract_path.read_bytes()).hexdigest()}"


def _write_contract(tmp_path: Path, ticket: str = "OMN-13060") -> Path:
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    contract_path = contracts_dir / f"{ticket}.yaml"
    contract_path.write_text(CONTRACT_BODY)
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


def test_denylist_is_lowercase_canonical() -> None:
    assert all(v == v.strip().lower() for v in DENYLISTED_VERIFIERS)
