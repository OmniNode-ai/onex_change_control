# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for ModelWireSchemaContract (OMN-7357)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from onex_change_control.models.model_wire_schema_contract import (
    load_wire_schema_contract,
)


def _minimal_contract(**overrides: object) -> dict[str, Any]:
    """Build a minimal valid wire schema contract dict."""
    base: dict[str, Any] = {
        "topic": "onex.evt.test.event.v1",
        "schema_version": "1.0.0",
        "producer": {
            "repo": "test_repo",
            "file": "src/test_repo/producer.py",
            "function": "emit",
        },
        "consumer": {
            "repo": "consumer_repo",
            "file": "src/consumer_repo/consumer.py",
            "model": "ModelTestEvent",
        },
        "required_fields": [
            {"name": "id", "type": "uuid", "description": "Primary key"},
        ],
    }
    base.update(overrides)
    return base


class TestModelWireSchemaContractValid:
    """Valid contract parsing."""

    def test_minimal_contract(self) -> None:
        contract = load_wire_schema_contract(_minimal_contract())
        assert contract.topic == "onex.evt.test.event.v1"
        assert contract.schema_version == "1.0.0"
        assert len(contract.required_fields) == 1
        assert contract.required_fields[0].name == "id"

    def test_full_contract_with_optional_fields(self) -> None:
        data = _minimal_contract(
            optional_fields=[
                {"name": "domain", "type": "string", "nullable": True},
                {"name": "metadata", "type": "object", "nullable": True},
            ]
        )
        contract = load_wire_schema_contract(data)
        assert len(contract.optional_fields) == 2
        assert contract.optional_field_names == {"domain", "metadata"}

    def test_contract_with_renamed_fields(self) -> None:
        data = _minimal_contract(
            renamed_fields=[
                {
                    "producer_name": "confidence",
                    "canonical_name": "confidence_score",
                    "shim_status": "active",
                    "retirement_ticket": "OMN-1234",
                },
            ]
        )
        contract = load_wire_schema_contract(data)
        assert len(contract.renamed_fields) == 1
        assert contract.active_renamed_fields == {"confidence": "confidence_score"}

    def test_contract_with_collapsed_fields(self) -> None:
        data = _minimal_contract(
            collapsed_fields=[
                {"name": "routing_method", "note": "Collapsed into metadata"},
            ]
        )
        contract = load_wire_schema_contract(data)
        assert len(contract.collapsed_fields) == 1

    def test_contract_with_ci_gate(self) -> None:
        data = _minimal_contract(
            ci_gate={
                "test_file": "tests/test_handshake.py",
                "test_class": "TestHandshake",
            }
        )
        contract = load_wire_schema_contract(data)
        assert contract.ci_gate is not None
        assert contract.ci_gate.test_file == "tests/test_handshake.py"

    def test_all_field_names_property(self) -> None:
        data = _minimal_contract(
            optional_fields=[
                {"name": "extra", "type": "string"},
            ]
        )
        contract = load_wire_schema_contract(data)
        assert contract.all_field_names == {"id", "extra"}

    def test_required_field_with_constraints(self) -> None:
        data = _minimal_contract(
            required_fields=[
                {
                    "name": "score",
                    "type": "float",
                    "constraints": {"ge": 0.0, "le": 1.0},
                    "description": "Score between 0 and 1",
                },
            ]
        )
        contract = load_wire_schema_contract(data)
        field = contract.required_fields[0]
        assert field.constraints is not None
        assert field.constraints.ge == 0.0
        assert field.constraints.le == 1.0

    def test_consumer_with_ingest_shim(self) -> None:
        data = _minimal_contract()
        data["consumer"]["ingest_shim"] = "ModelTestEventIngest"
        data["consumer"]["ingest_shim_retirement_ticket"] = "OMN-5678"
        contract = load_wire_schema_contract(data)
        assert contract.consumer.ingest_shim == "ModelTestEventIngest"

    def test_retired_renamed_field_excluded_from_active(self) -> None:
        data = _minimal_contract(
            renamed_fields=[
                {
                    "producer_name": "old_name",
                    "canonical_name": "new_name",
                    "shim_status": "retired",
                },
            ]
        )
        contract = load_wire_schema_contract(data)
        assert contract.active_renamed_fields == {}


