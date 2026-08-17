# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for model dump drift scanner (Check 6: MODEL_DUMP_DRIFT).

Ticket: OMN-7365
"""

from __future__ import annotations

from typing import Any

from onex_change_control.models.model_wire_schema_contract import (
    load_wire_schema_contract,
)
from onex_change_control.scanners.model_dump_drift import (
    check_model_dump_drift,
)


def _make_contract(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "topic": "onex.evt.test.event.v1",
        "schema_version": "1.0.0",
        "producer": {
            "repo": "test_repo",
            "file": "src/producer.py",
            "function": "emit",
        },
        "consumer": {
            "repo": "consumer_repo",
            "file": "src/consumer.py",
            "model": "ModelTestEvent",
        },
        "required_fields": [
            {"name": "id", "type": "uuid"},
            {"name": "name", "type": "string"},
            {"name": "score", "type": "float"},
        ],
        "optional_fields": [
            {"name": "metadata", "type": "object", "nullable": True},
        ],
    }
    base.update(overrides)
    return base


def _make_json_schema(
    properties: dict[str, dict[str, Any]],
    required: list[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal JSON schema dict."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "title": "TestModel",
    }
    if required:
        schema["required"] = required
    return schema


class TestCheckModelDumpDrift:
    """Check 6 scanner tests."""

    def test_no_violations_when_aligned(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        json_schema = _make_json_schema(
            {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "score": {"type": "number"},
                "metadata": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            }
        )
        violations = check_model_dump_drift(contract, json_schema)
        assert violations == []

    def test_undeclared_field_in_model(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        json_schema = _make_json_schema(
            {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "score": {"type": "number"},
                "metadata": {"type": "object"},
                "extra_field": {"type": "string"},
            }
        )
        violations = check_model_dump_drift(contract, json_schema)
        assert len(violations) == 1
        assert violations[0].field_name == "extra_field"
        assert "not declared in contract" in violations[0].detail

    def test_stale_contract_field(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        # Model is missing 'metadata' that contract declares
        json_schema = _make_json_schema(
            {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "score": {"type": "number"},
            }
        )
        violations = check_model_dump_drift(contract, json_schema)
        assert len(violations) == 1
        assert violations[0].field_name == "metadata"
        assert "missing from model" in violations[0].detail

    def test_type_mismatch(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        # score should be 'number' (float), but model says 'string'
        json_schema = _make_json_schema(
            {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "score": {"type": "string"},  # wrong type
                "metadata": {"type": "object"},
            }
        )
        violations = check_model_dump_drift(contract, json_schema)
        assert len(violations) == 1
        assert violations[0].field_name == "score"
        assert "Type mismatch" in violations[0].detail

    def test_integer_compatible_with_integer(self) -> None:
        """Integer type in contract should match integer in schema."""
        data = _make_contract(
            required_fields=[
                {"name": "count", "type": "integer"},
            ],
            optional_fields=[],
        )
        contract = load_wire_schema_contract(data)
        json_schema = _make_json_schema(
            {
                "count": {"type": "integer"},
            }
        )
        violations = check_model_dump_drift(contract, json_schema)
        assert violations == []

    def test_nullable_field_type_extraction(self) -> None:
        """Nullable fields use anyOf — should still extract the real type."""
        contract = load_wire_schema_contract(_make_contract())
        json_schema = _make_json_schema(
            {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "score": {"type": "number"},
                "metadata": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            }
        )
        violations = check_model_dump_drift(contract, json_schema)
        assert violations == []

    def test_multiple_violations(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        json_schema = _make_json_schema(
            {
                "id": {"type": "string"},
                # missing name, score
                "metadata": {"type": "object"},
                "extra1": {"type": "string"},
                "extra2": {"type": "integer"},
            }
        )
        violations = check_model_dump_drift(contract, json_schema)
        # 2 missing from model (name, score) + 2 undeclared (extra1, extra2)
        assert len(violations) == 4

    def test_ignores_pydantic_internal_fields(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        json_schema = _make_json_schema(
            {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "score": {"type": "number"},
                "metadata": {"type": "object"},
                "model_config": {"type": "object"},  # internal, should be ignored
            }
        )
        violations = check_model_dump_drift(contract, json_schema)
        assert violations == []
