# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

from __future__ import annotations

from omnibase_core.models.contracts.ticket.model_dod_receipt import (
    ModelDodReceipt,  # noqa: TC002
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from onex_change_control.overseer.enum_failure_class import EnumFailureClass
from onex_change_control.overseer.enum_verifier_verdict import EnumVerifierVerdict


class ModelVerifierOutput(BaseModel):
    """Output from the deterministic verification layer.

    This is the contract between the verification layer and the routing engine.
    Contains the overall verdict, per-check results (as ModelDodReceipt), and
    optional shim outputs for downstream consumers.

    Per OMN-9792: checks items migrated from ModelVerifierCheckResult to
    ModelDodReceipt. failure_class encoding moves to probe_stdout on each receipt.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: EnumVerifierVerdict = Field(
        ..., description="Overall verification verdict."
    )
    checks: tuple[ModelDodReceipt, ...] = Field(
        default_factory=tuple, description="Per-check results as canonical receipts."
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


__all__: list[str] = ["ModelVerifierOutput"]
