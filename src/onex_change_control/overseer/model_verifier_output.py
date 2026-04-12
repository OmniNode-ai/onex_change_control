# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from onex_change_control.overseer.enum_failure_class import (
    EnumFailureClass,
)
from onex_change_control.overseer.enum_verifier_verdict import EnumVerifierVerdict


class ModelVerifierCheckResult(BaseModel):
    """Result of a single verification check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., description="Check name identifier.")
    passed: bool = Field(..., description="Whether this check passed.")
    message: str = Field(default="", description="Details about the check result.")
    failure_class: EnumFailureClass | None = Field(
        default=None,
        description="Failure classification, set only when passed=False.",
    )

    @model_validator(mode="after")
    def validate_failure_class_consistency(self) -> ModelVerifierCheckResult:
        if self.passed and self.failure_class is not None:
            msg = "failure_class must be None when passed=True"
            raise ValueError(msg)
        return self


class ModelVerifierOutput(BaseModel):
    """Output from the deterministic verification layer.

    This is the contract between the verification layer and the routing engine.
    Contains the overall verdict, per-check results, and optional shim outputs
    for downstream consumers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: EnumVerifierVerdict = Field(
        ..., description="Overall verification verdict."
    )
    checks: tuple[ModelVerifierCheckResult, ...] = Field(
        default_factory=tuple, description="Per-check results."
    )
    failure_class: EnumFailureClass | None = Field(
        default=None,
        description="Dominant failure class when verdict is not PASS.",
    )
    shim_outputs: dict[str, str] = Field(
        default_factory=dict,
        description="Opaque key-value outputs for downstream shim consumers.",
    )
    summary: str = Field(
        default="", description="Human-readable summary of verification results."
    )

    @model_validator(mode="after")
    def validate_verdict_consistency(self) -> ModelVerifierOutput:
        if self.verdict == EnumVerifierVerdict.PASS and self.failure_class is not None:
            msg = "failure_class must be None when verdict=PASS"
            raise ValueError(msg)
        return self


__all__: list[str] = ["ModelVerifierCheckResult", "ModelVerifierOutput"]
