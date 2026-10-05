# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18406 -- a stale pin on a RETIRED binding is not refused.

The append-only gate forbids editing a merged ``dod_evidence`` entry, so a
binding whose criterion was later rewritten can only be corrected by appending
a ``supersedes_ac_binding`` item. ``_stale_pins`` used to ignore that retired
set, so any PR touching such a contract was refused with ``ac_binding_stale_hash``
on a binding the contract had already withdrawn.

The negative controls keep the gate closed for LIVE bindings.
"""

from __future__ import annotations

from typing import Any

from onex_change_control.validation.ac_binding_acceptance import (
    AcBindingFinding,
    check_contract_ac_bindings,
)
from onex_change_control.validation.ac_criteria import (
    criteria_by_label,
    criterion_hash,
)

_STALE_RULE = "ac_binding_stale_hash"

_BODY = """## Acceptance criteria

- AC1: the first criterion, unchanged since it was bound.
- AC2: the second criterion, REWRITTEN after it was bound.
"""

_STALE_PIN = "0" * 64


def _live_hash(label: str) -> str:
    return criterion_hash(criteria_by_label(_BODY)[label])


def _item(item_id: str, bindings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": item_id,
        "description": "Proves the criteria it claims.",
        "source": "manual",
        "status": "verified",
        "binds_ac": [b["label"] for b in bindings],
        "ac_bindings": bindings,
        "checks": [{"check_type": "command", "check_value": "true"}],
    }


def _retirement(target: str, label: str) -> dict[str, Any]:
    return {
        "id": f"retire-{target}-{label}",
        "description": "Retire a binding whose criterion was rewritten.",
        "source": "manual",
        "supersedes_ac_binding": [
            {"item": target, "label": label, "reason": "criterion rewritten"}
        ],
        "checks": [],
    }


def _contract(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ticket_id": "OMN-19999", "dod_evidence": items}


def _stale(findings: list[AcBindingFinding]) -> list[AcBindingFinding]:
    return [f for f in findings if f.rule == _STALE_RULE]


def _binding(label: str, pinned: str) -> dict[str, Any]:
    return {"label": label, "criterion_hash": pinned}


def test_stale_pin_on_a_retired_binding_is_not_refused() -> None:
    contract = _contract(
        [
            _item(
                "dod-001",
                [_binding("AC1", _live_hash("AC1")), _binding("AC2", _STALE_PIN)],
            ),
            _retirement("dod-001", "AC2"),
        ]
    )

    assert _stale(check_contract_ac_bindings("OMN-19999", contract, _BODY)) == []


def test_stale_pin_on_a_live_binding_is_still_refused() -> None:
    """Negative control: no retirement, so the gate must keep failing."""
    contract = _contract(
        [
            _item(
                "dod-001",
                [_binding("AC1", _live_hash("AC1")), _binding("AC2", _STALE_PIN)],
            ),
        ]
    )

    findings = _stale(check_contract_ac_bindings("OMN-19999", contract, _BODY))

    assert len(findings) == 1
    assert "AC2" in findings[0].message


def test_retiring_one_label_does_not_excuse_a_stale_pin_on_another() -> None:
    """Negative control: retirement is per (item, label), not per item."""
    contract = _contract(
        [
            _item(
                "dod-001",
                [_binding("AC1", _STALE_PIN), _binding("AC2", _STALE_PIN)],
            ),
            _retirement("dod-001", "AC2"),
        ]
    )

    findings = _stale(check_contract_ac_bindings("OMN-19999", contract, _BODY))

    assert len(findings) == 1
    assert "AC1" in findings[0].message


def test_retirement_on_another_item_does_not_excuse_this_items_stale_pin() -> None:
    """Negative control: a retirement naming a different item changes nothing."""
    contract = _contract(
        [
            _item("dod-001", [_binding("AC2", _STALE_PIN)]),
            _item("dod-002", [_binding("AC2", _STALE_PIN)]),
            _retirement("dod-002", "AC2"),
        ]
    )

    findings = _stale(check_contract_ac_bindings("OMN-19999", contract, _BODY))

    assert [f.subject for f in findings] == ["OMN-19999 dod-001"]


def test_unknown_criterion_on_a_retired_binding_is_not_refused() -> None:
    """A criterion the ticket dropped is withdrawn by retirement, not refused."""
    unknown = {"label": "AC9", "criterion_hash": _STALE_PIN}
    live = [_binding("AC1", _live_hash("AC1")), _binding("AC2", _live_hash("AC2"))]
    contract = _contract(
        [_item("old", [unknown]), _item("live", live), _retirement("old", "AC9")]
    )
    findings = check_contract_ac_bindings("OMN-19999", contract, _BODY)
    assert not [f for f in findings if f.rule == "ac_binding_unknown_criterion"]


def test_unknown_criterion_without_retirement_is_still_refused() -> None:
    unknown = {"label": "AC9", "criterion_hash": _STALE_PIN}
    contract = _contract([_item("old", [unknown])])
    findings = check_contract_ac_bindings("OMN-19999", contract, _BODY)
    assert [f for f in findings if f.rule == "ac_binding_unknown_criterion"]
