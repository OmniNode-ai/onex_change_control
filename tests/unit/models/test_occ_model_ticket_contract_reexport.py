# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for OCC ModelTicketContract re-export of core model. OMN-10066

These tests assert the post-OMN-10066 state where
onex_change_control/models/model_ticket_contract.py is a pure re-export.

Test coverage:
  1. Class identity: OCC import resolves to the SAME object as core import.
  2. Construction: OCC-path import accepts all merged-schema fields.
  3. Schema purity: re-export file passes check-schema-purity (no I/O, env, time).
"""

import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Test 1 — Class identity: OCC import IS the core class
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_occ_model_ticket_contract_is_core_class() -> None:
    """OCC ModelTicketContract must be the identical object as core's class.

    After re-export, both import paths must resolve to the same Python object.
    The 'is' check (object identity) is stricter than equality and is the
    correct assertion: a re-export that creates a subclass or copy would
    pass equality but fail identity.
    """
    from omnibase_core.models.ticket.model_ticket_contract import (
        ModelTicketContract as CoreModelTicketContract,
    )

    from onex_change_control.models.model_ticket_contract import (
        ModelTicketContract as OccModelTicketContract,
    )

    assert OccModelTicketContract is CoreModelTicketContract, (
        "OCC ModelTicketContract must be the same class object as core's "
        f"ModelTicketContract. Got OCC={OccModelTicketContract!r}, "
        f"core={CoreModelTicketContract!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Construction: OCC import accepts all merged-schema fields
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_occ_model_ticket_contract_construction_with_merged_fields() -> None:
    """ModelTicketContract constructed via OCC import accepts all merged fields.

    Exercises the full merged-schema field set absorbed from the OCC-local
    model by OMN-10064. After re-export the OCC import delegates to core,
    so all fields must be accepted without ValidationError.

    Merged fields verified here:
      - schema_version (SemVer string, default "1.0.0")
      - summary (human-readable summary, default "")
      - is_seam_ticket (bool, default False)
      - interface_change (bool, default False)
      - interfaces_touched (list, must be empty when interface_change=False)
      - evidence_requirements (list)
      - emergency_bypass (None or ModelEmergencyBypass)
      - golden_path (None or ModelGoldenPath)
      - dod_evidence (list of ModelContractDodItem)
      - contract_completeness (EnumContractCompleteness, default STUB)

    Original core fields also verified: ticket_id, title.
    """
    from onex_change_control.models.model_ticket_contract import ModelTicketContract

    contract = ModelTicketContract.model_validate(
        {
            "ticket_id": "OMN-1",
            "title": "Test ticket for re-export verification",
            "schema_version": "1.0.0",
            "summary": "Verify OCC re-export absorbs all merged fields",
            "is_seam_ticket": False,
            "interface_change": False,
        }
    )

    assert contract.schema_version == "1.0.0"
    assert contract.summary == "Verify OCC re-export absorbs all merged fields"
    assert contract.is_seam_ticket is False
    assert contract.interface_change is False
    assert contract.interfaces_touched == []
    assert contract.evidence_requirements == []
    assert contract.emergency_bypass is None
    assert contract.golden_path is None
    assert contract.dod_evidence == []

    from omnibase_core.enums.enum_contract_completeness import EnumContractCompleteness

    assert contract.contract_completeness == EnumContractCompleteness.STUB

    assert contract.ticket_id == "OMN-1"
    assert contract.title == "Test ticket for re-export verification"


# ---------------------------------------------------------------------------
# Test 3 — Schema purity: re-export file passes check-schema-purity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_occ_model_ticket_contract_reexport_passes_schema_purity() -> None:
    """check-schema-purity must pass on model_ticket_contract.py after re-export.

    A re-export file contains only re-export import statements — no class
    definitions, no I/O, no env reads, no time calls. check-schema-purity
    enforces the D-008 purity rule.
    """
    import importlib.util
    from pathlib import Path

    spec = importlib.util.find_spec("onex_change_control.models.model_ticket_contract")
    assert spec is not None, (
        "onex_change_control.models.model_ticket_contract not importable"
    )
    assert spec.origin is not None
    model_file = Path(spec.origin)
    assert model_file.exists(), f"model file not found: {model_file}"

    source = model_file.read_text()

    assert "class ModelTicketContract" not in source, (
        "After OMN-10066, model_ticket_contract.py must not contain a class "
        "definition — it must be a pure re-export of the core class. "
        "Found 'class ModelTicketContract' in the file."
    )

    assert "from omnibase_core.models.ticket.model_ticket_contract import" in source, (
        "After OMN-10066, model_ticket_contract.py must re-export from "
        "omnibase_core.models.ticket.model_ticket_contract. Re-export import not found."
    )

    try:
        result = subprocess.run(
            [sys.executable, "-m", "onex_change_control.scripts.check_schema_purity"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("check-schema-purity timed out after 30 seconds")
    assert result.returncode == 0, (
        f"check-schema-purity failed on re-export file.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
