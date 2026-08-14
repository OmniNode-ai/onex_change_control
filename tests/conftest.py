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


# OMN-15669 REMEDIATION r1: there is deliberately NO `collect_ignore_glob` here.
#
# The first build excluded `fixtures/contract_shape_v1/conformant/tests/*.py`
# from the outer suite on the reasoning that the tree is "data the gate
# collects, not a test of this repo". The gate collects it with
# `pytest --collect-only`, which never EXECUTES a line — so the conformant
# fixture's `assert_seam_shape` calls ran nowhere in CI, and an adversarial
# replay confirmed that mutating `MockWidgetStore` to return a shape the seam
# schema forbids left the suite 51/51 green. The reference implementation of
# "the mock is validated against the real seam schema" was itself unvalidated.
#
# The fixture module is a real, passing, self-contained test module: it resolves
# its schema from its own FIXTURE_ROOT, so it runs correctly from the outer
# rootdir. Collecting it normally is what makes the reference shape load-bearing
# — break the mock and the suite goes red. `test_conformant_fixture_executes_
# and_catches_a_divergent_mock` in tests/test_contract_shape_v1_legs.py is the
# anti-regression anchor for this decision.
