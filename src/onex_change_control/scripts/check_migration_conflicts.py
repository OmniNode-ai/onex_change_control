# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Check for migration conflicts across repositories.

Detects two types of conflicts in SQL migration files:

1. NAME_CONFLICT: Multiple migrations define the same table with different columns.
2. EXACT_DUPLICATE: Multiple migrations define the same table with identical columns.

Both are problems because they indicate uncoordinated schema evolution that will
cause runtime failures when migrations are applied in different orders.

Usage:
    uv run check-migration-conflicts --repos-root /path/to/omni_home
    uv run check-migration-conflicts --repos-root /path --repos repo1 repo2
    uv run check-migration-conflicts --repos-root /path --warn-only

Exit codes:
    0: No conflicts found (or --warn-only flag is set)
    1: One or more conflicts found (unless --warn-only is set)
"""

from __future__ import annotations

import argparse
import re
import sys
from enum import Enum, unique
from pathlib import Path
from typing import NamedTuple

from colorama import Fore, Style, init


@unique
class EnumMigrationConflictType(str, Enum):
    """Migration conflict type discriminators.

    Mirrors omnibase_core.enums.EnumMigrationConflictType without
    requiring a cross-package dependency.
    """

    NAME_CONFLICT = "name_conflict"
    EXACT_DUPLICATE = "exact_duplicate"


# Regex to extract CREATE TABLE statements and their columns
CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\);",
    re.IGNORECASE | re.DOTALL,
)

# Regex to extract column definitions (name + type, ignoring constraints)
COLUMN_RE = re.compile(
    r"^\s*(\w+)\s+([\w\[\]()]+)",
    re.MULTILINE,
)

# Keywords that are NOT column names (constraint keywords)
CONSTRAINT_KEYWORDS = frozenset(
    {
        "PRIMARY",
        "UNIQUE",
        "CHECK",
        "FOREIGN",
        "CONSTRAINT",
        "INDEX",
        "CREATE",
        "REFERENCES",
    }
)


class TableDefinition(NamedTuple):
    """A table definition extracted from a migration file."""

    table_name: str
    columns: frozenset[str]  # Column names only (not types)
    file_path: Path
    repo_name: str


class MigrationConflict(NamedTuple):
    """A detected migration conflict."""

    conflict_type: EnumMigrationConflictType
    table_name: str
    definitions: list[TableDefinition]


def extract_tables_from_sql(sql_path: Path, repo_name: str) -> list[TableDefinition]:
    """Extract CREATE TABLE definitions from a SQL migration file."""
    try:
        content = sql_path.read_text()
    except OSError:
        return []

    tables = []
    for match in CREATE_TABLE_RE.finditer(content):
        table_name = match.group(1).lower()
        body = match.group(2)

        columns: set[str] = set()
        for col_match in COLUMN_RE.finditer(body):
            col_name = col_match.group(1).upper()
            if col_name not in CONSTRAINT_KEYWORDS:
                columns.add(col_match.group(1).lower())

        if columns:
            tables.append(
                TableDefinition(
                    table_name=table_name,
                    columns=frozenset(columns),
                    file_path=sql_path,
                    repo_name=repo_name,
                )
            )

    return tables


def find_migration_files(
    repos_root: Path, repos: list[str] | None = None
) -> list[Path]:
    """Find all SQL migration files under the given repos root."""
    if repos:
        dirs = [repos_root / r for r in repos]
    else:
        dirs = [
            d for d in repos_root.iterdir() if d.is_dir() and not d.name.startswith(".")
        ]

    migration_files = []
    for repo_dir in dirs:
        if not repo_dir.is_dir():
            continue
        for sql_file in repo_dir.rglob("**/migrations/**/*.sql"):
            # Skip rollback migrations
            if "rollback" in str(sql_file).lower():
                continue
            migration_files.append(sql_file)

    return sorted(migration_files)


def detect_conflicts(
    repos_root: Path, repos: list[str] | None = None
) -> list[MigrationConflict]:
    """Detect migration conflicts across repositories."""
    # Collect all table definitions
    table_defs: dict[str, list[TableDefinition]] = {}

    migration_files = find_migration_files(repos_root, repos)

    for sql_file in migration_files:
        # Determine repo name from path
        try:
            repo_name = sql_file.relative_to(repos_root).parts[0]
        except ValueError:
            repo_name = sql_file.parent.name

        for table_def in extract_tables_from_sql(sql_file, repo_name):
            if table_def.table_name not in table_defs:
                table_defs[table_def.table_name] = []
            table_defs[table_def.table_name].append(table_def)

    # Find conflicts
    conflicts = []
    for table_name, defs in table_defs.items():
        if len(defs) <= 1:
            continue

        # Check if all definitions have the same columns
        column_sets = [d.columns for d in defs]
        if all(c == column_sets[0] for c in column_sets):
            conflicts.append(
                MigrationConflict(
                    conflict_type=EnumMigrationConflictType.EXACT_DUPLICATE,
                    table_name=table_name,
                    definitions=defs,
                )
            )
        else:
            conflicts.append(
                MigrationConflict(
                    conflict_type=EnumMigrationConflictType.NAME_CONFLICT,
                    table_name=table_name,
                    definitions=defs,
                )
            )

    return conflicts


def format_conflicts(conflicts: list[MigrationConflict]) -> str:
    """Format conflicts for human-readable output."""
    if not conflicts:
        return f"{Fore.GREEN}No migration conflicts found.{Style.RESET_ALL}"

    lines = [
        f"{Fore.RED}Found {len(conflicts)} migration conflict(s):{Style.RESET_ALL}",
        "",
    ]

    for conflict in conflicts:
        if conflict.conflict_type == EnumMigrationConflictType.NAME_CONFLICT:
            label = f"{Fore.RED}NAME_CONFLICT{Style.RESET_ALL}"
        else:
            label = f"{Fore.YELLOW}EXACT_DUPLICATE{Style.RESET_ALL}"

        lines.append(f"  {label}: table `{conflict.table_name}`")

        for defn in conflict.definitions:
            lines.append(
                f"    - {defn.repo_name}: {defn.file_path.name} "
                f"({len(defn.columns)} columns)"
            )

        if conflict.conflict_type == EnumMigrationConflictType.NAME_CONFLICT:
            # Show column diff
            all_columns: set[str] = set()
            for defn in conflict.definitions:
                all_columns |= defn.columns
            for defn in conflict.definitions:
                missing = all_columns - defn.columns
                if missing:
                    lines.append(
                        f"      missing in {defn.file_path.name}: "
                        f"{', '.join(sorted(missing))}"
                    )

        lines.append("")

    return "\n".join(lines)


def main() -> None:
    """CLI entry point."""
    init()  # Initialize colorama

    parser = argparse.ArgumentParser(
        description="Check for migration conflicts across repositories."
    )
    parser.add_argument(
        "--repos-root",
        type=Path,
        required=True,
        help="Root directory containing repository directories.",
    )
    parser.add_argument(
        "--repos",
        nargs="*",
        default=None,
        help="Specific repos to check (default: all under repos-root).",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Exit 0 even if conflicts found (CI warning mode).",
    )

    args = parser.parse_args()

    if not args.repos_root.is_dir():
        print(
            f"{Fore.RED}Error: repos-root '{args.repos_root}' "
            f"is not a directory.{Style.RESET_ALL}",
            file=sys.stderr,
        )
        sys.exit(1)

    conflicts = detect_conflicts(args.repos_root, args.repos)
    print(format_conflicts(conflicts))

    if conflicts and not args.warn_only:
        sys.exit(1)


if __name__ == "__main__":
    main()
