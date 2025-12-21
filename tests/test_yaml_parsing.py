"""Tests for YAML parsing with Pydantic models."""

from pathlib import Path

import yaml

from onex_change_control.models.model_day_close import ModelDayClose
from onex_change_control.models.model_ticket_contract import ModelTicketContract


def test_parse_existing_day_close_yaml() -> None:
    """Test parsing existing day_close.yaml file."""
    yaml_path = Path(__file__).parent.parent / "drift" / "day_close" / "2025-12-19.yaml"
    with yaml_path.open() as f:
        data = yaml.safe_load(f)

    # Parse with Pydantic model
    day_close = ModelDayClose.model_validate(data)

    assert day_close.schema_version == "1.0.0"
    assert day_close.date == "2025-12-19"
    assert len(day_close.plan) == 1
    expected_repo_count = 3
    assert len(day_close.actual_by_repo) == expected_repo_count
    assert len(day_close.drift_detected) == 1
    expected_corrections_count = 2
    assert len(day_close.corrections_for_tomorrow) == expected_corrections_count
    expected_risks_count = 2
    assert len(day_close.risks) == expected_risks_count


def test_parse_ticket_contract_template() -> None:
    """Test parsing ticket contract template.

    Note: The template contains placeholder values that need to be replaced
    with actual values. This test validates that a properly filled template
    can be parsed. We'll create a valid example instead of parsing the template
    directly since templates contain placeholders.
    """
    # Create a valid contract based on template structure
    data = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-000",
        "summary": "Test ticket",
        "is_seam_ticket": False,
        "interface_change": False,
        "interfaces_touched": [],
        "evidence_requirements": [
            {
                "kind": "tests",
                "description": "Unit tests required",
                "command": None,
            }
        ],
        "emergency_bypass": {
            "enabled": False,
            "justification": "",
            "follow_up_ticket_id": "",
        },
    }

    # Parse with Pydantic model
    contract = ModelTicketContract.model_validate(data)

    assert contract.schema_version == "1.0.0"
    assert contract.ticket_id == "OMN-000"
    assert contract.is_seam_ticket is False
    assert contract.interface_change is False
    assert len(contract.interfaces_touched) == 0
    assert contract.emergency_bypass.enabled is False
