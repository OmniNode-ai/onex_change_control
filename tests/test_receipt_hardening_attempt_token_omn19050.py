# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""OMN-19050 — an attempt-scoped supersede record must be visible and rank first.

Measured on omnimarket#2751 (2026-09-21): a supersede record recording FAIL
latched its receipt key forever, because the record was named for its consumer
PR alone and a second execution had nowhere to be filed. The repair mints
``<check>.supersede.<pr>.<n>.yaml`` for a re-executed check, and three readers
had to learn that shape. This module covers this repository's reader.

Two things were wrong here, both the same shape as the defect in
``omnibase_core.validation.validator_receipt_supersession``:

1. ``_SUPERSEDE_TOKEN_RE`` excluded a dot, so an attempt-scoped record produced
   a ``None`` token and every caller skipped it with no error.
2. ``_active_supersession_candidate`` and ``_repaired_targets`` ordered by
   ``int(token)`` behind a ``str.isdigit`` filter, which refused a dotted token
   outright. The FAIL record a correction was filed against therefore stayed
   the active one, and the gate validated the wrong record.

The agreement test at the bottom is the one that matters most. This module
decides which record gets VALIDATED and ``validator_receipt_supersession``
decides which record is ACTIVE for merge eligibility. If the two order records
differently, a record can pass the gate here and then not be the one that
decides the merge there, which is a silent gate.

That agreement is pinned as an explicit table rather than by importing the
core helper, because this repository pins ``omnibase-core`` from the registry
(0.47.17 at the time of writing) and the fix ships in a later release — an
import would make this module un-collectable until the pin moves. The same
table is pinned on the core side by
``tests/unit/validation/test_receipt_supersession_fail_latch_omn_19050.py``,
so both ends assert the same contract from their own repository. Replacing
this table with a direct behavioural comparison against ``resolve_supersession``
is the right shape once the pin carries the fix; that is a follow-up, and
until then the duplication is deliberate and named rather than accidental.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.validation.check_receipt_hardening import (
    _active_supersession_candidate,
    _supersede_sequence,
    _supersede_token,
)

TICKET = "OMN-18868"
ITEM = "dod-occ-diff-derived-behavior-proof-pr-2751"
CHECK = "test_passes"


def _write_chain(root: Path, tokens: list[str]) -> Path:
    """A base receipt plus one supersede record per token, all naming the base."""
    key_dir = root / "drift" / "dod_receipts" / TICKET / ITEM
    key_dir.mkdir(parents=True, exist_ok=True)
    base = key_dir / f"{CHECK}.yaml"
    base.write_text("status: PENDING\n", encoding="utf-8")
    for token in tokens:
        (key_dir / f"{CHECK}.supersede.{token}.yaml").write_text(
            yaml.safe_dump({"supersedes": base.as_posix()}, sort_keys=True),
            encoding="utf-8",
        )
    return base


@pytest.mark.unit
def test_attempt_scoped_filename_yields_a_token() -> None:
    """The dotted token was invisible: the extractor returned None."""
    assert _supersede_token(Path(f"{CHECK}.supersede.2751.0002.yaml")) == "2751.0002"
    assert _supersede_token(Path(f"{CHECK}.supersede.2751.yaml")) == "2751"


@pytest.mark.unit
def test_sequence_orders_an_attempt_after_the_record_it_extends() -> None:
    bare = _supersede_sequence("2751")
    attempt = _supersede_sequence("2751.0002")

    assert bare == (2751,)
    assert attempt == (2751, 2)
    assert bare is not None
    assert attempt is not None
    assert bare < attempt


@pytest.mark.unit
def test_non_numeric_token_stays_out_of_the_ordering() -> None:
    """Unchanged behaviour: a non-numeric token never participated."""
    assert _supersede_sequence("2010-head") is None
    assert _supersede_sequence(None) is None
    assert _supersede_sequence("") is None


@pytest.mark.unit
def test_active_candidate_is_the_attempt_not_the_record_it_corrects(
    tmp_path: Path,
) -> None:
    """The defect: the correction was skipped and the corrected record stayed active."""
    base = _write_chain(tmp_path, ["2751", "2751.0002"])

    active = _active_supersession_candidate(base)

    assert active is not None
    assert active.name == f"{CHECK}.supersede.2751.0002.yaml"


@pytest.mark.unit
def test_highest_attempt_wins_across_several(tmp_path: Path) -> None:
    base = _write_chain(tmp_path, ["2751", "2751.0002", "2751.0010", "2751.0003"])

    active = _active_supersession_candidate(base)

    assert active is not None
    assert active.name == f"{CHECK}.supersede.2751.0010.yaml"


@pytest.mark.unit
def test_bare_numeric_chain_resolves_exactly_as_before(tmp_path: Path) -> None:
    """Backward compatibility: a 1-tuple compares as int(token) did."""
    base = _write_chain(tmp_path, ["0001", "0002", "0010"])

    active = _active_supersession_candidate(base)

    assert active is not None
    assert active.name == f"{CHECK}.supersede.0010.yaml"


@pytest.mark.unit
def test_no_supersession_returns_none(tmp_path: Path) -> None:
    base = _write_chain(tmp_path, [])

    assert _active_supersession_candidate(base) is None


# The cross-repo contract, spelled once. ``_sequence_key`` in
# omnibase_core.validation.validator_receipt_supersession must return exactly
# this for the same input, and that repo's OMN-19050 test module pins it there.
SEQUENCE_CONTRACT: list[tuple[str, tuple[int, ...] | None]] = [
    ("2751", (2751,)),
    ("2751.0002", (2751, 2)),
    ("0001", (1,)),
    ("9999.0010.0003", (9999, 10, 3)),
    ("2010-head", None),
    ("", None),
    ("abc", None),
]


@pytest.mark.unit
@pytest.mark.parametrize(("token", "expected"), SEQUENCE_CONTRACT)
def test_ordering_matches_the_cross_repo_contract(
    token: str, expected: tuple[int, ...] | None
) -> None:
    """The gate and the eligibility resolver must rank records identically.

    A disagreement means the gate validates one record while the merge is
    decided by another -- the record that passed review is not the record
    that counts.
    """
    assert _supersede_sequence(token) == expected
