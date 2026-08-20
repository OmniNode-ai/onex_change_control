# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for contract drift detection models."""

from __future__ import annotations

import pytest

from onex_change_control.enums.enum_drift_sensitivity import EnumDriftSensitivity
from onex_change_control.enums.enum_drift_severity import EnumDriftSeverity
from onex_change_control.models.model_contract_drift_input import (
    ModelContractDriftInput,
)
from onex_change_control.models.model_contract_drift_output import (
    ModelContractDriftOutput,
    ModelFieldChange,
)


class TestEnumDriftSeverity:
    def test_values_exist(self) -> None:
        assert EnumDriftSeverity.NONE == "NONE"
        assert EnumDriftSeverity.NON_BREAKING == "NON_BREAKING"
        assert EnumDriftSeverity.ADDITIVE == "ADDITIVE"
        assert EnumDriftSeverity.BREAKING == "BREAKING"


class TestEnumDriftSensitivity:
    def test_values_exist(self) -> None:
        assert EnumDriftSensitivity.STRICT == "STRICT"
        assert EnumDriftSensitivity.STANDARD == "STANDARD"
        assert EnumDriftSensitivity.LAX == "LAX"


class TestModelContractDriftInput:
    def test_construction(self) -> None:
        inp = ModelContractDriftInput(
            contract_name="test",
            current_contract={"name": "test", "type": "COMPUTE"},
            pinned_hash="a" * 64,
        )
        assert inp.contract_name == "test"
        assert inp.sensitivity == EnumDriftSensitivity.STANDARD

    def test_frozen(self) -> None:
        inp = ModelContractDriftInput(
            contract_name="test",
            current_contract={"name": "test"},
            pinned_hash="a" * 64,
        )
        with pytest.raises(Exception, match="frozen"):
            inp.contract_name = "changed"  # type: ignore[misc]


class TestModelFieldChange:
    def test_construction(self) -> None:
        change = ModelFieldChange(
            path="algorithm.type",
            change_type="modified",
            old_value="default",
            new_value="custom",
            is_breaking=True,
        )
        assert change.is_breaking


class TestModelContractDriftOutput:
    def test_no_drift(self) -> None:
        out = ModelContractDriftOutput(
            contract_name="test",
            severity=EnumDriftSeverity.NONE,
            current_hash="a" * 64,
            pinned_hash="a" * 64,
            drift_detected=False,
            field_changes=[],
            breaking_changes=[],
            additive_changes=[],
            non_breaking_changes=[],
            summary="test: no drift detected",
        )
        assert not out.drift_detected
        assert out.severity == EnumDriftSeverity.NONE
