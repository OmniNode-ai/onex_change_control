# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for wire schema compliance scanner (Check 5: WIRE_SCHEMA_MISMATCH).

Ticket: OMN-7362
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from onex_change_control.models.model_wire_schema_contract import (
    load_wire_schema_contract,
)
from onex_change_control.scanners.wire_schema_compliance import (
    check_wire_schema_mismatch,
    discover_wire_schema_contracts,
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


class TestCheckWireSchemaMismatch:
    """Check 5 scanner tests."""

    def test_no_violations_when_fields_match(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        producer_fields = {"id", "name", "score", "metadata"}
        consumer_fields = {"id", "name", "score", "metadata"}
        violations = check_wire_schema_mismatch(
            contract,
            producer_fields=producer_fields,
            consumer_fields=consumer_fields,
        )
        assert violations == []

    def test_missing_required_field_in_producer(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        producer_fields = {"id", "name"}  # missing 'score'
        violations = check_wire_schema_mismatch(
            contract, producer_fields=producer_fields
        )
        assert len(violations) == 1
        assert violations[0].field_name == "score"
        assert violations[0].side == "producer"
        assert "missing from producer" in violations[0].detail

    def test_missing_required_field_in_consumer(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        consumer_fields = {"id", "score"}  # missing 'name'
        violations = check_wire_schema_mismatch(
            contract, consumer_fields=consumer_fields
        )
        assert len(violations) == 1
        assert violations[0].field_name == "name"
        assert violations[0].side == "consumer"

    def test_undeclared_field_in_producer(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        producer_fields = {"id", "name", "score", "metadata", "extra_field"}
        violations = check_wire_schema_mismatch(
            contract, producer_fields=producer_fields
        )
        assert len(violations) == 1
        assert violations[0].field_name == "extra_field"
        assert "not declared in contract" in violations[0].detail

    def test_undeclared_field_in_consumer(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        consumer_fields = {"id", "name", "score", "metadata", "bonus"}
        violations = check_wire_schema_mismatch(
            contract, consumer_fields=consumer_fields
        )
        assert len(violations) == 1
        assert violations[0].field_name == "bonus"
        assert violations[0].side == "consumer"

    def test_renamed_field_accepted_for_producer(self) -> None:
        data = _make_contract(
            renamed_fields=[
                {
                    "producer_name": "confidence",
                    "canonical_name": "score",
                    "shim_status": "active",
                    "retirement_ticket": "OMN-1234",
                },
            ]
        )
        contract = load_wire_schema_contract(data)
        # Producer uses old name 'confidence' instead of canonical 'score'
        producer_fields = {"id", "name", "confidence", "metadata"}
        violations = check_wire_schema_mismatch(
            contract, producer_fields=producer_fields
        )
        assert violations == []

    def test_skip_when_no_fields_provided(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        violations = check_wire_schema_mismatch(contract)
        assert violations == []

    def test_both_sides_checked(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        producer_fields = {"id", "name"}  # missing score
        consumer_fields = {"id", "score"}  # missing name
        violations = check_wire_schema_mismatch(
            contract,
            producer_fields=producer_fields,
            consumer_fields=consumer_fields,
        )
        assert len(violations) == 2
        sides = {v.side for v in violations}
        assert sides == {"producer", "consumer"}


class TestDiscoverWireSchemaContracts:
    """Contract discovery tests."""

    def test_discover_contracts_in_directory(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        data = _make_contract()
        (contracts_dir / "test_event_v1.yaml").write_text(yaml.dump(data))
        # Write a non-contract YAML that should be skipped
        (contracts_dir / "other_v1.yaml").write_text(yaml.dump({"key": "value"}))

        results = discover_wire_schema_contracts([contracts_dir])
        assert len(results) == 1
        assert results[0][1].topic == "onex.evt.test.event.v1"

    def test_discover_skips_nonexistent_dirs(self) -> None:
        results = discover_wire_schema_contracts([Path("/nonexistent/path")])
        assert results == []

    def test_discover_skips_invalid_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "bad_v1.yaml").write_text("not: valid: yaml: [")
        results = discover_wire_schema_contracts([tmp_path])
        assert results == []
