# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Wire Schema Contract Model.

Pydantic model for loading and validating wire schema contract YAML files.
Wire schema contracts are the single source of truth for cross-repo Kafka
topic field schemas — producer code, consumer models, and CI gates all
derive from these contracts.

Ticket: OMN-7357
Precedent: omnibase_infra routing_decision_v1.yaml (OMN-3425)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from onex_change_control.enums.enum_wire_field_type import (
    EnumWireFieldType,  # noqa: TC001 - Pydantic needs runtime access
)


class ModelWireFieldConstraints(BaseModel):
    """Optional constraints on a wire schema field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ge: float | None = Field(default=None, description="Greater than or equal to")
    le: float | None = Field(default=None, description="Less than or equal to")
    min_length: int | None = Field(default=None, description="Minimum string length")
    max_length: int | None = Field(default=None, description="Maximum string length")
    enum: list[str] | None = Field(default=None, description="Allowed enum values")


class ModelWireRequiredField(BaseModel):
    """A required field in a wire schema contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., description="Canonical field name")
    type: EnumWireFieldType = Field(..., description="Field type")
    description: str = Field(default="", description="Field description")
    constraints: ModelWireFieldConstraints | None = Field(
        default=None, description="Optional field constraints"
    )


class ModelWireOptionalField(BaseModel):
    """An optional field in a wire schema contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., description="Canonical field name")
    type: EnumWireFieldType = Field(..., description="Field type")
    nullable: bool = Field(default=True, description="Whether the field can be null")
    description: str = Field(default="", description="Field description")


class ModelWireRenamedField(BaseModel):
    """A renamed field tracking an active or retired shim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    producer_name: str = Field(..., description="Name emitted by the producer")
    canonical_name: str = Field(..., description="Canonical name in the contract")
    shim_status: Literal["active", "retired"] = Field(
        ..., description="Lifecycle state of the rename shim"
    )
    retirement_ticket: str = Field(
        default="", description="Ticket tracking shim retirement"
    )


class ModelWireCollapsedField(BaseModel):
    """A field collapsed into another field (e.g. into metadata)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., description="Field name")
    note: str = Field(default="", description="Explanation of collapse")


class ModelWireProducer(BaseModel):
    """Producer declaration in a wire schema contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str = Field(..., description="Repository containing the producer")
    file: str = Field(..., description="Path to producer code")
    function: str = Field(default="", description="Emitting function name")


class ModelWireConsumer(BaseModel):
    """Consumer declaration in a wire schema contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str = Field(..., description="Repository containing the consumer")
    file: str = Field(..., description="Path to consumer code")
    model: str = Field(..., description="Pydantic model class name")
    ingest_shim: str | None = Field(
        default=None, description="Ingest shim model name if active"
    )
    ingest_shim_retirement_ticket: str | None = Field(
        default=None, description="Ticket for shim retirement"
    )


class ModelWireCiGate(BaseModel):
    """CI gate declaration in a wire schema contract."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    test_file: str = Field(..., description="Path to the handshake test file")
    test_class: str = Field(default="", description="Test class name")


class ModelWireSchemaContract(BaseModel):
    """Wire schema contract for a Kafka topic.

    This model validates the YAML structure of wire schema contracts.
    It is the Pydantic representation of the canonical wire schema
    contract spec defined in OMN-7357.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    topic: str = Field(..., description="Kafka topic name")
    schema_version: str = Field(..., description="Schema version (semver)")
    ticket: str = Field(default="", description="Originating ticket")
    description: str = Field(default="", description="Contract description")

    producer: ModelWireProducer = Field(..., description="Producer declaration")
    consumer: ModelWireConsumer = Field(..., description="Consumer declaration")

    required_fields: list[ModelWireRequiredField] = Field(
        ..., description="Required fields the producer MUST emit"
    )
    optional_fields: list[ModelWireOptionalField] = Field(
        default_factory=list, description="Optional fields the producer MAY emit"
    )
    renamed_fields: list[ModelWireRenamedField] = Field(
        default_factory=list, description="Fields with active rename shims"
    )
    collapsed_fields: list[ModelWireCollapsedField] = Field(
        default_factory=list, description="Fields collapsed into other fields"
    )
    ci_gate: ModelWireCiGate | None = Field(
        default=None, description="CI gate test declaration"
    )

    @model_validator(mode="after")
    def _no_duplicate_field_names(self) -> ModelWireSchemaContract:
        """Reject contracts with duplicate field names within required or optional."""
        required_names = [f.name for f in self.required_fields]
        optional_names = [f.name for f in self.optional_fields]

        req_dupes = [n for n in required_names if required_names.count(n) > 1]
        if req_dupes:
            msg = f"Duplicate required_fields names: {sorted(set(req_dupes))}"
            raise ValueError(msg)

        opt_dupes = [n for n in optional_names if optional_names.count(n) > 1]
        if opt_dupes:
            msg = f"Duplicate optional_fields names: {sorted(set(opt_dupes))}"
            raise ValueError(msg)

        overlap = set(required_names) & set(optional_names)
        if overlap:
            msg = f"Field names appear in both required and optional: {sorted(overlap)}"
            raise ValueError(msg)

        return self

    @property
    def all_field_names(self) -> set[str]:
        """Return all declared field names (required + optional)."""
        return {f.name for f in self.required_fields} | {
            f.name for f in self.optional_fields
        }

    @property
    def required_field_names(self) -> set[str]:
        """Return required field names."""
        return {f.name for f in self.required_fields}

    @property
    def optional_field_names(self) -> set[str]:
        """Return optional field names."""
        return {f.name for f in self.optional_fields}

    @property
    def active_renamed_fields(self) -> dict[str, str]:
        """Return mapping of producer_name -> canonical_name for active shims."""
        return {
            r.producer_name: r.canonical_name
            for r in self.renamed_fields
            if r.shim_status == "active"
        }


def load_wire_schema_contract(data: dict[str, Any]) -> ModelWireSchemaContract:
    """Load a wire schema contract from a parsed YAML dict."""
    return ModelWireSchemaContract.model_validate(data)
