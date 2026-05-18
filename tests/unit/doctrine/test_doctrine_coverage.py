# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for the doctrine coverage generator."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.generate_doctrine_coverage import (
    _collect_job_names,
    _detect_regression,
    _effective_coverage,
    generate_coverage_table,
)

_WORKFLOWS_DIR = Path(__file__).parent.parent.parent.parent / ".github" / "workflows"


def _make_clause(
    clause_id: str,
    coverage: str,
    ci_gate: str | None,
) -> dict[str, object]:
    return {
        "clause_id": clause_id,
        "title": f"Test clause {clause_id}",
        "description": "Test",
        "doctrine_section": "1",
        "check": "test_check",
        "coverage": coverage,
        "ci_gate": ci_gate,
    }


# --- _effective_coverage ---


@pytest.mark.unit
def test_uncovered_when_no_ci_gate() -> None:
    clause = _make_clause("DT-001", "uncovered", None)
    result = _effective_coverage(clause, {"Some Job"})
    assert result == "UNCOVERED"


@pytest.mark.unit
def test_enforced_when_job_exists() -> None:
    clause = _make_clause("DT-001", "enforced", "verify/Run Receipt-Gate")
    result = _effective_coverage(clause, {"Run Receipt-Gate", "Other Job"})
    assert result == "ENFORCED"


@pytest.mark.unit
def test_advisory_when_declared_advisory_and_job_exists() -> None:
    clause = _make_clause("DT-001", "advisory", "verify/Some Advisory Check")
    result = _effective_coverage(clause, {"Some Advisory Check"})
    assert result == "ADVISORY"


@pytest.mark.unit
def test_uncovered_when_job_not_found() -> None:
    clause = _make_clause("DT-001", "enforced", "verify/Missing Job")
    result = _effective_coverage(clause, {"Run Receipt-Gate", "Type Check"})
    assert result == "UNCOVERED"


@pytest.mark.unit
def test_uncovered_when_empty_job_set() -> None:
    clause = _make_clause("DT-001", "enforced", "verify/Run Receipt-Gate")
    result = _effective_coverage(clause, set())
    assert result == "UNCOVERED"


# --- _detect_regression ---


@pytest.mark.unit
def test_no_regression_when_enforced_gate_present() -> None:
    clauses = [_make_clause("DT-001", "enforced", "verify/Run Receipt-Gate")]
    effective = {"DT-001": "ENFORCED"}
    regressions = _detect_regression(clauses, effective)
    assert regressions == []


@pytest.mark.unit
def test_regression_detected_when_enforced_clause_uncovered() -> None:
    clauses = [_make_clause("DT-001", "enforced", "verify/Run Receipt-Gate")]
    effective = {"DT-001": "UNCOVERED"}
    regressions = _detect_regression(clauses, effective)
    assert "DT-001" in regressions


@pytest.mark.unit
def test_no_regression_for_uncovered_clauses() -> None:
    clauses = [_make_clause("DT-002", "uncovered", None)]
    effective = {"DT-002": "UNCOVERED"}
    regressions = _detect_regression(clauses, effective)
    assert regressions == []


@pytest.mark.unit
def test_multiple_regressions_detected() -> None:
    clauses = [
        _make_clause("DT-001", "enforced", "verify/Gate A"),
        _make_clause("DT-002", "enforced", "verify/Gate B"),
        _make_clause("DT-003", "uncovered", None),
    ]
    effective = {"DT-001": "UNCOVERED", "DT-002": "UNCOVERED", "DT-003": "UNCOVERED"}
    regressions = _detect_regression(clauses, effective)
    assert set(regressions) == {"DT-001", "DT-002"}


# --- generate_coverage_table ---


@pytest.mark.unit
def test_coverage_table_has_all_clauses() -> None:
    clauses = [
        _make_clause("DT-001", "enforced", "verify/Run Receipt-Gate"),
        _make_clause("DT-002", "uncovered", None),
    ]
    markdown, _effective = generate_coverage_table(clauses, {"Run Receipt-Gate"})
    assert "DT-001" in markdown
    assert "DT-002" in markdown


@pytest.mark.unit
def test_coverage_table_marks_enforced() -> None:
    clauses = [_make_clause("DT-001", "enforced", "verify/Run Receipt-Gate")]
    markdown, effective = generate_coverage_table(clauses, {"Run Receipt-Gate"})
    assert effective["DT-001"] == "ENFORCED"
    assert "ENFORCED" in markdown


@pytest.mark.unit
def test_coverage_table_marks_uncovered() -> None:
    clauses = [_make_clause("DT-002", "uncovered", None)]
    markdown, effective = generate_coverage_table(clauses, {"Run Receipt-Gate"})
    assert effective["DT-002"] == "UNCOVERED"
    assert "UNCOVERED" in markdown


@pytest.mark.unit
def test_coverage_table_marks_advisory() -> None:
    clauses = [_make_clause("DT-003", "advisory", "verify/Advisory Check")]
    markdown, effective = generate_coverage_table(clauses, {"Advisory Check"})
    assert effective["DT-003"] == "ADVISORY"
    assert "ADVISORY" in markdown


@pytest.mark.unit
def test_coverage_table_is_markdown_table() -> None:
    clauses = [_make_clause("DT-001", "enforced", "verify/Run Receipt-Gate")]
    markdown, _ = generate_coverage_table(clauses, {"Run Receipt-Gate"})
    assert "| Clause |" in markdown
    assert "| --- |" in markdown


# --- _collect_job_names (integration with real workflows) ---


@pytest.mark.unit
def test_collect_job_names_returns_nonempty_set() -> None:
    job_names = _collect_job_names(_WORKFLOWS_DIR)
    assert len(job_names) > 0


@pytest.mark.unit
def test_collect_job_names_contains_known_jobs() -> None:
    job_names = _collect_job_names(_WORKFLOWS_DIR)
    # ci.yml has a "Pre-commit" job
    assert any("Pre-commit" in name for name in job_names)
