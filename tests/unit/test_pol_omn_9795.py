# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""OMN-9795 Proof of Life: adversarial receipt chain for OMN-9762.

Asserts the full pipeline: contract read → receipt files exist →
receipts carry adversarial fields (verifier != runner, probe_stdout non-empty) →
receipt gate reports PASS (not ADVISORY).

The gate test is skipped on PyPI 0.40.0 (which lacks CLOSING_KEYWORD_PATTERN
and the adversarial ModelDodReceipt fields). The YAML-level assertions run on
all versions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

try:
    from omnibase_core.validation.receipt_gate import (  # type: ignore[attr-defined, unused-ignore]
        CLOSING_KEYWORD_PATTERN as _CLOSING_KEYWORD_PATTERN,  # noqa: F401
    )
    from omnibase_core.validation.receipt_gate import (
        validate_pr_receipts,
    )

    _HAS_ADVERSARIAL_GATE = True
except ImportError:
    _HAS_ADVERSARIAL_GATE = False

_adversarial_gate = pytest.mark.skipif(
    not _HAS_ADVERSARIAL_GATE,
    reason=(
        "omnibase_core does not expose CLOSING_KEYWORD_PATTERN — "
        "upgrade to the version that includes adversarial gate (OMN-9788)"
    ),
)

REPO_ROOT = Path(__file__).parent.parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"
RECEIPTS_DIR = REPO_ROOT / "drift" / "dod_receipts"
TICKET_ID = "OMN-9762"
DOD_IDS = ["dod-001", "dod-002"]


@pytest.mark.unit
def test_omn9762_contract_exists_and_has_command_checks() -> None:
    contract_path = CONTRACTS_DIR / f"{TICKET_ID}.yaml"
    assert contract_path.exists(), f"Contract not found: {contract_path}"
    with contract_path.open() as f:
        contract = yaml.safe_load(f)
    dod_evidence = contract.get("dod_evidence", [])
    assert len(dod_evidence) == 2, "Expected exactly 2 dod_evidence items"
    for item in dod_evidence:
        checks = item.get("checks", [])
        assert any(c.get("check_type") == "command" for c in checks), (
            f"dod_evidence item {item['id']} must have check_type=command"
        )


@pytest.mark.unit
@pytest.mark.parametrize("dod_id", DOD_IDS)
def test_omn9762_adversarial_receipt_exists(dod_id: str) -> None:
    receipt_path = RECEIPTS_DIR / TICKET_ID / dod_id / "command.yaml"
    assert receipt_path.exists(), f"Adversarial receipt missing: {receipt_path}"


@pytest.mark.unit
@pytest.mark.parametrize("dod_id", DOD_IDS)
def test_omn9762_adversarial_receipt_has_required_fields(dod_id: str) -> None:
    receipt_path = RECEIPTS_DIR / TICKET_ID / dod_id / "command.yaml"
    with receipt_path.open() as f:
        raw = yaml.safe_load(f)
    assert isinstance(raw, dict), f"Receipt at {receipt_path} is not a YAML mapping"
    assert raw.get("ticket_id") == TICKET_ID
    assert raw.get("evidence_item_id") == dod_id
    assert raw.get("check_type") == "command"
    assert raw.get("status") == "PASS"
    assert raw.get("schema_version") == "1.0.0"


@pytest.mark.unit
@pytest.mark.parametrize("dod_id", DOD_IDS)
def test_omn9762_adversarial_receipt_verifier_not_runner(dod_id: str) -> None:
    receipt_path = RECEIPTS_DIR / TICKET_ID / dod_id / "command.yaml"
    with receipt_path.open() as f:
        raw = yaml.safe_load(f)
    runner = raw.get("runner", "")
    verifier = raw.get("verifier", "")
    assert verifier, f"Receipt {dod_id} missing verifier field"
    assert runner, f"Receipt {dod_id} missing runner field"
    assert verifier.strip() != runner.strip(), (
        f"Self-attestation: verifier == runner == {runner!r} in {dod_id}"
    )


@pytest.mark.unit
@pytest.mark.parametrize("dod_id", DOD_IDS)
def test_omn9762_adversarial_receipt_has_probe_stdout(dod_id: str) -> None:
    receipt_path = RECEIPTS_DIR / TICKET_ID / dod_id / "command.yaml"
    with receipt_path.open() as f:
        raw = yaml.safe_load(f)
    probe_stdout = raw.get("probe_stdout", "")
    assert probe_stdout is not None
    assert str(probe_stdout).strip(), f"Receipt probe_stdout is empty for {dod_id}"


@pytest.mark.unit
@_adversarial_gate
def test_omn9762_receipt_gate_passes() -> None:
    pr_body = "Closes OMN-9762\n\nPoL verification for OMN-9795."
    result = validate_pr_receipts(  # type: ignore[name-defined]
        pr_body=pr_body,
        contracts_dir=CONTRACTS_DIR,
        receipts_dir=RECEIPTS_DIR,
    )
    assert result.passed, f"Receipt gate FAILED: {result.message}"
    assert "PASS" in result.message
