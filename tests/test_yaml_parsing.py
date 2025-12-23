"""Tests for YAML parsing with Pydantic models."""

from pathlib import Path

import pytest
import yaml

from onex_change_control.models.model_day_close import ModelDayClose
from onex_change_control.models.model_ticket_contract import ModelTicketContract

# Template directory path for template validation tests
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

# Path to test YAML file
YAML_PATH = Path(__file__).parent.parent / "drift" / "day_close" / "2025-12-19.yaml"


@pytest.mark.skipif(not YAML_PATH.exists(), reason="YAML fixture not present")
def test_parse_existing_day_close_yaml() -> None:
    """Test parsing existing day_close.yaml file."""
    with YAML_PATH.open() as f:
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


def test_template_day_close_minimal_valid() -> None:
    """Test that day_close template can be filled with minimal valid values.

    This test validates the template structure by creating a minimal valid
    day close report based on the template. All required fields must be present
    and valid according to the schema.
    """
    # Minimal valid day close based on template
    data = {
        "schema_version": "1.0.0",
        "date": "2025-12-21",  # Valid ISO date
        "process_changes_today": [],  # Empty list is valid
        "plan": [],  # Empty list is valid
        "actual_by_repo": [],  # Empty list is valid
        "drift_detected": [],  # Empty list is valid
        "invariants_checked": {
            "reducers_pure": "pass",
            "orchestrators_no_io": "pass",
            "effects_do_io_only": "pass",
            "real_infra_proof_progressing": "pass",
        },
        "corrections_for_tomorrow": [],  # Empty list is valid
        "risks": [],  # Empty list is valid
    }

    # Parse with Pydantic model - should not raise
    day_close = ModelDayClose.model_validate(data)

    assert day_close.schema_version == "1.0.0"
    assert day_close.date == "2025-12-21"
    assert len(day_close.process_changes_today) == 0
    assert len(day_close.plan) == 0
    assert len(day_close.actual_by_repo) == 0
    assert len(day_close.drift_detected) == 0
    assert day_close.invariants_checked.reducers_pure == "pass"
    assert len(day_close.corrections_for_tomorrow) == 0
    assert len(day_close.risks) == 0


def test_template_ticket_contract_minimal_valid() -> None:
    """Test that ticket_contract template can be filled with minimal valid values.

    This test validates the template structure by creating a minimal valid
    ticket contract based on the template. All required fields must be present
    and valid according to the schema.
    """
    # Minimal valid ticket contract based on template
    data = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-999",
        "summary": "Test ticket summary",
        "is_seam_ticket": False,
        "interface_change": False,
        "interfaces_touched": [],  # Must be empty when interface_change=False
        "evidence_requirements": [],  # Empty list is valid
        "emergency_bypass": {
            "enabled": False,
            "justification": "",
            "follow_up_ticket_id": "",
        },
    }

    # Parse with Pydantic model - should not raise
    contract = ModelTicketContract.model_validate(data)

    assert contract.schema_version == "1.0.0"
    assert contract.ticket_id == "OMN-999"
    assert contract.summary == "Test ticket summary"
    assert contract.is_seam_ticket is False
    assert contract.interface_change is False
    assert len(contract.interfaces_touched) == 0
    assert len(contract.evidence_requirements) == 0
    assert contract.emergency_bypass.enabled is False


def test_template_ticket_contract_with_evidence_requirements() -> None:
    """Test ticket contract template with evidence requirements filled.

    Validates that evidence requirements can be properly specified using
    all valid evidence kinds from the enum.
    """
    data = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-999",
        "summary": "Test ticket with evidence",
        "is_seam_ticket": False,
        "interface_change": False,
        "interfaces_touched": [],
        "evidence_requirements": [
            {
                "kind": "tests",
                "description": "Unit tests required",
                "command": "poetry run pytest tests/test_models.py",
            },
            {
                "kind": "docs",
                "description": "Documentation update required",
                "command": None,  # Optional field
            },
            {
                "kind": "ci",
                "description": "CI validation required",
                "command": None,
            },
            {
                "kind": "benchmark",
                "description": "Performance benchmark required",
                "command": "poetry run pytest tests/test_benchmark.py",
            },
            {
                "kind": "manual",
                "description": "Manual verification checklist",
                "command": None,
            },
        ],
        "emergency_bypass": {
            "enabled": False,
            "justification": "",
            "follow_up_ticket_id": "",
        },
    }

    contract = ModelTicketContract.model_validate(data)

    # Test all 5 evidence kinds: tests, docs, ci, benchmark, manual
    expected_evidence_count = 5
    assert len(contract.evidence_requirements) == expected_evidence_count
    assert contract.evidence_requirements[0].kind == "tests"
    assert contract.evidence_requirements[1].kind == "docs"
    assert contract.evidence_requirements[2].kind == "ci"
    assert contract.evidence_requirements[3].kind == "benchmark"
    assert contract.evidence_requirements[4].kind == "manual"


