# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for the doctrine clause loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from onex_change_control.doctrine.loader import DoctrineClause, load_doctrine_clauses

_CLAUSES_YAML = (
    Path(__file__).parent.parent.parent.parent
    / "docs"
    / "standards"
    / "doctrine_clauses.yaml"
)


@pytest.mark.unit
def test_load_doctrine_clauses_returns_list() -> None:
    clauses = load_doctrine_clauses(_CLAUSES_YAML)
    assert isinstance(clauses, list)
    assert len(clauses) > 0


@pytest.mark.unit
def test_all_clauses_are_doctrine_clause_instances() -> None:
    clauses = load_doctrine_clauses(_CLAUSES_YAML)
    for clause in clauses:
        assert isinstance(clause, DoctrineClause)


@pytest.mark.unit
def test_no_duplicate_clause_ids() -> None:
    clauses = load_doctrine_clauses(_CLAUSES_YAML)
    ids = [c.clause_id for c in clauses]
    assert len(ids) == len(set(ids)), "Duplicate clause_ids found"


@pytest.mark.unit
def test_clause_ids_have_dt_prefix() -> None:
    clauses = load_doctrine_clauses(_CLAUSES_YAML)
    for clause in clauses:
        assert clause.clause_id.startswith("DT-"), f"Bad clause_id: {clause.clause_id}"


@pytest.mark.unit
def test_coverage_values_are_valid() -> None:
    valid = {"enforced", "advisory", "uncovered"}
    clauses = load_doctrine_clauses(_CLAUSES_YAML)
    for clause in clauses:
        assert clause.coverage in valid, (
            f"{clause.clause_id} has invalid coverage: {clause.coverage!r}"
        )


@pytest.mark.unit
def test_required_fields_populated() -> None:
    clauses = load_doctrine_clauses(_CLAUSES_YAML)
    for clause in clauses:
        assert clause.clause_id
        assert clause.title
        assert clause.description
        assert clause.doctrine_section
        assert clause.check


@pytest.mark.unit
def test_enforced_clauses_have_ci_gate() -> None:
    clauses = load_doctrine_clauses(_CLAUSES_YAML)
    for clause in clauses:
        if clause.coverage == "enforced":
            assert clause.ci_gate is not None, (
                f"{clause.clause_id} is 'enforced' but has no ci_gate"
            )


@pytest.mark.unit
def test_all_fifteen_doctrine_sections_covered() -> None:
    clauses = load_doctrine_clauses(_CLAUSES_YAML)
    sections = {c.doctrine_section for c in clauses}
    expected = {str(i) for i in range(1, 16)}
    assert sections == expected, f"Missing sections: {expected - sections}"


@pytest.mark.unit
def test_load_doctrine_clauses_default_path() -> None:
    # Default path resolution: loader resolves relative to its own location
    clauses = load_doctrine_clauses()
    assert len(clauses) == 15


@pytest.mark.unit
def test_duplicate_clause_id_raises() -> None:
    data = {
        "schema_version": {"major": 1, "minor": 0, "patch": 0},
        "clauses": [
            {
                "clause_id": "DT-001",
                "title": "First",
                "description": "desc",
                "doctrine_section": "1",
                "check": "some_check",
                "ci_gate": None,
                "coverage": "uncovered",
            },
            {
                "clause_id": "DT-001",
                "title": "Duplicate",
                "description": "desc",
                "doctrine_section": "2",
                "check": "other_check",
                "ci_gate": None,
                "coverage": "uncovered",
            },
        ],
    }
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(data, f)
        tmp_path = Path(f.name)

    with pytest.raises(Exception, match="Duplicate clause_id"):
        load_doctrine_clauses(tmp_path)

    tmp_path.unlink()


@pytest.mark.unit
def test_invalid_clause_id_prefix_raises() -> None:
    data = {
        "schema_version": {"major": 1, "minor": 0, "patch": 0},
        "clauses": [
            {
                "clause_id": "WRONG-001",
                "title": "Bad prefix",
                "description": "desc",
                "doctrine_section": "1",
                "check": "some_check",
                "ci_gate": None,
                "coverage": "uncovered",
            },
        ],
    }
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(data, f)
        tmp_path = Path(f.name)

    with pytest.raises(ValueError, match="DT-"):
        load_doctrine_clauses(tmp_path)

    tmp_path.unlink()


@pytest.mark.unit
def test_invalid_coverage_raises() -> None:
    data = {
        "schema_version": {"major": 1, "minor": 0, "patch": 0},
        "clauses": [
            {
                "clause_id": "DT-001",
                "title": "Bad coverage",
                "description": "desc",
                "doctrine_section": "1",
                "check": "some_check",
                "ci_gate": None,
                "coverage": "partial",
            },
        ],
    }
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(data, f)
        tmp_path = Path(f.name)

    with pytest.raises(ValueError, match="partial"):
        load_doctrine_clauses(tmp_path)

    tmp_path.unlink()
