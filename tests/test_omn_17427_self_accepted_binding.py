# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-17427 — a binding's author cannot accept their own binding."""

from __future__ import annotations

import pytest

from onex_change_control.validation.ac_binding_acceptance import (
    BINDING_REFUSAL,
    AcBindingFinding,
    _actor_identities,
    check_contract_ac_bindings,
    check_local_ac_bindings,
)
from onex_change_control.validation.ac_criteria import (
    criteria_by_label,
    criterion_hash,
)

_TICKET = "OMN-17427"
_RULE = "ac_binding_self_accepted"
_BODY = "## Acceptance criteria\n\n- AC1: a second lane accepts the binding.\n"


def _item(
    item_id: str = "dod-001",
    *,
    proposed_by: str | None = "evid-B13-2a21",
    accepted_by: str | None = "evid-B13-2a21",
    label: str = "AC1",
) -> dict[str, object]:
    binding: dict[str, object] = {
        "label": label,
        "criterion_hash": criterion_hash(criteria_by_label(_BODY)["AC1"]),
    }
    if proposed_by is not None:
        binding["proposed_by"] = proposed_by
    if accepted_by is not None:
        binding["accepted_by"] = accepted_by
        binding["accepted_at"] = "2026-10-03T00:40:00Z"
    return {
        "id": item_id,
        "description": "Proves the criterion it claims.",
        "source": "manual",
        "status": "verified",
        "binds_ac": [label],
        "ac_bindings": [binding],
        "checks": [{"check_type": "command", "check_value": "true"}],
    }


def _hosted(contract: dict[str, object]) -> list[AcBindingFinding]:
    """The self-acceptance findings the hosted gate reports for ``contract``."""
    findings = check_contract_ac_bindings(_TICKET, contract, _BODY)
    return [finding for finding in findings if finding.rule == _RULE]


def _contract(*items: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "ticket_id": _TICKET,
        "title": "fixture",
        "dod_evidence": list(items),
    }


@pytest.mark.parametrize(
    ("actor", "expected"),
    [
        ("", frozenset()),
        ("  ", frozenset()),
        (
            " Mac-Occ-Contracts@MAC ",
            frozenset({"mac-occ-contracts@mac", "mac-occ-contracts"}),
        ),
        ("lane@worker@host", frozenset({"lane@worker@host", "lane@worker"})),
        (
            "Claude:Opus5 lane=MAC-OCC-CONTRACTS, lane=verify-B13-2a21.)",
            frozenset(
                {
                    "claude:opus5 lane=mac-occ-contracts, lane=verify-b13-2a21.)",
                    "mac-occ-contracts",
                    "verify-b13-2a21",
                }
            ),
        ),
    ],
)
def test_actor_identities(actor: str, expected: frozenset[str]) -> None:
    assert _actor_identities(actor) == expected


@pytest.mark.parametrize("actor", ["mac-occ-contracts", "evid-B13-2a21"])
def test_self_accepted_binding_is_refused(actor: str) -> None:
    findings = _hosted(_contract(_item(proposed_by=actor, accepted_by=actor)))

    assert [f.rule for f in findings] == [_RULE]
    assert findings[0].severity == BINDING_REFUSAL
    assert findings[0].subject == f"{_TICKET} dod-001"
    assert "AC1" in findings[0].message
    assert repr(actor) in findings[0].message
    assert "second lane re-run the bound check" in findings[0].message


def test_host_suffix_and_lane_token_identify_the_same_lane() -> None:
    contract = _contract(
        _item(
            proposed_by="claude:opus5:subagent lane=mac-occ-contracts",
            accepted_by="mac-occ-contracts@mac",
        )
    )

    assert [f.rule for f in _hosted(contract)] == [_RULE]


@pytest.mark.parametrize(
    ("proposed_by", "accepted_by"),
    [
        ("evid-B13-2a21", "verify-B13-2a21"),
        ("occ-autobind", "12345678-1234-4321-8123-123456789abc"),
        ("evid-B13-2a21", None),
        (None, "evid-B13-2a21"),
    ],
    ids=["different-lane", "autobind-creator", "draft", "no-proposer"],
)
def test_legitimate_acceptance_and_drafts_are_unchanged(
    proposed_by: str | None, accepted_by: str | None
) -> None:
    contract = _contract(_item(proposed_by=proposed_by, accepted_by=accepted_by))

    assert _hosted(contract) == []


@pytest.mark.parametrize("verifier_first", [False, True])
def test_appended_verifier_re_acceptance_clears_the_refusal(
    *,
    verifier_first: bool,
) -> None:
    original = _item()
    verifier = _item("dod-002", accepted_by="verify-B13-2a21", label="ac-1")
    items = [verifier, original] if verifier_first else [original, verifier]

    assert _hosted(_contract(*items)) == []


def _retirement(item_id: str) -> dict[str, object]:
    return {
        "id": "retire-binding",
        "supersedes_ac_binding": [
            {"item": item_id, "label": "ac-1", "reason": "replace author acceptance"}
        ],
    }


def test_retired_self_accepted_binding_is_not_refused() -> None:
    contract = _contract(_item(), _retirement("dod-001"))

    assert _hosted(contract) == []


def test_retired_verifier_acceptance_does_not_clear_the_refusal() -> None:
    contract = _contract(
        _item(),
        _item("dod-002", accepted_by="verify-B13-2a21"),
        _retirement("dod-002"),
    )

    findings = _hosted(contract)

    assert [f.rule for f in findings] == [_RULE]
    assert findings[0].subject == f"{_TICKET} dod-001"


@pytest.mark.parametrize(
    ("accepted_by", "label", "expected_items"),
    [
        ("evid-B13-2a21", "AC1", ["dod-001", "dod-002"]),
        (None, "AC1", ["dod-001"]),
        ("verify-B13-2a21", "AC2", ["dod-001"]),
    ],
    ids=["self-accepted", "draft", "different-label"],
)
def test_only_verifier_acceptance_of_the_same_label_clears_a_refusal(
    accepted_by: str | None, label: str, expected_items: list[str]
) -> None:
    second_item = _item("dod-002", accepted_by=accepted_by, label=label)
    findings = _hosted(_contract(_item(), second_item))

    assert [f.rule for f in findings] == [_RULE] * len(expected_items)
    assert [f.subject for f in findings] == [
        f"{_TICKET} {item_id}" for item_id in expected_items
    ]


def test_local_mode_does_not_sweep_merged_self_acceptance() -> None:
    """CI runs the local half with --all-files over every merged contract."""
    findings = check_local_ac_bindings(_TICKET, _contract(_item()))

    assert _RULE not in [f.rule for f in findings]


def test_hosted_mode_refuses_self_acceptance_with_no_ticket_body() -> None:
    findings = check_contract_ac_bindings(_TICKET, _contract(_item()), None)
    findings = [f for f in findings if f.rule == _RULE]

    assert [f.rule for f in findings] == [_RULE]
    assert findings[0].severity == BINDING_REFUSAL
    assert findings[0].subject == f"{_TICKET} dod-001"
