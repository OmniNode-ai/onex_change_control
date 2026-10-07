# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18577 — a source grep may not bind a criterion measured on a live system.

The fixture is OMN-17214's real acceptance-criteria section, and the four
criterion hashes recorded in ``contracts/OMN-17214.yaml`` are asserted against
it, so the fixture cannot silently drift from the ticket the defect was found
on. If somebody rewrites the ticket, these tests fail and say so rather than
quietly testing a sentence nobody wrote.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from onex_change_control.validation.ac_binding_acceptance import (
    BINDING_HOLD,
    BINDING_REFUSAL,
    check_contract_ac_bindings,
    refusals,
)
from onex_change_control.validation.ac_criteria import (
    criteria_by_label,
    criterion_hash,
    falsifier_of,
)
from onex_change_control.validation.proof_class import (
    criterion_is_live,
    item_is_static,
    live_terms_in,
    static_check_reason,
)

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "omn18577"
_BODY = (_FIXTURE / "omn17214_acceptance_criteria.md").read_text(encoding="utf-8")

#: The item the autobinder bound to five criteria on one source grep, and the
#: criterion hashes it recorded. Both are copied from the merged contract.
_OMN17214_ITEM_ID = "dod-OmniNode-ai-omnibase_infra-pr-3678"
_RECORDED_HASHES: dict[str, str] = {
    "AC1": "3f62121ef1468e298d416e2cfab68d802671925cd1427beaa67432182d645874",
    "AC2": "cd944456d6e5bce3dc978bbc66234caccacd3c017fbd4ef51e73a5302402e8b9",
    "AC3": "446e680e87e2652c362dc82e93ce1be0cc050b3c746a275f62f99b2b771a9924",
    "AC4": "f7bf4840e4f9efefb4ba8d3b1c53646f111439c3411532f7911e906eb7875aff",
    "AC5": "5ebe86524d9073d09c8e592661a95814732cc74c27fb5a0177739e4a965b60d5",
}
#: The grep that was bound to four live-lane measurements.
_SOURCE_GREP = (
    "gh api repos/OmniNode-ai/omnibase_infra/contents/"
    "src/omnibase_infra/runtime/observability/consumer_flow_counters.py"
    "?ref=4b110fe5fcbad2d393a279ebc3cabeec5644e72e --jq '.content' "
    "| base64 -d | grep -c 'def record_flow_output'"
)

_RULE_STATIC_LIVE = "ac_binding_static_evidence_on_live_criterion"
_RULE_RETIRED_UNBOUND = "ac_binding_criterion_retired_unbound"
_RULE_RETIREMENT_MALFORMED = "ac_binding_retirement_malformed"


def _binding(
    label: str, *, accepted: bool = True, proposed_by: str = "occ-autobind"
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "label": label,
        "criterion_hash": _RECORDED_HASHES[label],
    }
    if proposed_by:
        record["proposed_by"] = proposed_by
    if accepted:
        record["accepted_by"] = "7a850ce1-f95e-431f-b4e3-62f7449f04c0"
        record["accepted_at"] = "2026-08-30T15:27:16Z"
    return record


