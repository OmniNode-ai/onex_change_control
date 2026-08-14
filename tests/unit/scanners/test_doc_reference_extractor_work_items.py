# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for the uncited-work-item-row extractor (OMN-15105).

Reproduces the exact 2026-07-25 phantom-dispatch pattern: ROLLING_SEVEN_DAY_PLAN.md
rows A5/A7 described already-merged fixes as open work, citing zero OMN ticket
or PR reference anywhere in the row -- so no live-state checker had anything to
resolve against. These tests seed that shape directly (not paraphrased) and
prove the extractor flags it, plus prove the negative controls that must NOT
be flagged (fully-cited rows, and non-table prose).
"""

from __future__ import annotations

from onex_change_control.enums.enum_doc_reference_type import EnumDocReferenceType
from onex_change_control.scanners.doc_reference_extractor import (
    extract_uncited_work_item_rows,
)

# Reproduces docs/plans/ROLLING_SEVEN_DAY_PLAN.md line 49 (A5) verbatim in shape:
# a `| LABEL |` work-item row describing a defect as unresolved, citing no
# OMN-XXXX ticket and no PR anywhere in the row.
_A5_STYLE_ROW = (
    "| A5 | **Repair the observation chain -- N=10 is unreachable today.** "
    "`handler_occ_attestation_observe.py:313-335` calls `rest_json_array` on "
    "`/commits/{sha}/check-runs`, which returns an **object**; it raises and is "
    "swallowed to `False`. | Integration test against the real response shape |"
)

# Reproduces line 51 (A7): claims a field is missing, cites nothing.
_A7_STYLE_ROW = (
    "| A7 | **Representative N=10** -- composition is currently "
    "**unrepresentable** (`ModelOccObservationRecord` has no such field). "
    "| Materialized projection, not a count |"
)


def test_row_with_zero_citations_is_flagged() -> None:
    lines = [_A5_STYLE_ROW]

    results = extract_uncited_work_item_rows("plan.md", lines)

    assert len(results) == 1
    finding = results[0]
    assert finding.reference_type == EnumDocReferenceType.UNCITED_WORK_ITEM
    assert finding.raw_text.startswith("A5:")
    assert finding.line_number == 1


def test_second_uncited_row_a7_is_also_flagged() -> None:
    results = extract_uncited_work_item_rows("plan.md", [_A7_STYLE_ROW])

    assert len(results) == 1
    assert results[0].raw_text.startswith("A7:")


def test_row_citing_an_omn_ticket_is_not_flagged() -> None:
    row = (
        "| A2 | **Root-cause DONE.** OMN-14893 Done 2026-07-24 "
        "(`omnimarket#1869` MERGED). | Confirm on the next real PR |"
    )

    assert extract_uncited_work_item_rows("plan.md", [row]) == []


def test_row_citing_only_a_bare_pr_number_is_not_flagged() -> None:
    row = "| B7 | Immutable readiness packet, teardown readback #2372. | proof |"

    assert extract_uncited_work_item_rows("plan.md", [row]) == []


def test_row_citing_only_a_repo_qualified_pr_is_not_flagged() -> None:
    row = "| A8 | DONE -- `omnibase_core#1476` MERGED 00:46:29Z. | Landed |"

    assert extract_uncited_work_item_rows("plan.md", [row]) == []


def test_table_header_and_separator_rows_are_not_flagged() -> None:
    lines = [
        "| # | Work | Proof |",
        "|---|---|---|",
    ]

    assert extract_uncited_work_item_rows("plan.md", lines) == []


def test_ticket_id_labeled_table_is_not_a_work_item_row() -> None:
    # docs/plans/ROLLING_SEVEN_DAY_PLAN.md §4's distributed-validation subtable
    # uses the ticket id itself as the label -- already inherently cited,
    # and not the `[A-Z][0-9]+` lettered-item convention this check targets.
    row = "| OMN-14976 | Push-validation Contract v2 (born DRAFT) | 5 | -- |"

    assert extract_uncited_work_item_rows("plan.md", [row]) == []


def test_non_table_prose_is_not_flagged() -> None:
    lines = [
        "This is ordinary prose describing A5 with no table syntax at all.",
    ]

    assert extract_uncited_work_item_rows("plan.md", lines) == []


def test_no_freshness_annotation_suppresses_the_flag() -> None:
    lines = [
        "<!-- no-freshness-check -->",
        _A5_STYLE_ROW,
    ]

    assert extract_uncited_work_item_rows("plan.md", lines) == []
