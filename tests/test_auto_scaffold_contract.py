# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for scripts/auto_scaffold_contract.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from scripts.auto_scaffold_contract import (
    _build_contract_yaml,
    _build_receipt_stub,
    _detect_seam_signals,
    _extract_dod_items,
    generate_stubs,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestDetectSeamSignals:
    def test_no_signals(self) -> None:
        assert _detect_seam_signals("Refactor the logging module") == []

    def test_kafka_signal(self) -> None:
        result = _detect_seam_signals("Add Kafka consumer for events")
        assert "topics" in result

    def test_multiple_signals(self) -> None:
        result = _detect_seam_signals("Add Kafka topic and REST endpoint")
        assert "topics" in result
        assert "public_api" in result

    def test_case_insensitive(self) -> None:
        result = _detect_seam_signals("KAFKA TOPIC PROTOCOL")
        assert "topics" in result
        assert "protocols" in result

    def test_deduplication(self) -> None:
        result = _detect_seam_signals("kafka topic consumer producer")
        assert result.count("topics") == 1


class TestExtractDodItems:
    def test_no_checkboxes(self) -> None:
        assert _extract_dod_items("No checkboxes here") == []

    def test_single_checkbox(self) -> None:
        desc = "## DoD\n- [ ] Tests pass\n- [ ] CI green"
        result = _extract_dod_items(desc)
        assert len(result) == 2
        assert result[0] == "Tests pass"
        assert result[1] == "CI green"

    def test_mixed_content(self) -> None:
        desc = "## Summary\nSome text\n\n## DoD\n- [ ] Item 1\nParagraph\n- [ ] Item 2"
        result = _extract_dod_items(desc)
        assert len(result) == 2


class TestBuildContractYaml:
    def test_minimal_contract_validates(self) -> None:
        yaml_str = _build_contract_yaml(
            ticket_id="OMN-9999",
            title="Test ticket",
            description="",
            dod_items=[],
        )
        data = yaml.safe_load(yaml_str)
        assert data["ticket_id"] == "OMN-9999"
        assert data["title"] == "Test ticket"
        assert data["is_seam_ticket"] is False
        assert data["interface_change"] is False
        assert data["interfaces_touched"] == []
        assert data["contract_completeness"] == "STUB"

    def test_seam_contract(self) -> None:
        yaml_str = _build_contract_yaml(
            ticket_id="OMN-8888",
            title="Add Kafka topic for events",
            description="",
            dod_items=[],
        )
        data = yaml.safe_load(yaml_str)
        assert data["is_seam_ticket"] is True
        assert data["interface_change"] is True
        assert "topics" in data["interfaces_touched"]

    def test_dod_items_enriched(self) -> None:
        yaml_str = _build_contract_yaml(
            ticket_id="OMN-7777",
            title="Test",
            description="",
            dod_items=["Tests pass", "Docs updated"],
        )
        data = yaml.safe_load(yaml_str)
        assert data["contract_completeness"] == "ENRICHED"
        assert len(data["dod_evidence"]) == 2
        assert data["dod_evidence"][0]["source"] == "linear"
        assert data["dod_evidence"][0]["linear_dod_text"] == "Tests pass"

    def test_default_dod_when_no_items(self) -> None:
        yaml_str = _build_contract_yaml(
            ticket_id="OMN-6666",
            title="Test",
            description="",
            dod_items=[],
        )
        data = yaml.safe_load(yaml_str)
        assert len(data["dod_evidence"]) == 2
        assert data["dod_evidence"][0]["id"] == "dod-001"
        assert data["dod_evidence"][1]["id"] == "dod-002"


class TestBuildReceiptStub:
    def test_pending_status(self) -> None:
        stub = _build_receipt_stub(
            ticket_id="OMN-9999",
            dod_id="dod-001",
            check_type="command",
            check_value="uv run pytest",
        )
        data = yaml.safe_load(stub)
        assert data["status"] == "PENDING"
        assert data["ticket_id"] == "OMN-9999"
        assert data["evidence_item_id"] == "dod-001"


class TestGenerateStubs:
    def test_generates_contract_and_receipts(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        drift_dir = tmp_path / "drift" / "dod_receipts"
        drift_dir.mkdir(parents=True)

        paths = generate_stubs(
            ticket_id="OMN-1234",
            title="Test ticket",
            description="## DoD\n- [ ] Tests pass\n- [ ] CI green",
            repo_root=tmp_path,
        )

        contract_path = contracts_dir / "OMN-1234.yaml"
        assert contract_path.exists()
        assert contract_path in paths

        data = yaml.safe_load(contract_path.read_text())
        assert data["ticket_id"] == "OMN-1234"
        assert len(data["dod_evidence"]) == 2

        receipt_dir = drift_dir / "OMN-1234"
        assert receipt_dir.exists()

        receipt_path = receipt_dir / "dod-001" / "command.yaml"
        assert receipt_path.exists()
        receipt_data = yaml.safe_load(receipt_path.read_text())
        assert receipt_data["status"] == "PENDING"

    def test_idempotency(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        drift_dir = tmp_path / "drift" / "dod_receipts"
        drift_dir.mkdir(parents=True)

        generate_stubs(
            ticket_id="OMN-5678",
            title="Idempotent test",
            description="",
            repo_root=tmp_path,
        )

        paths_second = generate_stubs(
            ticket_id="OMN-5678",
            title="Idempotent test",
            description="",
            repo_root=tmp_path,
        )

        contract_path = contracts_dir / "OMN-5678.yaml"
        content = contract_path.read_text()
        data = yaml.safe_load(content)
        assert data["title"] == "Idempotent test"

        assert len(paths_second) == 0 or all("skip" not in str(p) for p in paths_second)

    def test_invalid_ticket_id(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        with pytest.raises(SystemExit):
            generate_stubs(
                ticket_id="INVALID",
                title="test",
                description="",
                repo_root=tmp_path,
            )

    def test_case_normalization(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        drift_dir = tmp_path / "drift" / "dod_receipts"
        drift_dir.mkdir(parents=True)

        generate_stubs(
            ticket_id="omn-9999",
            title="Case test",
            description="",
            repo_root=tmp_path,
        )

        assert (contracts_dir / "OMN-9999.yaml").exists()
        assert (drift_dir / "OMN-9999").exists()

    def test_no_dod_generates_default_receipts(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        drift_dir = tmp_path / "drift" / "dod_receipts"
        drift_dir.mkdir(parents=True)

        generate_stubs(
            ticket_id="OMN-1111",
            title="No DoD",
            description="Just a description",
            repo_root=tmp_path,
        )

        receipt_1 = drift_dir / "OMN-1111" / "dod-001" / "test_passes.yaml"
        receipt_2 = drift_dir / "OMN-1111" / "dod-002" / "command.yaml"
        assert receipt_1.exists()
        assert receipt_2.exists()
