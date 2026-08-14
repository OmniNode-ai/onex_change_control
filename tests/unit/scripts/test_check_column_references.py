# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for column-reference validation in check_migration_conflicts."""

from pathlib import Path

import pytest

from onex_change_control.scripts.check_migration_conflicts import (
    check_column_references,
)

FIXTURES_ROOT = Path(__file__).parent.parent.parent / "fixtures" / "column_references"


@pytest.mark.unit
class TestCheckColumnReferences:
    def test_detects_missing_column_reference(self) -> None:
        """Python references column 'nonexistent' not in migration DDL."""
        result = check_column_references(
            repos_root=FIXTURES_ROOT, repos=["missing_col_repo"]
        )
        assert len(result.violations) >= 1
        violation_columns = {v.column for v in result.violations}
        assert "nonexistent" in violation_columns

    def test_accepts_valid_column_reference(self) -> None:
        """All referenced columns exist in DDL — no violations."""
        result = check_column_references(repos_root=FIXTURES_ROOT, repos=["valid_repo"])
        assert len(result.violations) == 0

    def test_reports_ambiguous_schema_instead_of_validating(self) -> None:
        """Two repos define same table with different columns — report ambiguity."""
        result = check_column_references(
            repos_root=FIXTURES_ROOT,
            repos=["ambiguous_repo_a", "ambiguous_repo_b"],
        )
        assert "users" in result.ambiguous_tables
        # No column validation attempted on ambiguous tables
        users_violations = [v for v in result.violations if v.table == "users"]
        assert len(users_violations) == 0