def _contract(
    *,
    labels: tuple[str, ...] = ("AC1", "AC2", "AC3", "AC4", "AC5"),
    accepted: bool = True,
    proposed_by: str = "occ-autobind",
    retirements: list[dict[str, Any]] | None = None,
    extra_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """OMN-17214's shape: one source-grep item carrying every binding."""
    items: list[dict[str, Any]] = [
        {
            "id": _OMN17214_ITEM_ID,
            "description": "PR #3678 — Evidence-Source autobind.",
            "source": "generated",
            "binds_ac": list(labels),
            "ac_bindings": [
                _binding(label, accepted=accepted, proposed_by=proposed_by)
                for label in labels
            ],
            "checks": [{"check_type": "command", "check_value": _SOURCE_GREP}],
        }
    ]
    if retirements is not None:
        items.append(
            {
                "id": "dod-omn17214-retire-nonprobative-bindings",
                "description": "Retire bindings the item's grep cannot settle.",
                "source": "manual",
                "supersedes_ac_binding": retirements,
                "checks": [],
            }
        )
    items.extend(extra_items or [])
    return {"ticket_id": "OMN-17214", "dod_evidence": items}


# -- AC1: the criterion classifier ------------------------------------------


def test_fixture_matches_the_criterion_hashes_the_merged_contract_recorded() -> None:
    """The fixture IS the ticket. A drift here invalidates every test below."""
    known = criteria_by_label(_BODY)
    for label, recorded in _RECORDED_HASHES.items():
        assert criterion_hash(known[label]) == recorded, (
            f"{label}'s fixture text no longer hashes to the value "
            f"contracts/OMN-17214.yaml recorded; the fixture has drifted "
            f"from the ticket"
        )


def test_omn17214_live_criteria_classify_live_and_the_hermetic_one_does_not() -> None:
    known = criteria_by_label(_BODY)
    for label in ("AC1", "AC2", "AC3", "AC4"):
        text = known[label]
        assert criterion_is_live(text, falsifier_of(text)), (
            f"{label} is a live-lane measurement and must classify live"
        )
    ac5 = known["AC5"]
    assert not criterion_is_live(ac5, falsifier_of(ac5)), (
        "AC5 is settled by a hermetic test, not by a probe; classifying it "
        "live would refuse a correct binding"
    )


def test_the_falsifier_outranks_the_prose_in_both_directions() -> None:
    """A probe falsifier makes an abstract criterion live; a test falsifier
    makes an operational-sounding one hermetic."""
    assert criterion_is_live(
        "AC9: the counter is incremented.",
        "reading the consumer group's lag on the dev lane",
    )
    assert not criterion_is_live(
        "AC9: the projection writer attributes its publish on the lane.",
        "`uv run pytest tests/unit/test_writer.py -q` passes",
    )


def test_a_falsifier_naming_both_stays_live() -> None:
    """The test marker is a counter-signal, never an override."""
    assert criterion_is_live(
        "AC9: the writer attributes its publish.",
        "`uv run pytest ...` passes AND the dev lane reads FLOWING",
    )


def test_live_terms_are_word_bounded() -> None:
    assert live_terms_in("the change was delivered to the customer") == []
    assert live_terms_in("the item was flagged for review") == []
    assert live_terms_in("read back from the live lane") == [
        "read back",
        "live",
        "lane",
    ]


def test_the_vocabulary_excludes_the_nouns_a_hermetic_test_carries() -> None:
    """Measured false positives, kept out by name so a widening is deliberate."""
    from onex_change_control.validation.proof_class import LIVE_MEASUREMENT_TERMS

    for noun in ("runtime", "window", "broker", "partition", "receipt", "restart"):
        assert noun not in LIVE_MEASUREMENT_TERMS, (
            f"{noun!r} fired on criteria a hermetic test settles; re-adding it "
            f"reintroduces a false live verdict"
        )


# -- AC2: the evidence classifier -------------------------------------------


def test_the_omn17214_source_grep_classifies_static() -> None:
    is_static, reasons = item_is_static(
        [{"check_type": "command", "check_value": _SOURCE_GREP}]
    )
    assert is_static
    assert reasons
    assert "reads the repository tree" in reasons[0]


def test_a_test_passes_check_is_not_static() -> None:
    assert not item_is_static(
        [{"check_type": "test_passes", "check_value": "uv run pytest tests/ -q"}]
    )[0]


def test_an_unreadable_command_makes_the_item_non_static() -> None:
    """Fail-OPEN on the evidence side: unknown never refuses."""
    assert (
        static_check_reason(
            {"check_type": "command", "check_value": "frobnicate --widget 7"}
        )
        is None
    )
    assert not item_is_static(
        [{"check_type": "command", "check_value": "frobnicate --widget 7"}]
    )[0]


def test_a_live_probe_marker_beats_a_source_read_marker_in_one_command() -> None:
    assert (
        static_check_reason(
            {
                "check_type": "command",
                "check_value": "docker exec pg psql -tAc 'select 1' | grep -c 1",
            }
        )
        is None
    )


def test_file_exists_is_static_by_type_matching_core_weak_proof() -> None:
    assert static_check_reason({"check_type": "file_exists", "check_value": "src/*.py"})


def test_an_item_with_no_checks_is_not_static() -> None:
    """It proves nothing, which is a different defect other rules own."""
    assert not item_is_static([])[0]


# -- AC3: the gate rule ------------------------------------------------------


def test_the_gate_reports_the_four_live_bindings_and_not_the_hermetic_one() -> None:
    findings = check_contract_ac_bindings("OMN-17214", _contract(), _BODY)
    flagged = {
        f.subject.rsplit(" ", 1)[-1]: f for f in findings if f.rule == _RULE_STATIC_LIVE
    }
    labels = sorted(
        label
        for f in findings
        if f.rule == _RULE_STATIC_LIVE
        for label in _RECORDED_HASHES
        if f"binds {label} " in f.message or f.message.startswith(label)
    )
    assert labels == ["AC1", "AC2", "AC3", "AC4"], (
        f"expected the four live-lane criteria, got {labels}"
    )
    assert flagged, "the rule produced no finding at all"


def test_the_finding_names_the_item_the_criterion_and_the_live_term() -> None:
    findings = [
        f
        for f in check_contract_ac_bindings("OMN-17214", _contract(), _BODY)
        if f.rule == _RULE_STATIC_LIVE
    ]
    assert findings
    joined = " ".join(f.message for f in findings)
    assert _OMN17214_ITEM_ID in " ".join(f.subject for f in findings)
    assert "AC3" in joined
    assert "stalled" in joined.casefold()
    assert "grep" in joined or "repository tree" in joined


def test_a_non_static_item_binding_the_same_live_criteria_is_not_flagged() -> None:
    """The positive control against a vacuous rule: swap only the check."""
    contract = _contract()
    contract["dod_evidence"][0]["checks"] = [
        {"check_type": "test_passes", "check_value": "uv run pytest tests/ -q"}
    ]
    assert not [
        f
        for f in check_contract_ac_bindings("OMN-17214", contract, _BODY)
        if f.rule == _RULE_STATIC_LIVE
    ]


# -- AC4: the severity split -------------------------------------------------


def test_a_transcribed_binding_is_reported_and_does_not_fail_the_gate() -> None:
    findings = [
        f
        for f in check_contract_ac_bindings(
            "OMN-17214", _contract(proposed_by="occ-autobind"), _BODY
        )
        if f.rule == _RULE_STATIC_LIVE
    ]
    assert findings
    assert all(f.severity == BINDING_HOLD for f in findings)
    assert not refusals(findings)


def test_a_hand_authored_accepted_binding_refuses() -> None:
    findings = [
        f
        for f in check_contract_ac_bindings(
            "OMN-17214", _contract(proposed_by=""), _BODY
        )
        if f.rule == _RULE_STATIC_LIVE
    ]
    assert findings
    assert all(f.severity == BINDING_REFUSAL for f in findings)


def test_the_severity_discriminator_reads_no_date_allowlist_or_env() -> None:
    """The flip is computed from the contract, per the module's own doctrine."""
    import ast

    path = (
        _REPO_ROOT
        / "src"
        / "onex_change_control"
        / "validation"
        / "ac_binding_acceptance.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_static_evidence_on_live_criteria"
    )
    # The CODE only. The docstring explains why there is no date or allowlist,
    # so scanning it would fail on the sentence that promises the property.
    statements = [
        node
        for node in func.body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
    ]
    code = "\n".join(ast.unparse(node) for node in statements)
    for forbidden in ("environ", "getenv", "date(", "datetime", "ALLOWLIST", "2026-"):
        assert forbidden not in code, (
            f"the discriminator reads {forbidden!r}; it must be computed from "
            f"the contract's own content"
        )


# -- AC5: the retirement shape ----------------------------------------------


def _retirement(
    label: str, *, reason: str = "the item's only check greps source"
) -> dict[str, Any]:
    return {"item": _OMN17214_ITEM_ID, "label": label, "reason": reason}


def test_a_retired_binding_stops_being_a_claim() -> None:
    contract = _contract(
        retirements=[
            _retirement(label) for label in ("AC1", "AC2", "AC3", "AC4", "AC5")
        ]
    )
    findings = check_contract_ac_bindings("OMN-17214", contract, _BODY)
    assert not [f for f in findings if f.rule == _RULE_STATIC_LIVE], (
        "a retired binding must not still be reported as a static->live claim"
    )
    retired = {
        f.message.split()[0] for f in findings if f.rule == _RULE_RETIRED_UNBOUND
    }
    assert {"AC1", "AC2", "AC3", "AC4"} <= retired


def test_the_retirement_is_read_by_this_gate_not_by_receipt_lineage() -> None:
    """The append-only rule is untouched: nothing merged is edited."""
    contract = _contract(retirements=[_retirement("AC3")])
    findings = check_contract_ac_bindings("OMN-17214", contract, _BODY)
    flagged_labels = {
        label
        for f in findings
        if f.rule == _RULE_STATIC_LIVE
        for label in ("AC1", "AC2", "AC3", "AC4")
        if label in f.message
    }
    assert "AC3" not in flagged_labels
    assert {"AC1", "AC2", "AC4"} <= flagged_labels


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param({"item": _OMN17214_ITEM_ID, "label": "AC1"}, id="no-reason"),
        pytest.param(
            {"item": _OMN17214_ITEM_ID, "label": "AC1", "reason": "  "},
            id="blank-reason",
        ),
        pytest.param(
            {"item": "dod-does-not-exist", "label": "AC1", "reason": "x"},
            id="unknown-item",
        ),
        pytest.param(
            {"item": _OMN17214_ITEM_ID, "label": "AC9", "reason": "x"},
            id="label-that-item-never-bound",
        ),
        pytest.param({"label": "AC1", "reason": "x"}, id="no-item"),
    ],
)
def test_a_malformed_retirement_refuses(entry: dict[str, Any]) -> None:
    findings = check_contract_ac_bindings(
        "OMN-17214", _contract(retirements=[entry]), _BODY
    )
    bad = [f for f in findings if f.rule == _RULE_RETIREMENT_MALFORMED]
    assert bad, "a malformed retirement must be refused, never silently ignored"
    assert all(f.severity == BINDING_REFUSAL for f in bad)


