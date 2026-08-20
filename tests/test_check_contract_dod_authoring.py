# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for the DoD-authoring hygiene gate (OMN-14767 / friction F-15).

Proves the gate rejects the two dishonest/unsatisfiable authoring classes —
no-op / TODO placeholder check_values and self-PR impossible-merge assertions —
without false-positiving on legitimate checks (pytest, grep, diff, weak state
reads), and that the legacy-allowlist ratchet works both directions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "validation"
    / "check_contract_dod_authoring.py"
)
_spec = importlib.util.spec_from_file_location(
    "check_contract_dod_authoring", _MODULE_PATH
)
assert _spec is not None
assert _spec.loader is not None
dod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = dod
_spec.loader.exec_module(dod)


# ---------------------------------------------------------------------------
# Class 1 — placeholder detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "# TODO: verify: tests exist and pass",  # the auto_scaffold_contract output
        "# TODO",
        "# FIXME later",
        "  # only a comment  ",
        "",
        "   ",
        "TODO: fill this in",
        "PLACEHOLDER",
        "# line one\n# line two",
    ],
)
def test_placeholder_is_rejected(value: str) -> None:
    assert dod.is_placeholder(value) is True
    assert dod.classify_check(value, "some description") is dod.Violation.PLACEHOLDER


@pytest.mark.parametrize(
    "value",
    [
        "uv run pytest tests/ -v",
        "pre-commit run --all-files",
        "gh pr diff ${PR_NUMBER} --repo ${REPO} --name-only | grep -q .",
        "grep -q '^status: PASS$' drift/dod_receipts/OMN-1/dod-1/command.yaml",
        "echo TODO && uv run pytest",  # a real command that mentions TODO in an arg
    ],
)
def test_real_check_is_not_a_placeholder(value: str) -> None:
    assert dod.is_placeholder(value) is False


# ---------------------------------------------------------------------------
# Class 2 — self-PR impossible-merge detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        '[ "$(gh pr view ${PR_NUMBER} --json state -q .state)" = "MERGED" ]',
        'test "$(gh pr view $PR_NUMBER --json state -q .state)" = MERGED',
        "gh pr view ${PR_NUMBER} --json merged -q .merged | grep -q true",
        '[ "$(gh pr view ${PR_NUMBER} --json mergedAt -q .mergedAt)" != null ]',
        '[ -n "$(gh pr view ${PR_NUMBER} --json mergedAt -q .mergedAt)" ]',
    ],
)
def test_self_pr_merged_assertion_is_rejected(value: str) -> None:
    assert dod.is_impossible_pre_merge(value, "") is True
    assert dod.classify_check(value, "") is dod.Violation.IMPOSSIBLE_PRE_MERGE


@pytest.mark.parametrize(
    "value",
    [
        # Bare field reads that always exit 0 — weak, but not impossible (they do
        # not assert; substance-floor L0 territory, not this gate's class).
        "gh pr view ${PR_NUMBER} --repo ${REPO} --json state -q .state",
        "gh pr view ${PR_NUMBER} --json mergedAt -q .mergedAt --repo ${REPO}",
        "gh pr view ${PR_NUMBER} --repo ${REPO} --json state,mergedAt",
        # A HARDCODED other PR asserted MERGED — a satisfiable dependency check.
        '[ "$(gh pr view 123 --json state -q .state)" = "MERGED" ]',
        # mergeStateStatus / mergeable must not trip the MERGED token.
        "gh pr view ${PR_NUMBER} --json mergeStateStatus,mergeable",
    ],
)
def test_non_impossible_checks_are_accepted(value: str) -> None:
    assert dod.is_impossible_pre_merge(value, "") is False


def test_description_merged_to_main_is_not_hard_gated() -> None:
    # The prose "PR merged to main" is the dominant LEGACY house style; the gate
    # deliberately does NOT reject on the description alone (deferred kill switch).
    value = "gh pr view ${PR_NUMBER} --json mergedAt -q .mergedAt --repo ${REPO}"
    assert dod.is_impossible_pre_merge(value, "PR merged to main") is False
    assert dod.GATE_MERGED_DESCRIPTION_BOILERPLATE is False


