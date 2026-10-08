# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-20157 -- a binding retirement is typed and audited.

The DoD verifier counts every binding no second lane accepted and has no way to
take a withdrawn one out of that count. It honours a retirement that names the
typed reason, who retired the binding and when. This pins the schema half: the
OCC evidence item model accepts that shape, refuses a half-written one, and
still parses the retirements already merged (item, label, reason only).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from onex_change_control.models.model_ac_binding_retirement import (
    ModelAcBindingRetirement,
)
from onex_change_control.models.model_dod_check import ModelDodEvidenceItem

pytestmark = pytest.mark.unit

_BASE: dict[str, Any] = {
    "item": "dod-old",
    "label": "AC5",
    "reason": "The check greps ledger text about a run whose receipt is gone.",
}
_AUDIT: dict[str, Any] = {
    "retired_by": "dod-retire-binding-1620",
    "retired_at": "2026-10-08T17:00:00Z",
}


def test_a_legacy_retirement_without_the_typed_fields_still_parses() -> None:
    entry = ModelAcBindingRetirement(**_BASE)
    assert entry.reason_kind is None
    assert entry.retired_by is None


def test_superseded_by_retirement_names_its_replacement() -> None:
    entry = ModelAcBindingRetirement(
        **_BASE, **_AUDIT, reason_kind="superseded_by", superseded_by="dod-new"
    )
    assert entry.superseded_by == "dod-new"


def test_no_longer_applicable_retirement_needs_no_replacement() -> None:
    entry = ModelAcBindingRetirement(
        **_BASE, **_AUDIT, reason_kind="no_longer_applicable"
    )
    assert entry.superseded_by is None


@pytest.mark.parametrize(
    "extra",
    [
        {"reason_kind": "superseded_by"},
        {"reason_kind": "no_longer_applicable", "superseded_by": "dod-new"},
        {"reason_kind": "other"},
        {"retired_by": "a-lane"},
        {"retired_at": "2026-10-08T17:00:00Z"},
        {"reason_kind": "no_longer_applicable", "retired_by": "a-lane"},
        {
            "reason_kind": "no_longer_applicable",
            "retired_by": "a-lane",
            "retired_at": "yesterday",
        },
    ],
)
def test_a_half_written_typed_retirement_is_refused(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ModelAcBindingRetirement(**_BASE, **extra)


def test_a_typed_retirement_still_needs_a_reason() -> None:
    with pytest.raises(ValidationError):
        ModelAcBindingRetirement(
            item="dod-old",
            label="AC5",
            reason="x",
            reason_kind="no_longer_applicable",
            **_AUDIT,
        )


def test_the_evidence_item_accepts_a_typed_retirement() -> None:
    item = ModelDodEvidenceItem(
        id="dod-retire",
        description="retires a draft binding",
        supersedes_ac_binding=(
            ModelAcBindingRetirement(
                **_BASE, **_AUDIT, reason_kind="superseded_by", superseded_by="dod-new"
            ),
        ),
    )
    assert item.supersedes_ac_binding[0].retired_at == "2026-10-08T17:00:00Z"