def test_a_retiring_item_that_itself_binds_static_to_live_is_still_judged() -> None:
    """A retirement cannot smuggle in a replacement the rule would refuse."""
    contract = _contract(retirements=[_retirement("AC3")])
    contract["dod_evidence"][1]["binds_ac"] = ["AC3"]
    contract["dod_evidence"][1]["ac_bindings"] = [_binding("AC3", proposed_by="")]
    contract["dod_evidence"][1]["checks"] = [
        {"check_type": "command", "check_value": _SOURCE_GREP}
    ]
    findings = check_contract_ac_bindings("OMN-17214", contract, _BODY)
    assert [
        f for f in findings if f.rule == _RULE_STATIC_LIVE and "AC3" in f.message
    ], "the replacement binding must be judged by the same rule"


# -- AC6: retirement does not launder the coverage refusal -------------------


def test_a_retired_criterion_holds_rather_than_vanishing_from_coverage() -> None:
    contract = _contract(
        retirements=[
            _retirement(label) for label in ("AC1", "AC2", "AC3", "AC4", "AC5")
        ]
    )
    findings = check_contract_ac_bindings("OMN-17214", contract, _BODY)
    retired = [f for f in findings if f.rule == _RULE_RETIRED_UNBOUND]
    assert retired, "a retired criterion must still be reported as unbound"
    assert all(f.severity == BINDING_HOLD for f in retired)
    joined = " ".join(f.message for f in retired)
    assert "retire" in joined.casefold()


