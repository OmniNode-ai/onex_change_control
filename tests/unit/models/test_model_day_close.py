# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for ModelDayCloseInvariantsChecked.

Tests cover:
1. integration_sweep defaults to UNKNOWN when omitted
2. integration_sweep accepts explicit PASS value
3. Existing fields still required (no regression)
"""

from __future__ import annotations

import pytest

from onex_change_control.enums.enum_invariant_status import EnumInvariantStatus
from onex_change_control.models.model_day_close import ModelDayCloseInvariantsChecked

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_KWARGS = {
    "reducers_pure": EnumInvariantStatus.PASS,
    "orchestrators_no_io": EnumInvariantStatus.PASS,
    "effects_do_io_only": EnumInvariantStatus.PASS,
    "real_infra_proof_progressing": EnumInvariantStatus.PASS,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_invariants_checked_integration_sweep_defaults_to_unknown() -> None:
    """integration_sweep defaults to UNKNOWN when kwarg is omitted."""
    model = ModelDayCloseInvariantsChecked(**_BASE_KWARGS)
    assert model.integration_sweep == EnumInvariantStatus.UNKNOWN


@pytest.mark.unit
def test_invariants_checked_integration_sweep_explicit_pass() -> None:
    """integration_sweep accepts explicit PASS value."""
    model = ModelDayCloseInvariantsChecked(
        **_BASE_KWARGS,
        integration_sweep=EnumInvariantStatus.PASS,
    )
    assert model.integration_sweep == EnumInvariantStatus.PASS


@pytest.mark.unit
def test_invariants_checked_existing_fields_unchanged() -> None:
    """Existing required fields still behave as before (no regression)."""
    model = ModelDayCloseInvariantsChecked(**_BASE_KWARGS)
    assert model.reducers_pure == EnumInvariantStatus.PASS
    assert model.orchestrators_no_io == EnumInvariantStatus.PASS
    assert model.effects_do_io_only == EnumInvariantStatus.PASS
    assert model.real_infra_proof_progressing == EnumInvariantStatus.PASS
