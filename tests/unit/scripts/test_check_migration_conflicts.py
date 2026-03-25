# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_migration_conflicts script."""

from pathlib import Path

import pytest

from onex_change_control.scripts.check_migration_conflicts import (
    EnumMigrationConflictType,
    detect_conflicts,
    extract_tables_from_sql,
    filter_suppressed_conflicts,
    load_suppressions,
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


@pytest.mark.unit
class TestSuppressions:
    def test_load_suppressions_from_yaml(self, tmp_path: Path) -> None:
        """load_suppressions reads table names from a YAML file."""
        yaml_content = (
            "suppressions:\n"
            "  - table: users\n"
            "    conflict_type: name_conflict\n"
            "    reason: test\n"
            "  - table: Sessions\n"
            "    conflict_type: exact_duplicate\n"
            "    reason: test\n"
        )
        sup_file = tmp_path / "suppressions.yaml"
        sup_file.write_text(yaml_content)

        result = load_suppressions(sup_file)
        assert result == {"users", "sessions"}

    def test_load_suppressions_missing_file(self, tmp_path: Path) -> None:
        """load_suppressions returns empty set for missing file."""
        result = load_suppressions(tmp_path / "nonexistent.yaml")
        assert result == set()

    def test_load_suppressions_empty_file(self, tmp_path: Path) -> None:
        """load_suppressions returns empty set for empty YAML."""
        sup_file = tmp_path / "empty.yaml"
        sup_file.write_text("")

        result = load_suppressions(sup_file)
        assert result == set()

    def test_filter_suppressed_conflicts(self) -> None:
        """filter_suppressed_conflicts splits conflicts by suppression set."""
        conflicts = detect_conflicts(FIXTURES_ROOT, ["repo_a", "repo_b"])
        assert len(conflicts) == 2  # users (NAME_CONFLICT) + sessions (EXACT_DUPLICATE)

        unsuppressed, suppressed = filter_suppressed_conflicts(conflicts, {"users"})
        assert len(unsuppressed) == 1
        assert unsuppressed[0].table_name == "sessions"
        assert len(suppressed) == 1
        assert suppressed[0].table_name == "users"

    def test_filter_all_suppressed(self) -> None:
        """When all conflicts are suppressed, unsuppressed list is empty."""
        conflicts = detect_conflicts(FIXTURES_ROOT, ["repo_a", "repo_b"])

        unsuppressed, suppressed = filter_suppressed_conflicts(
            conflicts, {"users", "sessions"}
        )
        assert len(unsuppressed) == 0
        assert len(suppressed) == 2

    def test_filter_none_suppressed(self) -> None:
        """When no tables are suppressed, all conflicts are unsuppressed."""
        conflicts = detect_conflicts(FIXTURES_ROOT, ["repo_a", "repo_b"])

        unsuppressed, suppressed = filter_suppressed_conflicts(conflicts, set())
        assert len(unsuppressed) == 2
        assert len(suppressed) == 0
