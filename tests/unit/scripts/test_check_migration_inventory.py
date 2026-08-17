# SPDX-License-Identifier: MIT
"""Tests for check_migration_inventory."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
def test_detect_missing_migration_file(tmp_path: Path) -> None:
    """Inventory references a file that doesn't exist on disk."""
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text("""
version: "1"
databases:
  test_db:
    connection_env: TEST_DB_URL
    migration_sets:
      - source_repo: test_repo
        directory: migrations
        migrations:
          - file: "001_missing.sql"
            tables: [foo]
""")
    repo_dir = tmp_path / "test_repo" / "migrations"
    repo_dir.mkdir(parents=True)
    # 001_missing.sql does NOT exist

    from onex_change_control.scripts.check_migration_inventory import (
        validate_inventory,
    )

    result = validate_inventory(inventory, tmp_path)
    assert not result.ok
    assert any("001_missing.sql" in f.detail for f in result.findings)


@pytest.mark.unit
def test_detect_unlisted_migration_file(tmp_path: Path) -> None:
    """Migration file exists on disk but is not in the inventory."""
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text("""
version: "1"
databases:
  test_db:
    connection_env: TEST_DB_URL
    migration_sets:
      - source_repo: test_repo
        directory: migrations
        migrations:
          - file: "001_create_foo.sql"
            tables: [foo]
""")
    repo_dir = tmp_path / "test_repo" / "migrations"
    repo_dir.mkdir(parents=True)
    (repo_dir / "001_create_foo.sql").write_text("CREATE TABLE foo (id int);")
    (repo_dir / "002_unlisted.sql").write_text("CREATE TABLE bar (id int);")

    from onex_change_control.scripts.check_migration_inventory import (
        validate_inventory,
    )

    result = validate_inventory(inventory, tmp_path)
    assert not result.ok
    assert any("002_unlisted.sql" in f.detail for f in result.findings)


@pytest.mark.unit
def test_valid_inventory_passes(tmp_path: Path) -> None:
    """Complete and correct inventory should pass validation."""
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text("""
version: "1"
databases:
  test_db:
    connection_env: TEST_DB_URL
    migration_sets:
      - source_repo: test_repo
        directory: migrations
        migrations:
          - file: "001_create_foo.sql"
            tables: [foo]
""")
    repo_dir = tmp_path / "test_repo" / "migrations"
    repo_dir.mkdir(parents=True)
    (repo_dir / "001_create_foo.sql").write_text("CREATE TABLE foo (id int);")

    from onex_change_control.scripts.check_migration_inventory import (
        validate_inventory,
    )

    result = validate_inventory(inventory, tmp_path)
    assert result.ok
    assert not any(f.severity == "ERROR" for f in result.findings)


@pytest.mark.unit
def test_missing_repo_warns(tmp_path: Path) -> None:
    """Missing repo root should warn, not error (degraded validation)."""
    inventory = tmp_path / "inventory.yaml"
    inventory.write_text("""
version: "1"
databases:
  test_db:
    connection_env: TEST_DB_URL
    migration_sets:
      - source_repo: nonexistent_repo
        directory: migrations
        migrations:
          - file: "001_foo.sql"
            tables: [foo]
""")
    # nonexistent_repo does NOT exist under tmp_path

    from onex_change_control.scripts.check_migration_inventory import (
        validate_inventory,
    )

    result = validate_inventory(inventory, tmp_path)
    # Should still be ok (warnings don't fail)
    assert result.ok
    assert any(
        f.check == "MISSING_REPO" and f.severity == "WARNING" for f in result.findings
    )