class TestModelWireSchemaContractInvalid:
    """Invalid contract rejection."""

    def test_missing_required_fields_section(self) -> None:
        data = _minimal_contract()
        del data["required_fields"]
        with pytest.raises(ValidationError):
            load_wire_schema_contract(data)

    def test_missing_topic(self) -> None:
        data = _minimal_contract()
        del data["topic"]
        with pytest.raises(ValidationError):
            load_wire_schema_contract(data)

    def test_missing_producer(self) -> None:
        data = _minimal_contract()
        del data["producer"]
        with pytest.raises(ValidationError):
            load_wire_schema_contract(data)

    def test_missing_consumer(self) -> None:
        data = _minimal_contract()
        del data["consumer"]
        with pytest.raises(ValidationError):
            load_wire_schema_contract(data)

    def test_duplicate_required_field_names(self) -> None:
        data = _minimal_contract(
            required_fields=[
                {"name": "id", "type": "uuid"},
                {"name": "id", "type": "string"},
            ]
        )
        with pytest.raises(ValueError, match="Duplicate required_fields"):
            load_wire_schema_contract(data)

    def test_duplicate_optional_field_names(self) -> None:
        data = _minimal_contract(
            optional_fields=[
                {"name": "domain", "type": "string"},
                {"name": "domain", "type": "object"},
            ]
        )
        with pytest.raises(ValueError, match="Duplicate optional_fields"):
            load_wire_schema_contract(data)

    def test_field_in_both_required_and_optional(self) -> None:
        data = _minimal_contract(
            required_fields=[
                {"name": "id", "type": "uuid"},
            ],
            optional_fields=[
                {"name": "id", "type": "string"},
            ],
        )
        with pytest.raises(ValueError, match="both required and optional"):
            load_wire_schema_contract(data)

    def test_invalid_field_type(self) -> None:
        data = _minimal_contract(
            required_fields=[
                {"name": "id", "type": "invalid_type"},
            ]
        )
        with pytest.raises(ValidationError):
            load_wire_schema_contract(data)

    def test_empty_required_fields_is_valid(self) -> None:
        """An empty required_fields list is valid (must be present but can be empty)."""
        data = _minimal_contract(required_fields=[])
        contract = load_wire_schema_contract(data)
        assert len(contract.required_fields) == 0


class TestRoutingDecisionV1Compatibility:
    """Validate that the model can parse the existing routing_decision_v1.yaml."""

    def test_parse_routing_decision_contract(self) -> None:
        env_root = os.environ.get("OMNI_HOME")
        candidates: list[Path] = []
        if env_root:
            candidates.append(Path(env_root) / "omnibase_infra")
        candidates.extend(
            [
                Path("/Volumes/PRO-G40/Code/omni_home/omnibase_infra"),
                Path("/Users/jonah/Code/omni_home/omnibase_infra"),
            ]
        )
        yaml_path: Path | None = None
        for candidate in candidates:
            p = (
                candidate
                / "src/omnibase_infra/services/observability/agent_actions/contracts"
                / "routing_decision_v1.yaml"
            )
            if p.exists():
                yaml_path = p
                break

        if yaml_path is None:
            pytest.skip(
                "routing_decision_v1.yaml not available (set OMNI_HOME env var)"
            )

        with yaml_path.open() as f:
            data = yaml.safe_load(f)

        contract = load_wire_schema_contract(data)
        assert contract.topic == "onex.evt.omniclaude.routing-decision.v1"
        assert contract.schema_version == "1.0.0"
        assert len(contract.required_fields) == 5
        assert contract.required_field_names == {
            "id",
            "correlation_id",
            "selected_agent",
            "confidence_score",
            "created_at",
        }
        assert "domain" in contract.optional_field_names
        assert len(contract.renamed_fields) == 4
        assert contract.active_renamed_fields["confidence"] == "confidence_score"
        assert len(contract.collapsed_fields) == 5
        assert contract.ci_gate is not None
