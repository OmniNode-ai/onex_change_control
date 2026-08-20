# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Wire Schema Compliance Scanner (Check 5: WIRE_SCHEMA_MISMATCH).

Cross-repo publisher vs consumer field matching. For each wire schema
contract YAML, verify that producer and consumer models align with the
contract's declared required and optional fields.

Ticket: OMN-7362
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any

import yaml

from onex_change_control.enums.enum_compliance_violation import EnumComplianceViolation
from onex_change_control.models.model_wire_schema_contract import (
    ModelWireSchemaContract,
    load_wire_schema_contract,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Internal Pydantic fields to exclude from compliance checks
_PYDANTIC_INTERNAL: frozenset[str] = frozenset(
    {"model_config", "model_fields", "model_computed_fields"}
)


class ModelWireSchemaViolation:
    """A single wire schema compliance violation."""

    __slots__ = ("detail", "field_name", "side", "topic", "violation_type")

    def __init__(
        self,
        topic: str,
        field_name: str,
        side: str,
        detail: str,
        violation_type: EnumComplianceViolation = (
            EnumComplianceViolation.WIRE_SCHEMA_MISMATCH
        ),
    ) -> None:
        self.topic = topic
        self.field_name = field_name
        self.side = side
        self.detail = detail
        self.violation_type = violation_type

    def __repr__(self) -> str:
        return (
            f"WireSchemaViolation("
            f"{self.topic}, {self.field_name}, "
            f"{self.side}: {self.detail})"
        )


def _load_contract_yaml(path: Path) -> dict[str, Any] | None:
    """Load and return a YAML file, returning None if invalid."""
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return None
    else:
        if not isinstance(data, dict):
            return None
        return data


def _is_wire_schema_contract(data: dict[str, Any]) -> bool:
    """Check if a YAML dict looks like a wire schema contract."""
    return (
        "topic" in data
        and "schema_version" in data
        and "producer" in data
        and "consumer" in data
        and "required_fields" in data
    )


def discover_wire_schema_contracts(
    search_dirs: list[Path],
) -> list[tuple[Path, ModelWireSchemaContract]]:
    """Discover and parse all wire schema contract YAMLs in the given directories.

    Searches for *_v*.yaml files that match the wire schema contract structure.
    """
    contracts: list[tuple[Path, ModelWireSchemaContract]] = []
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for yaml_path in sorted(search_dir.rglob("*_v*.yaml")):
            data = _load_contract_yaml(yaml_path)
            if data is None or not _is_wire_schema_contract(data):
                continue
            try:
                contract = load_wire_schema_contract(data)
                contracts.append((yaml_path, contract))
            except (ValueError, TypeError, KeyError):
                logger.warning(
                    "Failed to parse wire schema contract: %s",
                    yaml_path,
                )
    return contracts


def _resolve_model_fields(model_fqn: str) -> set[str] | None:
    """Attempt to import a Pydantic model and extract field names.

    Uses model_json_schema() to resolve field names.
    Returns None if the model cannot be resolved (e.g. not installed).
    """
    parts = model_fqn.rsplit(".", 1)
    if len(parts) != 2:
        return None
    module_path, class_name = parts
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        schema = cls.model_json_schema()
        props = schema.get("properties", {})
        return set(props.keys())
    except (ImportError, AttributeError, TypeError):
        return None


def check_wire_schema_mismatch(
    contract: ModelWireSchemaContract,
    producer_model_fqn: str | None = None,
    consumer_model_fqn: str | None = None,
    producer_fields: set[str] | None = None,
    consumer_fields: set[str] | None = None,
) -> list[ModelWireSchemaViolation]:
    """Check 5: Compare a wire schema contract against producer/consumer model fields.

    Fields can be provided directly or resolved from fully-qualified model names.
    If neither is available, the check is skipped for that side.

    Args:
        contract: The parsed wire schema contract.
        producer_model_fqn: Fully qualified name of the producer Pydantic model.
        consumer_model_fqn: Fully qualified name of the consumer Pydantic model.
        producer_fields: Pre-resolved producer field names (overrides FQN resolution).
        consumer_fields: Pre-resolved consumer field names (overrides FQN resolution).

    Returns:
        List of wire schema violations found.
    """
    violations: list[ModelWireSchemaViolation] = []
    active_renames = contract.active_renamed_fields

    # Resolve producer fields
    if producer_fields is None and producer_model_fqn:
        producer_fields = _resolve_model_fields(producer_model_fqn)

    # Resolve consumer fields
    if consumer_fields is None and consumer_model_fqn:
        consumer_fields = _resolve_model_fields(consumer_model_fqn)

    if producer_fields is not None:
        violations.extend(
            _check_side(contract, producer_fields, "producer", active_renames)
        )

    if consumer_fields is not None:
        violations.extend(
            _check_side(contract, consumer_fields, "consumer", active_renames)
        )

    return violations


def _check_side(
    contract: ModelWireSchemaContract,
    model_fields: set[str],
    side: str,
    active_renames: dict[str, str],
) -> list[ModelWireSchemaViolation]:
    """Check one side (producer or consumer) against the contract."""
    violations: list[ModelWireSchemaViolation] = []
    contract_required = contract.required_field_names
    contract_all = contract.all_field_names

    # Renamed field producer names that are acceptable
    rename_producer_names = set(active_renames.keys())

    # Check: required contract field missing from model
    for field_name in sorted(contract_required):
        if field_name not in model_fields:
            # For producers, any active alias for this canonical field is acceptable
            if side == "producer":
                active_aliases = {
                    pn for pn, cn in active_renames.items() if cn == field_name
                }
                if active_aliases & model_fields:
                    continue
            violations.append(
                ModelWireSchemaViolation(
                    topic=contract.topic,
                    field_name=field_name,
                    side=side,
                    detail=(
                        f"Required field '{field_name}' in contract "
                        f"but missing from {side} model"
                    ),
                )
            )

    # Check: model field not in contract (undeclared emission/consumption)
    for field_name in sorted(model_fields - _PYDANTIC_INTERNAL):
        if field_name in contract_all:
            continue
        # Allow renamed producer names
        if side == "producer" and field_name in rename_producer_names:
            continue
        violations.append(
            ModelWireSchemaViolation(
                topic=contract.topic,
                field_name=field_name,
                side=side,
                detail=(
                    f"Field '{field_name}' in {side} model but not declared in contract"
                ),
            )
        )

    return violations


def scan_wire_schema_compliance(
    search_dirs: list[Path],
) -> list[ModelWireSchemaViolation]:
    """Run Check 5 across all discovered wire schema contracts.

    This function discovers contracts and attempts to resolve producer and consumer
    models via their fully-qualified names inferred from contract declarations.
    For cross-repo scanning, models may not be importable — in those cases
    the check is skipped for that side.
    """
    contracts = discover_wire_schema_contracts(search_dirs)
    all_violations: list[ModelWireSchemaViolation] = []

    for _path, contract in contracts:
        # Derive FQNs from contract file paths: src/<module>/path.py -> <module>.path
        def _file_to_fqn(file_path: str, model_name: str) -> str | None:
            path = file_path
            if path.startswith("src/"):
                path = path[4:]
            if path.endswith(".py"):
                path = path[:-3]
            module = path.replace("/", ".")
            return f"{module}.{model_name}"

        producer_fqn = _file_to_fqn(
            contract.producer.file, contract.producer.function or ""
        )
        consumer_fqn = _file_to_fqn(contract.consumer.file, contract.consumer.model)
        violations = check_wire_schema_mismatch(
            contract,
            producer_model_fqn=producer_fqn,
            consumer_model_fqn=consumer_fqn,
        )
        all_violations.extend(violations)

    return all_violations
