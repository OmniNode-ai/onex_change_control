# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for DoD evidence schema on ModelTicketContract.

Validates that DodCheck, DodEvidenceItem, and the dod_evidence[] field
on ModelTicketContract parse, validate, and round-trip correctly.
"""

import pytest
import yaml
from pydantic import ValidationError

from onex_change_control.models.model_ticket_contract import (
    ModelDodCheck,
    ModelDodEvidenceItem,
    ModelTicketContract,
)


def _minimal_contract(**overrides: object) -> dict[str, object]:
    """Return minimal valid ModelTicketContract data with optional overrides."""
    base: dict[str, object] = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-5168",
        "summary": "Test ticket",
        "is_seam_ticket": False,
        "interface_change": False,
        "emergency_bypass": {"enabled": False},
    }
    base.update(overrides)
    return base


class TestContractWithoutDodEvidence:
    """Existing contracts with no dod_evidence field still parse."""

    def test_contract_without_dod_evidence_is_valid(self) -> None:
        data = _minimal_contract()
        contract = ModelTicketContract.model_validate(data)
        assert contract.dod_evidence == []

    def test_contract_with_empty_dod_evidence_is_valid(self) -> None:
        data = _minimal_contract(dod_evidence=[])
        contract = ModelTicketContract.model_validate(data)
        assert contract.dod_evidence == []


class TestDodEvidenceItemValidation:
    """DodEvidenceItem requires id and description."""

    def test_dod_evidence_entry_requires_id_and_description(self) -> None:
        with pytest.raises(ValidationError):
            ModelDodEvidenceItem.model_validate(
                {"checks": [{"check_type": "command", "check_value": "echo ok"}]}
            )

    def test_dod_evidence_entry_requires_checks(self) -> None:
        with pytest.raises(ValidationError):
            ModelDodEvidenceItem.model_validate(
                {"id": "dod-001", "description": "Must have checks"}
            )

    def test_valid_dod_evidence_item(self) -> None:
        item = ModelDodEvidenceItem.model_validate(
            {
                "id": "dod-001",
                "description": "Tests added",
                "checks": [{"check_type": "test_exists", "check_value": "tests/"}],
            }
        )
        assert item.id == "dod-001"
        assert item.source == "generated"
        assert item.status == "pending"
        assert item.linear_dod_text is None
        assert item.evidence_artifact is None


class TestDodCheckTypes:
    """Validate all 6 check types."""

    @pytest.mark.parametrize(
        "check_type",
        ["test_exists", "test_passes", "file_exists", "grep", "command", "endpoint"],
    )
    def test_dod_evidence_check_types(self, check_type: str) -> None:
        check = ModelDodCheck.model_validate(
            {"check_type": check_type, "check_value": "some_value"}
        )
        assert check.check_type == check_type

    def test_invalid_check_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelDodCheck.model_validate(
                {"check_type": "invalid_type", "check_value": "x"}
            )

    def test_check_value_as_dict(self) -> None:
        check = ModelDodCheck.model_validate(
            {
                "check_type": "grep",
                "check_value": {"pattern": "def test_", "path": "tests/"},
            }
        )
        assert isinstance(check.check_value, dict)
        assert check.check_value["pattern"] == "def test_"


class TestDodEvidenceStatusLifecycle:
    """Status transitions: pending -> verified / failed / skipped."""

    @pytest.mark.parametrize("status", ["pending", "verified", "failed", "skipped"])
    def test_dod_evidence_status_lifecycle(self, status: str) -> None:
        item = ModelDodEvidenceItem.model_validate(
            {
                "id": "dod-001",
                "description": "Test item",
                "checks": [{"check_type": "command", "check_value": "true"}],
                "status": status,
            }
        )
        assert item.status == status

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelDodEvidenceItem.model_validate(
                {
                    "id": "dod-001",
                    "description": "Test item",
                    "checks": [{"check_type": "command", "check_value": "true"}],
                    "status": "invalid",
                }
            )


class TestDodEvidenceSource:
    """Source field validation."""

    @pytest.mark.parametrize("source", ["linear", "manual", "generated"])
    def test_valid_sources(self, source: str) -> None:
        item = ModelDodEvidenceItem.model_validate(
            {
                "id": "dod-001",
                "description": "Test item",
                "source": source,
                "checks": [{"check_type": "command", "check_value": "true"}],
            }
        )
        assert item.source == source

    def test_invalid_source_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelDodEvidenceItem.model_validate(
                {
                    "id": "dod-001",
                    "description": "Test item",
                    "source": "unknown",
                    "checks": [{"check_type": "command", "check_value": "true"}],
                }
            )


class TestDodEvidenceRoundtripYaml:
    """Write to YAML, read back, fields preserved."""

    def test_dod_evidence_roundtrip_yaml(self) -> None:
        data = _minimal_contract(
            dod_evidence=[
                {
                    "id": "dod-001",
                    "description": "Unit tests exist and pass",
                    "source": "linear",
                    "linear_dod_text": "Unit tests added and passing",
                    "checks": [
                        {"check_type": "test_exists", "check_value": "tests/unit/"},
                        {
                            "check_type": "test_passes",
                            "check_value": "pytest tests/unit/ -v",
                        },
                    ],
                    "status": "verified",
                    "evidence_artifact": ".evidence/OMN-5168/dod_report.json",
                },
                {
                    "id": "dod-002",
                    "description": "Config file created",
                    "checks": [
                        {
                            "check_type": "file_exists",
                            "check_value": "config/*.yaml",
                        }
                    ],
                    "status": "pending",
                },
            ]
        )

        contract = ModelTicketContract.model_validate(data)
        yaml_str = yaml.dump(contract.model_dump(mode="json"), default_flow_style=False)
        loaded = yaml.safe_load(yaml_str)
        roundtripped = ModelTicketContract.model_validate(loaded)

        assert len(roundtripped.dod_evidence) == 2

        item1 = roundtripped.dod_evidence[0]
        assert item1.id == "dod-001"
        assert item1.description == "Unit tests exist and pass"
        assert item1.source == "linear"
        assert item1.linear_dod_text == "Unit tests added and passing"
        assert len(item1.checks) == 2
        assert item1.checks[0].check_type == "test_exists"
        assert item1.checks[1].check_type == "test_passes"
        assert item1.status == "verified"
        assert item1.evidence_artifact == ".evidence/OMN-5168/dod_report.json"

        item2 = roundtripped.dod_evidence[1]
        assert item2.id == "dod-002"
        assert item2.source == "generated"
        assert item2.status == "pending"
        assert item2.linear_dod_text is None
        assert item2.evidence_artifact is None


class TestDodEvidenceOnContract:
    """Integration tests for dod_evidence on ModelTicketContract."""

    def test_contract_with_full_dod_evidence(self) -> None:
        data = _minimal_contract(
            dod_evidence=[
                {
                    "id": "dod-001",
                    "description": "API endpoint responds",
                    "source": "manual",
                    "checks": [
                        {
                            "check_type": "endpoint",
                            "check_value": "http://localhost:8000/health",
                        }
                    ],
                },
                {
                    "id": "dod-002",
                    "description": "mypy passes",
                    "checks": [
                        {
                            "check_type": "command",
                            "check_value": "uv run mypy src/ --strict",
                        }
                    ],
                },
                {
                    "id": "dod-003",
                    "description": "Pattern found in source",
                    "checks": [
                        {
                            "check_type": "grep",
                            "check_value": {
                                "pattern": "class ModelDodCheck",
                                "path": "src/",
                            },
                        }
                    ],
                },
            ]
        )
        contract = ModelTicketContract.model_validate(data)
        assert len(contract.dod_evidence) == 3
        assert contract.dod_evidence[0].checks[0].check_type == "endpoint"
        assert contract.dod_evidence[1].checks[0].check_type == "command"
        assert contract.dod_evidence[2].checks[0].check_type == "grep"

    def test_immutability_of_dod_evidence(self) -> None:
        """Frozen model prevents mutation."""
        data = _minimal_contract(
            dod_evidence=[
                {
                    "id": "dod-001",
                    "description": "Test",
                    "checks": [{"check_type": "command", "check_value": "true"}],
                }
            ]
        )
        contract = ModelTicketContract.model_validate(data)
        with pytest.raises(ValidationError):
            contract.dod_evidence[0].status = "verified"  # type: ignore[misc]
