# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for CrossSchemaCoherenceValidator v1 (OMN-4344)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from onex_change_control.validators.cross_schema_coherence import (
    CoherenceResult,
    CrossSchemaCoherenceValidator,
    EnumCoherenceLevel,
)


@pytest.mark.unit
def test_no_interfaces_provided_not_required(tmp_path: Path) -> None:
    v = CrossSchemaCoherenceValidator(contracts_dir=tmp_path)
    result = v.check("OMN-100", {"interfaces_provided": []})
    assert result.passed is True
    assert result.level == EnumCoherenceLevel.NOT_REQUIRED


@pytest.mark.unit
def test_interfaces_provided_missing_seam_contract_fails(tmp_path: Path) -> None:
    v = CrossSchemaCoherenceValidator(contracts_dir=tmp_path)
    result = v.check("OMN-200", {"interfaces_provided": [{"name": "FooProtocol"}]})
    assert result.passed is False
    assert "OMN-200" in result.message


@pytest.mark.unit
def test_seam_contract_is_seam_ticket_true_passes(tmp_path: Path) -> None:
    seam: dict[str, object] = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-300",
        "summary": "test",
        "is_seam_ticket": True,
        "interface_change": True,
        "interfaces_touched": ["topics"],
        "emergency_bypass": {
            "enabled": False,
            "justification": "",
            "follow_up_ticket_id": "",
        },
    }
    (tmp_path / "OMN-300.yaml").write_text(yaml.dump(seam))
    v = CrossSchemaCoherenceValidator(contracts_dir=tmp_path)
    result = v.check("OMN-300", {"interfaces_provided": [{"name": "FooProtocol"}]})
    assert result.passed is True
    assert result.level == EnumCoherenceLevel.V1_SEAM_PRESENT


@pytest.mark.unit
def test_seam_contract_is_seam_ticket_false_fails(tmp_path: Path) -> None:
    seam: dict[str, object] = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-400",
        "summary": "test",
        "is_seam_ticket": False,
        "interface_change": False,
        "interfaces_touched": [],
        "emergency_bypass": {
            "enabled": False,
            "justification": "",
            "follow_up_ticket_id": "",
        },
    }
    (tmp_path / "OMN-400.yaml").write_text(yaml.dump(seam))
    v = CrossSchemaCoherenceValidator(contracts_dir=tmp_path)
    result = v.check("OMN-400", {"interfaces_provided": [{"name": "Bar"}]})
    assert result.passed is False
    assert "is_seam_ticket" in result.message