def test_partial_coverage_with_no_retirement_still_refuses() -> None:
    """The existing OMN-18333 behaviour is untouched."""
    contract = _contract(labels=("AC1",))
    findings = check_contract_ac_bindings("OMN-17214", contract, _BODY)
    unbound = [f for f in findings if f.rule == "ac_binding_criterion_unbound"]
    assert unbound
    assert any(f.severity == BINDING_REFUSAL for f in unbound)


# -- AC7: the rule runs where it blocks --------------------------------------


def test_the_blocking_ci_job_names_this_test_file() -> None:
    workflow = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    job = workflow.split("  ac-binding-acceptance:", 1)[1].split("\n  ci-summary:", 1)[
        0
    ]
    assert Path(__file__).name in job, (
        "the acceptance-criterion binding gate job must run this rule's tests; "
        "without the line the rule can be unwired while its tests keep passing"
    )


def test_the_precommit_hook_runs_the_same_entrypoint() -> None:
    config = (_REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "check-ac-binding-acceptance" in config


def test_the_cli_refuses_a_hand_authored_static_live_contract(tmp_path: Path) -> None:
    """End to end through the installed console entry point, not the library."""
    contract_path = tmp_path / "OMN-17214.yaml"
    contract_path.write_text(
        yaml.safe_dump(_contract(proposed_by="")), encoding="utf-8"
    )
    bodies = tmp_path / "bodies.json"
    bodies.write_text(
        yaml.safe_dump({"OMN-17214": _BODY}).replace("\n", "\n"), encoding="utf-8"
    )
    import json

    bodies.write_text(json.dumps({"OMN-17214": _BODY}), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "onex_change_control.scripts.check_ac_binding_acceptance",
            "--ticket-bodies",
            str(bodies),
            str(contract_path),
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert _RULE_STATIC_LIVE in result.stdout + result.stderr
