# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Shared fixtures for contract drift tests."""

from __future__ import annotations

from typing import Any

import pytest

from onex_change_control.handlers.handler_drift_analysis import compute_canonical_hash
from onex_change_control.models.model_contract_drift_input import (
    ModelContractDriftInput,
)


@pytest.fixture
def base_compute_contract() -> dict[str, Any]:
    """A minimal ONEX COMPUTE contract dict for testing."""
    return {
        "name": "node_transform_data",
        "version": "1.0.0",
        "type": "COMPUTE",
        "description": "Transforms input records.",
        "algorithm": {
            "algorithm_type": "default_transform",
            "deterministic": True,
        },
        "input_schema": "ModelTransformInput",
        "output_schema": "ModelTransformOutput",
        "metadata": {
            "owner": "platform-team",
            "sla_ms": 100,
        },
    }


@pytest.fixture
def pinned_hash(base_compute_contract: dict[str, Any]) -> str:
    return compute_canonical_hash(base_compute_contract)


@pytest.fixture
def drift_input_no_change(
    base_compute_contract: dict[str, Any],
    pinned_hash: str,
) -> ModelContractDriftInput:
    return ModelContractDriftInput(
        contract_name="node_transform_data",
        current_contract=base_compute_contract,
        pinned_hash=pinned_hash,
    )
