# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Model Dump Drift Scanner (Check 6: MODEL_DUMP_DRIFT).

Detect when a Pydantic model's model_json_schema() output drifts from its
wire schema contract declaration. Catches cases where a developer adds a
field to the model but forgets to update the contract.

Ticket: OMN-7365
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from onex_change_control.enums.enum_compliance_violation import EnumComplianceViolation

if TYPE_CHECKING:
    from onex_change_control.models.model_wire_schema_contract import (
        ModelWireSchemaContract,
    )

logger = logging.getLogger(__name__)

# Mapping from wire schema field types to JSON schema types
_WIRE_TO_JSON_SCHEMA_TYPE: dict[str, set[str]] = {
    "uuid": {"string"},
    "string": {"string"},
    "float": {"number"},
    "integer": {"integer", "number"},
    "datetime": {"string"},
    "boolean": {"boolean"},
    "array": {"array"},
    "object": {"object"},
}

# Internal Pydantic fields to ignore
_PYDANTIC_INTERNAL: frozenset[str] = frozenset(
    {"model_config", "model_fields", "model_computed_fields"}
)


class ModelDumpDriftViolation:
    """A single model dump drift violation."""

    __slots__ = ("detail", "field_name", "topic", "violation_type")

    def __init__(
        self,
        topic: str,
        field_name: str,
        detail: str,
        violation_type: EnumComplianceViolation = (
            EnumComplianceViolation.MODEL_DUMP_DRIFT
        ),
    ) -> None:
        self.topic = topic
        self.field_name = field_name
        self.detail = detail
        self.violation_type = violation_type

    def __repr__(self) -> str:
        return (
            f"ModelDumpDriftViolation({self.topic}, {self.field_name}: {self.detail})"
        )


def _get_json_schema_type(prop: dict[str, Any]) -> str | None:
    """Extract the type from a JSON schema property definition."""
    if "type" in prop:
        return str(prop["type"])
    # Handle anyOf (nullable fields)
    if "anyOf" in prop:
        for item in prop["anyOf"]:
            if isinstance(item, dict) and "type" in item:
                t = str(item["type"])
                if t != "null":
                    return t
    return None


def check_model_dump_drift(
    contract: ModelWireSchemaContract,
    model_json_schema: dict[str, Any],
) -> list[ModelDumpDriftViolation]:
    """Check 6: Compare a wire schema contract against a model's JSON schema.

    Args:
        contract: The parsed wire schema contract.
        model_json_schema: The output of SomeModel.model_json_schema().

    Returns:
        List of model dump drift violations found.
    """
    violations: list[ModelDumpDriftViolation] = []
    props = model_json_schema.get("properties", {})
    model_field_names = set(props.keys()) - _PYDANTIC_INTERNAL
    contract_all = contract.all_field_names

    # Check: field in model but not in contract (undeclared field)
    for field_name in sorted(model_field_names - contract_all):
        violations.append(
            ModelDumpDriftViolation(
                topic=contract.topic,
                field_name=field_name,
                detail=(
                    f"Field '{field_name}' in model schema but not declared in contract"
                ),
            )
        )

    # Check: field in contract but not in model (stale contract declaration)
    for field_name in sorted(contract_all - model_field_names):
        violations.append(
            ModelDumpDriftViolation(
                topic=contract.topic,
                field_name=field_name,
                detail=(
                    f"Field '{field_name}' declared in contract "
                    f"but missing from model schema"
                ),
            )
        )

    # Check: type mismatches for fields present in both
    contract_fields = {f.name: f.type for f in contract.required_fields}
    contract_fields.update({f.name: f.type for f in contract.optional_fields})

    for field_name in sorted(model_field_names & contract_all):
        if field_name not in props or field_name not in contract_fields:
            continue
        json_type = _get_json_schema_type(props[field_name])
        wire_type = contract_fields[field_name]
        if json_type is None:
            continue
        expected_json_types = _WIRE_TO_JSON_SCHEMA_TYPE.get(str(wire_type), set())
        if json_type not in expected_json_types:
            violations.append(
                ModelDumpDriftViolation(
                    topic=contract.topic,
                    field_name=field_name,
                    detail=(
                        f"Type mismatch for '{field_name}': "
                        f"contract says '{wire_type}', model schema says '{json_type}'"
                    ),
                )
            )

    return violations
