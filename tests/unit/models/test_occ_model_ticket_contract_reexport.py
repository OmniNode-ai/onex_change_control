# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Red tests for OCC ModelTicketContract re-export of core model.

Task 3 of OMN-9582 plan (TDD red step).

These tests assert the TARGET state after OMN-10066 converts
onex_change_control/models/model_ticket_contract.py to a re-export.
They are marked xfail(strict=True) because OCC still has its own copy
of ModelTicketContract (the "dual model" problem). OMN-10066 will remove
the xfail markers when the re-export is wired in.

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
@pytest.mark.xfail(
    strict=True,
    reason="OMN-10066 converts OCC ModelTicketContract to a re-export; "
    "until then OCC has its own copy so 'is' check fails",
)
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

    # NOTE(OMN-10065): mypy reports comparison-overlap because statically the two
    # classes are distinct types. That is exactly what xfail captures — they ARE
    # different until OMN-10066 wires the re-export. Suppress for red phase only.
    assert OccModelTicketContract is CoreModelTicketContract, (  # type: ignore[comparison-overlap]
        "OCC ModelTicketContract must be the same class object as core's "
        f"ModelTicketContract. Got OCC={OccModelTicketContract!r}, "
        f"core={CoreModelTicketContract!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Construction: OCC import accepts all merged-schema fields
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.xfail(
    strict=True,
    reason="OMN-10066 converts OCC ModelTicketContract to a re-export; "
    "until then OCC model lacks merged-schema fields (schema_version, "
    "summary, is_seam_ticket, contract_completeness, dod_evidence, etc.)",
)
def test_occ_model_ticket_contract_construction_with_merged_fields() -> None:
    """ModelTicketContract constructed via OCC import accepts all merged fields.

    This test exercises the full merged-schema field set absorbed from the
    OCC-local model by OMN-10064. After re-export the OCC import delegates
    to core, so all fields must be accepted without ValidationError.

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

    # NOTE(OMN-10065): mypy reports call-arg errors because the OCC model
    # (pre-re-export) does not have title/schema_version/summary etc. These
    # fields will exist after OMN-10066 wires the re-export. Constructing via
    # model_validate bypasses static type checking while still exercising the
    # runtime validation path that the xfail gate tests.
    contract = ModelTicketContract.model_validate(  # type: ignore[attr-defined]
        {
            "ticket_id": "OMN-1",
            "title": "Test ticket for re-export verification",
            "schema_version": "1.0.0",
            "summary": "Verify OCC re-export absorbs all merged fields",
            "is_seam_ticket": False,
            "interface_change": False,
        }
    )

    # Verify merged fields are present and have correct defaults
    assert contract.schema_version == "1.0.0"
    assert contract.summary == "Verify OCC re-export absorbs all merged fields"
    assert contract.is_seam_ticket is False
    assert contract.interface_change is False
    assert contract.interfaces_touched == []
    assert contract.evidence_requirements == []
    assert contract.emergency_bypass is None
    assert contract.golden_path is None
    assert contract.dod_evidence == []

    # contract_completeness should default to STUB
    from omnibase_core.enums.enum_contract_completeness import EnumContractCompleteness

    assert contract.contract_completeness == EnumContractCompleteness.STUB

    # Ticket identification (original core required fields)
    assert contract.ticket_id == "OMN-1"
    assert contract.title == "Test ticket for re-export verification"


# ---------------------------------------------------------------------------
# Test 3 — Schema purity: re-export file passes check-schema-purity
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.xfail(
    strict=True,
    reason="OMN-10066 converts OCC ModelTicketContract to a re-export; "
    "until then the existing model file has its own Pydantic model definition "
    "with os/datetime imports that technically pass purity today, but the "
    "re-export file structure is what this test validates. This xfail will be "
    "removed when the re-export is in place and check-schema-purity is "
    "confirmed green on the new file.",
)
def test_occ_model_ticket_contract_reexport_passes_schema_purity() -> None:
    """check-schema-purity must pass on model_ticket_contract.py after re-export.

    A re-export file contains only 're-export' import statements — no class
    definitions, no I/O, no env reads, no time calls. check-schema-purity
    enforces the D-008 purity rule. After OMN-10066 converts the file to a
    re-export, this test verifies the purity tool accepts it.

    The test explicitly invokes check-schema-purity via subprocess to match
    the CI gate behavior rather than calling the Python API directly.
    """
    import importlib.util
    from pathlib import Path

    # Locate the OCC model file
    spec = importlib.util.find_spec("onex_change_control.models.model_ticket_contract")
    assert spec is not None, (
        "onex_change_control.models.model_ticket_contract not importable"
    )
    assert spec.origin is not None
    model_file = Path(spec.origin)
    assert model_file.exists(), f"model file not found: {model_file}"

    # Read the re-exported file and assert it contains only re-export lines
    # (no class definitions, no field declarations, no pydantic imports)
    source = model_file.read_text()

    # A pure re-export file must NOT define its own ModelTicketContract class
    assert "class ModelTicketContract" not in source, (
        "After OMN-10066, model_ticket_contract.py must not contain a class "
        "definition — it must be a pure re-export of the core class. "
        "Found 'class ModelTicketContract' in the file."
    )

    # It MUST contain a re-export import from core
    assert "from omnibase_core.models.ticket.model_ticket_contract import" in source, (
        "After OMN-10066, model_ticket_contract.py must re-export from "
        "omnibase_core.models.ticket.model_ticket_contract. Re-export import not found."
    )

    # Run check-schema-purity subprocess to confirm the tool accepts it
    result = subprocess.run(
        [sys.executable, "-m", "onex_change_control.scripts.check_schema_purity"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"check-schema-purity failed on re-export file.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