# ---------------------------------------------------------------------------
# Contract evaluation + sweep + allowlist ratchet
# ---------------------------------------------------------------------------

_CLEAN_CONTRACT = """\
---
schema_version: "1.0.0"
ticket_id: "OMN-CLEAN"
dod_evidence:
  - id: "dod-001"
    description: "Focused unit coverage added"
    checks:
      - check_type: "command"
        check_value: "uv run pytest tests/test_thing.py -v"
"""

_PLACEHOLDER_CONTRACT = """\
---
schema_version: "1.0.0"
ticket_id: "OMN-PLACEHOLD"
dod_evidence:
  - id: "dod-001"
    description: "Tests pass"
    checks:
      - check_type: "command"
        check_value: "uv run pytest tests/ -v"
  - id: "dod-002"
    description: "Verify the thing"
    checks:
      - check_type: "command"
        check_value: "# TODO: verify: the thing works"
"""

_IMPOSSIBLE_CONTRACT = """\
---
schema_version: "1.0.0"
ticket_id: "OMN-IMPOSSIBLE"
dod_evidence:
  - id: "dod-002"
    description: "PR merged to main"
    checks:
      - check_type: "command"
        check_value: 'gh pr view ${PR_NUMBER} --json state | grep -q MERGED'
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_clean_contract_passes(tmp_path: Path) -> None:
    result = dod.evaluate_contract(_write(tmp_path, "OMN-CLEAN.yaml", _CLEAN_CONTRACT))
    assert result.passed is True


def test_placeholder_contract_fails(tmp_path: Path) -> None:
    result = dod.evaluate_contract(
        _write(tmp_path, "OMN-PLACEHOLD.yaml", _PLACEHOLDER_CONTRACT)
    )
    assert result.passed is False
    assert result.findings[0].violation is dod.Violation.PLACEHOLDER


def test_impossible_contract_fails(tmp_path: Path) -> None:
    result = dod.evaluate_contract(
        _write(tmp_path, "OMN-IMPOSSIBLE.yaml", _IMPOSSIBLE_CONTRACT)
    )
    assert result.passed is False
    assert result.findings[0].violation is dod.Violation.IMPOSSIBLE_PRE_MERGE


def test_sweep_grandfathers_listed_contract(tmp_path: Path) -> None:
    path = _write(tmp_path, "OMN-PLACEHOLD.yaml", _PLACEHOLDER_CONTRACT)
    report = dod.sweep([path], allowlist={"OMN-PLACEHOLD"})
    assert not report.failures
    assert report.grandfathered == ["OMN-PLACEHOLD"]


def test_sweep_flags_new_violation_not_in_allowlist(tmp_path: Path) -> None:
    path = _write(tmp_path, "OMN-IMPOSSIBLE.yaml", _IMPOSSIBLE_CONTRACT)
    report = dod.sweep([path], allowlist=set())
    assert [r.ticket_id for r in report.failures] == ["OMN-IMPOSSIBLE"]


def test_sweep_ratchet_flags_stale_allowlist_entry(tmp_path: Path) -> None:
    # A now-clean contract that is still listed must be reported stale (ratchet).
    path = _write(tmp_path, "OMN-CLEAN.yaml", _CLEAN_CONTRACT)
    report = dod.sweep([path], allowlist={"OMN-CLEAN"})
    assert report.stale_allowlist == ["OMN-CLEAN"]


def test_main_all_corpus_is_green_with_shipped_allowlist() -> None:
    # The full committed contracts/ corpus must pass under the shipped allowlist,
    # so the gate does not wedge CI on pre-existing debt (mirrors the substance
    # floor's --all sweep).
    import os

    repo_root = _MODULE_PATH.parents[2]
    cwd = Path.cwd()
    os.chdir(repo_root)
    try:
        assert dod.main(["--all"]) == 0
    finally:
        os.chdir(cwd)
