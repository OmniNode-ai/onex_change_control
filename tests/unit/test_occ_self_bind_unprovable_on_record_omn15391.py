# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15391: OCC self-bind stamps are UNPROVABLE-ON-RECORD, and this proves it.

OCC#5481 replaced eight ``${PR_NUMBER}`` self-bind placeholders with

    gh pr view <N> --repo OmniNode-ai/onex_change_control --json number \
      --jq '.number == <N>' | grep -qx true

which asserts ``N == N`` -- true by construction for every PR that exists, and
incapable of going RED for any product reason. All eight were executed at
review time and all eight returned rc=0.

The obvious repair is to swap in a "real" binding check. This module proves,
by execution against the live OMN-14505/OMN-15309 admissibility predicate, that
NO such check exists for this claim -- the two exhaustive candidate shapes are
both refused, for two different and individually correct reasons:

1. Read the companion PR's file list and assert it carries this ticket's
   contract. REFUSED as ``INSIDE_OWN_DIFF``: it reads back the very
   contract/receipt tree the evidence author writes, so its verdict is decided
   by text this PR authors.
2. Read the companion PR's metadata (``/pulls/<N>``) and assert it merged.
   REFUSED as ``INSIDE_OWN_DIFF``: PR metadata proves the change exists and
   merged, which is a fact about the change itself, not about anything it
   claims to have made true.

An OCC self-bind stamp is, by construction, a statement about OCC's own tree
and about a PR's own metadata. Both halves are exactly what the predicate
refuses. So the honest record is not a better command -- it is the recorded
finding that the binding is unprovable on record, mirroring the operator's
OMN-15376 amend-on-record ruling: say so explicitly rather than manufacture a
vacuous command that passes.

These tests go RED if the predicate is ever changed to admit either shape. That
is the intended signal: it would mean the unprovability claim no longer holds
and the eight stamps should be revisited, not that this file needs relaxing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import lint_contract_check_values as linter  # noqa: E402

from onex_change_control.validation.evidence_admissibility import (  # noqa: E402
    EnumAdmissibilityRule,
    classify_evidence_item,
)

_OCC = "OmniNode-ai/onex_change_control"


def _files_list_shape(pr: int, ticket: str) -> str:
    return (
        f"gh api repos/{_OCC}/pulls/{pr}/files --paginate "
        f"--jq '.[].filename' | grep -qx 'contracts/{ticket}.yaml'"
    )


def _metadata_shape(pr: int) -> str:
    return (
        f"gh api repos/{_OCC}/pulls/{pr} --jq '.merged_at' | grep -qE '^20[0-9][0-9]-'"
    )


def _shipped_tautology(pr: int) -> str:
    return (
        f"gh pr view {pr} --repo {_OCC} --json number "
        f"--jq '.number == {pr}' | grep -qx true"
    )


def _assert_unprovable(pr: int, ticket: str) -> None:
    """Both candidate binding shapes are refused; the shipped one is a tautology."""
    files_verdict = classify_evidence_item("command", _files_list_shape(pr, ticket))
    assert not files_verdict.admissible, (
        f"PR #{pr}: the file-list binding shape is now ADMISSIBLE. The "
        "unprovability claim recorded in contracts/"
        f"{ticket}.yaml no longer holds -- revisit that stamp instead of "
        "relaxing this assertion."
    )
    assert files_verdict.rule is EnumAdmissibilityRule.INSIDE_OWN_DIFF

    meta_verdict = classify_evidence_item("command", _metadata_shape(pr))
    assert not meta_verdict.admissible, (
        f"PR #{pr}: the PR-metadata binding shape is now ADMISSIBLE. See the "
        "note above -- revisit the stamp, do not relax this."
    )
    assert meta_verdict.rule is EnumAdmissibilityRule.INSIDE_OWN_DIFF

    # The shape OCC#5481 actually shipped is a tautology, independently of
    # admissibility: the predicate cannot see it, Rule C can.
    assert (
        linter._tautological_selfcheck_violation(_shipped_tautology(pr)) is not None
    ), f"PR #{pr}: Rule C no longer flags the shipped `N == N` self-comparison"


@pytest.mark.unit
def test_pr_5408_omn_14979_binding_unprovable() -> None:
    """Binding OMN-14979 to OCC companion PR #5408 admits no admissible check."""
    _assert_unprovable(5408, "OMN-14979")


@pytest.mark.unit
def test_pr_5415_omn_14980_binding_unprovable() -> None:
    """Binding OMN-14980 to OCC companion PR #5415 admits no admissible check."""
    _assert_unprovable(5415, "OMN-14980")


@pytest.mark.unit
def test_pr_5414_omn_15299_binding_unprovable() -> None:
    """Binding OMN-15299 to OCC companion PR #5414 admits no admissible check."""
    _assert_unprovable(5414, "OMN-15299")


@pytest.mark.unit
def test_pr_5473_omn_15362_binding_unprovable() -> None:
    """Binding OMN-15362 to OCC companion PR #5473 admits no admissible check."""
    _assert_unprovable(5473, "OMN-15362")


@pytest.mark.unit
def test_pr_5407_omn_15365_binding_unprovable() -> None:
    """Binding OMN-15365 to OCC companion PR #5407 admits no admissible check."""
    _assert_unprovable(5407, "OMN-15365")


@pytest.mark.unit
def test_pr_5413_omn_15366_binding_unprovable() -> None:
    """Binding OMN-15366 to OCC companion PR #5413 admits no admissible check."""
    _assert_unprovable(5413, "OMN-15366")


@pytest.mark.unit
def test_pr_5411_omn_15370_binding_unprovable() -> None:
    """Binding OMN-15370 to OCC companion PR #5411 admits no admissible check."""
    _assert_unprovable(5411, "OMN-15370")


@pytest.mark.unit
def test_pr_5418_omn_15375_binding_unprovable() -> None:
    """Binding OMN-15375 to OCC companion PR #5418 admits no admissible check."""
    _assert_unprovable(5418, "OMN-15375")
