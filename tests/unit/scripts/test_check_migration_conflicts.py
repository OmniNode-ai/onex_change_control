# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_migration_conflicts script."""

from pathlib import Path

import pytest

from onex_change_control.scripts.check_migration_conflicts import (
    EnumMigrationConflictType,
    detect_conflicts,
    extract_tables_from_sql,
)

FIXTURES_ROOT = Path(__file__).parent.parent.parent / "fixtures" / "migration_conflicts"


@pytest.mark.unit
class TestCheckMigrationConflicts:
    def test_name_conflict_different_columns(self) -> None:
        """Two repos defining the same table with different columns = NAME_CONFLICT."""
        conflicts = detect_conflicts(FIXTURES_ROOT, ["repo_a", "repo_b"])

        name_conflicts = [
            c
            for c in conflicts
            if c.conflict_type == EnumMigrationConflictType.NAME_CONFLICT
        ]
        assert len(name_conflicts) == 1
        assert name_conflicts[0].table_name == "users"
        assert len(name_conflicts[0].definitions) == 2

    def test_exact_duplicate_identical_columns(self) -> None:
        """Same table, identical columns = EXACT_DUPLICATE."""
        conflicts = detect_conflicts(FIXTURES_ROOT, ["repo_a", "repo_b"])

        exact_dupes = [
            c
            for c in conflicts
            if c.conflict_type == EnumMigrationConflictType.EXACT_DUPLICATE
        ]
        assert len(exact_dupes) == 1
        assert exact_dupes[0].table_name == "sessions"
        assert len(exact_dupes[0].definitions) == 2

    def test_clean_repo_no_conflicts(self) -> None:
        """Single repo with unique tables produces no conflicts."""
        conflicts = detect_conflicts(FIXTURES_ROOT, ["repo_clean"])
        assert len(conflicts) == 0

    def test_multi_table_no_false_positive(self) -> None:
        """Multiple tables in same file should not trigger false positives."""
        clean_sql = (
            FIXTURES_ROOT
            / "repo_clean"
            / "deployment"
            / "database"
            / "migrations"
            / "001_create_orders.sql"
        )
        tables = extract_tables_from_sql(clean_sql, "repo_clean")
        assert len(tables) == 2
        table_names = {t.table_name for t in tables}
        assert table_names == {"orders", "order_items"}

        # Running conflict detection on clean repo alone should find nothing
        conflicts = detect_conflicts(FIXTURES_ROOT, ["repo_clean"])
        assert len(conflicts) == 0