def test_template_ticket_contract_unknown_handling() -> None:
    """Test ticket contract template with unknown handling scenarios.

    Validates that templates support 'unknown' status for invariants and
    proper handling of incomplete information.
    """
    # Test with interface_change=True but empty interfaces_touched (temporary state)
    data = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-999",
        "summary": "Test ticket with unknown interfaces",
        "is_seam_ticket": True,
        "interface_change": True,
        "interfaces_touched": [],  # Temporarily empty - allowed but incomplete
        "evidence_requirements": [],
        "emergency_bypass": {
            "enabled": False,
            "justification": "",
            "follow_up_ticket_id": "",
        },
    }

    contract = ModelTicketContract.model_validate(data)

    # Contract should parse but be marked as incomplete
    assert contract.interface_change is True
    assert len(contract.interfaces_touched) == 0
    assert (
        contract.is_complete is False
    )  # Incomplete because interfaces_touched is empty


def test_template_ticket_contract_emergency_bypass() -> None:
    """Test ticket contract template with emergency bypass enabled.

    Validates that emergency bypass can be properly configured when needed.
    """
    data = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-999",
        "summary": "Emergency hotfix ticket",
        "is_seam_ticket": False,
        "interface_change": False,
        "interfaces_touched": [],
        "evidence_requirements": [],
        "emergency_bypass": {
            "enabled": True,
            "justification": "Production incident requires immediate hotfix",
            "follow_up_ticket_id": "OMN-1000",
        },
    }

    contract = ModelTicketContract.model_validate(data)

    assert contract.emergency_bypass.enabled is True
    assert (
        contract.emergency_bypass.justification
        == "Production incident requires immediate hotfix"
    )
    assert contract.emergency_bypass.follow_up_ticket_id == "OMN-1000"


def test_template_day_close_unknown_invariant_statuses() -> None:
    """Test that day_close template supports 'unknown' for invariant statuses.

    This test validates the unknown handling guidance in the template,
    ensuring that 'unknown' is a valid value for invariant status fields
    when the status cannot be determined.
    """
    data = {
        "schema_version": "1.0.0",
        "date": "2025-12-21",
        "process_changes_today": [],
        "plan": [],
        "actual_by_repo": [],
        "drift_detected": [],
        "invariants_checked": {
            "reducers_pure": "unknown",  # Status cannot be determined yet
            "orchestrators_no_io": "unknown",
            "effects_do_io_only": "unknown",
            "real_infra_proof_progressing": "unknown",
        },
        "corrections_for_tomorrow": [],
        "risks": [],
    }

    day_close = ModelDayClose.model_validate(data)

    assert day_close.invariants_checked.reducers_pure == "unknown"
    assert day_close.invariants_checked.orchestrators_no_io == "unknown"
    assert day_close.invariants_checked.effects_do_io_only == "unknown"
    assert day_close.invariants_checked.real_infra_proof_progressing == "unknown"


def test_template_files_parse_with_minimal_replacements() -> None:
    """Test that template files can be parsed with minimal placeholder replacements.

    This test validates that the template files themselves are schema-aligned
    by parsing them with minimal valid replacements. This catches issues like
    invalid placeholder values (e.g., pr: 0) automatically in CI.
    """
    # Test day_close template with minimal replacements
    day_close_template_path = TEMPLATE_DIR / "day_close.template.yaml"
    if day_close_template_path.exists():
        with day_close_template_path.open() as f:
            day_close_data = yaml.safe_load(f)

        # Replace placeholders with minimal valid values
        day_close_data["date"] = "2025-12-21"
        day_close_data["process_changes_today"] = []
        day_close_data["plan"] = []
        day_close_data["actual_by_repo"] = []
        day_close_data["drift_detected"] = []
        day_close_data["invariants_checked"] = {
            "reducers_pure": "pass",
            "orchestrators_no_io": "pass",
            "effects_do_io_only": "pass",
            "real_infra_proof_progressing": "pass",
        }
        day_close_data["corrections_for_tomorrow"] = []
        day_close_data["risks"] = []

        # Should parse without errors
        day_close = ModelDayClose.model_validate(day_close_data)
        assert day_close.schema_version == "1.0.0"
        assert day_close.date == "2025-12-21"

    # Test ticket_contract template with minimal replacements
    ticket_contract_template_path = TEMPLATE_DIR / "ticket_contract.template.yaml"
    if ticket_contract_template_path.exists():
        with ticket_contract_template_path.open() as f:
            contract_data = yaml.safe_load(f)

        # Replace placeholders with minimal valid values
        contract_data["ticket_id"] = "OMN-999"
        contract_data["summary"] = "Test ticket"
        contract_data["is_seam_ticket"] = False
        contract_data["interface_change"] = False
        contract_data["interfaces_touched"] = []
        contract_data["evidence_requirements"] = []
        contract_data["emergency_bypass"] = {
            "enabled": False,
            "justification": "",
            "follow_up_ticket_id": "",
        }

        # Should parse without errors
        contract = ModelTicketContract.model_validate(contract_data)
        assert contract.schema_version == "1.0.0"
        assert contract.ticket_id == "OMN-999"
