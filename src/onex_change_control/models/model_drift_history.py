# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Drift history accumulation model for the REDUCER node."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from onex_change_control.models.model_contract_drift_output import (
    ModelContractDriftOutput,  # noqa: TC001  Why: Pydantic model needs runtime type for field annotation
)


class ModelDriftHistory(BaseModel):
    """Accumulated drift state for a single contract over time."""

    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    contract_name: str
    state: str = Field(
        default="clean",
        description=(
            "FSM state: clean, drifted. (Phase 1 — acknowledged/resolved deferred.)"
        ),
    )
    drift_reports: list[ModelContractDriftOutput] = Field(
        default_factory=list,
        description="Ordered list of drift reports for this contract.",
    )
    transition_count: int = Field(
        default=0,
        description="Number of FSM state transitions.",
    )
