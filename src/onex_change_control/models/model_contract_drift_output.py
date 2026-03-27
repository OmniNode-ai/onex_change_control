# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Output models for contract drift detection."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from onex_change_control.enums.enum_drift_severity import (
    EnumDriftSeverity,  # noqa: TC001
)


class ModelFieldChange(BaseModel):
    """A single field-level change between current and pinned contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    path: str = Field(description="Dot-separated path to the changed field.")
    change_type: str = Field(description="One of: 'added', 'removed', 'modified'.")
    old_value: str | int | float | bool | list[object] | dict[str, object] | None = (
        Field(default=None, description="Value in the pinned contract.")
    )
    new_value: str | int | float | bool | list[object] | dict[str, object] | None = (
        Field(default=None, description="Value in the current contract.")
    )
    is_breaking: bool = Field(
        description="True when this change is likely to break existing consumers."
    )


class ModelContractDriftOutput(BaseModel):
    """Full drift report produced by the contract drift COMPUTE node."""

    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    contract_name: str
    severity: EnumDriftSeverity
    current_hash: str = Field(description="Canonical hash of the current contract.")
    pinned_hash: str = Field(description="Hash supplied in the input (baseline).")
    drift_detected: bool
    field_changes: list[ModelFieldChange]
    breaking_changes: list[str] = Field(
        description="Human-readable summaries of breaking changes."
    )
    additive_changes: list[str] = Field(
        description="Human-readable summaries of additive changes."
    )
    non_breaking_changes: list[str] = Field(
        description="Human-readable summaries of non-breaking changes."
    )
    summary: str = Field(description="One-line summary of the drift report.")
