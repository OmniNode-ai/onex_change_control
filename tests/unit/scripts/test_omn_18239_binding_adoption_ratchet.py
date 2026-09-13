# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""OMN-18239 — acceptance-criterion binding adoption may not go backwards.

The plan's criterion for this item is a TREND: contracts declaring an accepted
binding rise week over week from the live baseline. No gate can assert a trend
on one run, and one that pretended to would be worse than none.

What a gate can do is make the trend mechanical in the one direction available
to it. The counts are frozen in a baseline file under `.onex_ratchets/` and a run
measuring fewer than a frozen floor fails, so the number in the
repository is always a measurement somebody made in the pull request that
changed it rather than a number somebody remembered.

This module runs over EVERY contract, in the unconditional corpus-ratchet CI
job, for the same reason the Rule A/B/C/D/E ratchets do: the per-contract hooks
are changed-files scoped and cannot see a contract the pull request does not
touch, so an adoption regression elsewhere in the corpus would be invisible to
them.

Three counts, because they answer three different questions, and counting them
separately is what stops the headline number from being satisfied by the cheap
half: a proposer can raise `with_records` on its own, and only a reviewer can
raise `with_acceptance`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from onex_change_control.validation.binding_adoption import (
    count_binding_adoption,
    load_baseline,
    render_regression,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_DIR = REPO_ROOT / "contracts"
BASELINE_PATH = (
    REPO_ROOT / ".onex_ratchets" / "omn_18239_binding_adoption_baseline.yaml"
)


def test_the_baseline_file_exists_and_parses() -> None:
    """A missing baseline is an error, never an implicit floor of zero.

    Defaulting to zero would turn "somebody deleted the baseline" into "every
    count is fine", which is the shape of a gate reporting green because it did
    not run.
    """
    floors = load_baseline(BASELINE_PATH)

    assert set(floors) == {"declaring", "with_records", "with_acceptance"}
    assert all(value >= 0 for value in floors.values())


def test_adoption_has_not_gone_backwards() -> None:
    """THE RATCHET. Grow-only across the whole contract corpus."""
    measured = count_binding_adoption(CONTRACTS_DIR)
    floors = load_baseline(BASELINE_PATH)

    regression = render_regression(measured, floors)

    assert regression == "", regression


def test_the_corpus_total_is_not_frozen() -> None:
    """Freezing it would fire on every unrelated contract anybody adds."""
    document = yaml.safe_load(BASELINE_PATH.read_text(encoding="utf-8"))

    assert isinstance(document, dict)
    assert "contracts" not in document


def test_the_count_parses_rather_than_greps(tmp_path: Path) -> None:
    """A contract whose PROSE names the field does not declare one.

    This ticket's own contract mentions `binds_ac` in its description. A
    substring count would report the documentation as adoption, and the number
    the plan reads would be inflated by the tickets that talk about the
    mechanism.
    """
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "OMN-1.yaml").write_text(
        yaml.safe_dump(
            {
                "ticket_id": "OMN-1",
                "dod_evidence": [
                    {
                        "id": "dod-1",
                        "description": "explains what binds_ac and ac_bindings do",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    measured = count_binding_adoption(contracts)

    assert measured.contracts == 1
    assert measured.declaring == 0
    assert measured.with_records == 0
    assert measured.with_acceptance == 0


def test_each_count_measures_its_own_question(tmp_path: Path) -> None:
    """Declaring, recorded and accepted are three facts, not one."""
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "OMN-1.yaml").write_text(
        yaml.safe_dump(
            {
                "ticket_id": "OMN-1",
                "dod_evidence": [
                    {"id": "a", "description": "claims only", "binds_ac": ["AC1"]}
                ],
            }
        ),
        encoding="utf-8",
    )
    (contracts / "OMN-2.yaml").write_text(
        yaml.safe_dump(
            {
                "ticket_id": "OMN-2",
                "dod_evidence": [
                    {
                        "id": "a",
                        "description": "a draft proposal, nobody accepted it",
                        "binds_ac": ["AC1"],
                        "ac_bindings": [{"label": "AC1", "criterion_hash": "a" * 64}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (contracts / "OMN-3.yaml").write_text(
        yaml.safe_dump(
            {
                "ticket_id": "OMN-3",
                "dod_evidence": [
                    {
                        "id": "a",
                        "description": "accepted",
                        "binds_ac": ["AC1"],
                        "ac_bindings": [
                            {
                                "label": "AC1",
                                "criterion_hash": "a" * 64,
                                "accepted_by": "a-reviewer",
                                "accepted_at": "2026-09-12T21:00:00Z",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    measured = count_binding_adoption(contracts)

    assert measured.declaring == 3
    assert measured.with_records == 2
    # Only the reviewer's one counts. A proposer cannot raise this number.
    assert measured.with_acceptance == 1


def test_a_regression_is_reported_with_what_was_lost() -> None:
    """The control: the ratchet must be able to fail, and say what fell.

    A ratchet whose failure path nothing exercises is a ratchet nobody knows is
    wired.
    """
    contracts = Path(__file__).parent  # deliberately holds no contracts
    measured = count_binding_adoption(contracts)

    regression = render_regression(
        measured, {"declaring": 67, "with_records": 0, "with_acceptance": 0}
    )

    assert "went BACKWARDS" in regression
    assert "declaring: baseline 67, measured 0" in regression


def test_holding_steady_is_not_a_regression() -> None:
    """Grow-only means grow-or-hold. Equality passes."""
    measured = count_binding_adoption(CONTRACTS_DIR)

    assert render_regression(measured, measured.as_baseline()) == ""
