# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Check for migration conflicts and column references across repositories.

Detects two types of conflicts in SQL migration files:

1. NAME_CONFLICT: Multiple migrations define the same table with different columns.
2. EXACT_DUPLICATE: Multiple migrations define the same table with identical columns.

Both are problems because they indicate uncoordinated schema evolution that will
cause runtime failures when migrations are applied in different orders.

Additionally supports --check-columns to validate that Python SQL string literals
reference columns that actually exist in migration DDL (OMN-5769).

Usage:
    uv run check-migration-conflicts --repos-root /path/to/omni_home
    uv run check-migration-conflicts --repos-root /path --repos repo1 repo2
    uv run check-migration-conflicts --repos-root /path --warn-only
    uv run check-migration-conflicts --repos-root /path --check-columns

Exit codes:
    0: No conflicts found (or --warn-only flag is set)
    1: One or more conflicts found (unless --warn-only is set)
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from enum import Enum, unique
from pathlib import Path
from typing import NamedTuple

import yaml
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


def load_suppressions(suppressions_path: Path) -> set[str]:
    """Load suppressed table names from a YAML suppressions file.

    Returns a set of table names (lowercased) that should be excluded
    from conflict reporting.
    """
    if not suppressions_path.is_file():
        return set()

    with suppressions_path.open() as f:
        data = yaml.safe_load(f)

    if not data or "suppressions" not in data:
        return set()

    suppressed: set[str] = set()
    for entry in data["suppressions"]:
        table = entry.get("table", "").lower()
        if table:
            suppressed.add(table)

    return suppressed


def filter_suppressed_conflicts(
    conflicts: list[MigrationConflict],
    suppressed_tables: set[str],
) -> tuple[list[MigrationConflict], list[MigrationConflict]]:
    """Split conflicts into unsuppressed and suppressed lists."""
    unsuppressed = []
    suppressed = []
    for conflict in conflicts:
        if conflict.table_name in suppressed_tables:
            suppressed.append(conflict)
        else:
            unsuppressed.append(conflict)
    return unsuppressed, suppressed


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


## ---------------------------------------------------------------------------
## Column-reference validation (OMN-5769)
## ---------------------------------------------------------------------------

# High-confidence SQL patterns for extracting column references.
# V1 scope: single-table statements only.  See docstring for exclusions.

# INSERT INTO table (col1, col2, ...)
_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)",
    re.IGNORECASE,
)

# SELECT col1, col2 FROM table
_SELECT_RE = re.compile(
    r"SELECT\s+(.*?)\s+FROM\s+(\w+)",
    re.IGNORECASE | re.DOTALL,
)

# UPDATE table SET col = ...
_UPDATE_RE = re.compile(
    r"UPDATE\s+(\w+)\s+SET\s+(.*?)(?:\s+WHERE|\s+RETURNING|\s*;|\s*$)",
    re.IGNORECASE | re.DOTALL,
)

# ON CONFLICT (col1, col2)
_ON_CONFLICT_RE = re.compile(
    r"ON\s+CONFLICT\s*\(([^)]+)\)",
    re.IGNORECASE,
)

# RETURNING col1, col2
_RETURNING_RE = re.compile(
    r"RETURNING\s+(.*?)(?:\s*;|\s*$|\s*\))",
    re.IGNORECASE | re.DOTALL,
)

# ALTER TABLE t ADD COLUMN col type ...
_ALTER_ADD_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\w+)\s+ADD\s+(?:COLUMN\s+)?(\w+)\s+",
    re.IGNORECASE,
)

# ALTER TABLE t DROP COLUMN col
_ALTER_DROP_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\w+)\s+DROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?(\w+)",
    re.IGNORECASE,
)

# Tokens that are SQL keywords / expressions, not column names
_SQL_KEYWORDS = frozenset(
    {
        "*",
        "null",
        "true",
        "false",
        "default",
        "now",
        "current_timestamp",
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "coalesce",
        "case",
        "when",
        "then",
        "else",
        "end",
        "as",
        "distinct",
        "all",
    }
)


@dataclass
class ColumnViolation:
    """A column referenced in Python that does not exist in migration DDL."""

    table: str
    column: str
    python_file: Path
    repo: str


@dataclass
class ColumnReferenceResult:
    """Aggregated column-reference check results."""

    violations: list[ColumnViolation] = field(default_factory=list)
    ambiguous_tables: set[str] = field(default_factory=set)


def _collect_repo_schemas(
    repos_root: Path,
    migration_files: list[Path],
) -> dict[str, dict[str, set[str]]]:
    """Collect per-repo column schemas from migration files."""
    repo_schemas: dict[str, dict[str, set[str]]] = {}

    for sql_file in migration_files:
        try:
            repo_name = sql_file.relative_to(repos_root).parts[0]
        except ValueError:
            repo_name = sql_file.parent.name

        if repo_name not in repo_schemas:
            repo_schemas[repo_name] = {}

        for table_def in extract_tables_from_sql(sql_file, repo_name):
            tname = table_def.table_name
            if tname not in repo_schemas[repo_name]:
                repo_schemas[repo_name][tname] = set(table_def.columns)
            else:
                repo_schemas[repo_name][tname] |= set(table_def.columns)

        _apply_alter_statements(sql_file, repo_name, repo_schemas)

    return repo_schemas


def _apply_alter_statements(
    sql_file: Path,
    repo_name: str,
    repo_schemas: dict[str, dict[str, set[str]]],
) -> None:
    """Apply ALTER TABLE ADD/DROP COLUMN to repo schemas."""
    try:
        content = sql_file.read_text()
    except OSError:
        return

    for m in _ALTER_ADD_RE.finditer(content):
        tname = m.group(1).lower()
        col = m.group(2).lower()
        if tname not in repo_schemas[repo_name]:
            repo_schemas[repo_name][tname] = set()
        repo_schemas[repo_name][tname].add(col)

    for m in _ALTER_DROP_RE.finditer(content):
        tname = m.group(1).lower()
        col = m.group(2).lower()
        if tname in repo_schemas[repo_name]:
            repo_schemas[repo_name][tname].discard(col)


def _merge_schemas(
    repo_schemas: dict[str, dict[str, set[str]]],
) -> tuple[dict[str, set[str]], set[str]]:
    """Merge per-repo schemas, detecting ambiguous tables."""
    table_columns: dict[str, set[str]] = {}
    ambiguous_tables: set[str] = set()

    table_to_repos: dict[str, dict[str, set[str]]] = {}
    for repo_name, schemas in repo_schemas.items():
        for tname, cols in schemas.items():
            if tname not in table_to_repos:
                table_to_repos[tname] = {}
            table_to_repos[tname][repo_name] = cols

    for tname, repo_cols in table_to_repos.items():
        if len(repo_cols) > 1:
            col_sets = list(repo_cols.values())
            if not all(c == col_sets[0] for c in col_sets):
                ambiguous_tables.add(tname)
                continue
        merged: set[str] = set()
        for cols in repo_cols.values():
            merged |= cols
        table_columns[tname] = merged

    return table_columns, ambiguous_tables


def _build_canonical_schemas(
    repos_root: Path,
    repos: list[str] | None = None,
) -> tuple[dict[str, set[str]], set[str]]:
    """Build canonical column sets per table from migration DDL.

    Returns:
        (table_columns, ambiguous_tables) where:
        - table_columns maps table_name -> set of column names
        - ambiguous_tables is the set of tables defined with different schemas
          across repos (NAME_CONFLICT — no column validation attempted)
    """
    migration_files = find_migration_files(repos_root, repos)
    repo_schemas = _collect_repo_schemas(repos_root, migration_files)
    return _merge_schemas(repo_schemas)


def _extract_column_names(raw: str) -> list[str]:
    """Extract column names from a comma-separated SQL fragment.

    Filters out expressions, function calls, *, and keywords.
    """
    columns: list[str] = []
    for raw_token in raw.split(","):
        token = raw_token.strip()
        # Skip empty, expressions with parens, casts, qualified refs (foo.col)
        if not token or "(" in token or "::" in token or "." in token:
            continue
        # Take first word only (handles "col AS alias")
        word = token.split()[0].strip().lower()
        # Skip keywords and non-identifiers
        if word in _SQL_KEYWORDS or not re.match(r"^[a-z_]\w*$", word):
            continue
        columns.append(word)
    return columns


def _extract_update_targets(raw: str) -> list[str]:
    """Extract SET target column names from UPDATE ... SET fragment."""
    columns: list[str] = []
    for assignment in raw.split(","):
        parts = assignment.split("=", 1)
        if len(parts) >= 2:  # noqa: PLR2004
            col = parts[0].strip().lower()
            if re.match(r"^[a-z_]\w*$", col):
                columns.append(col)
    return columns


def _add_column_ref(
    refs: dict[str, set[str]],
    known_tables: set[str],
    table: str,
    cols: list[str],
) -> None:
    """Add column references for a table if it's in the known set."""
    table = table.lower()
    if table in known_tables:
        if table not in refs:
            refs[table] = set()
        refs[table].update(cols)


def _find_preceding_table(
    content: str,
    pos: int,
) -> str | None:
    """Find the table name from the nearest preceding INSERT or UPDATE."""
    prefix = content[:pos]
    insert_match = list(_INSERT_RE.finditer(prefix))
    update_match = list(_UPDATE_RE.finditer(prefix))
    if insert_match:
        return insert_match[-1].group(1)
    if update_match:
        return update_match[-1].group(1)
    return None


def _scan_python_for_column_refs(
    py_file: Path,
    known_tables: set[str],
) -> dict[str, set[str]]:
    """Scan a Python file for SQL string literals referencing known tables.

    Returns dict[table_name, set[referenced_column_names]].
    """
    try:
        content = py_file.read_text()
    except OSError:
        return {}

    refs: dict[str, set[str]] = {}

    for m in _INSERT_RE.finditer(content):
        _add_column_ref(
            refs, known_tables, m.group(1), _extract_column_names(m.group(2))
        )

    for m in _SELECT_RE.finditer(content):
        _add_column_ref(
            refs, known_tables, m.group(2), _extract_column_names(m.group(1))
        )

    for m in _UPDATE_RE.finditer(content):
        _add_column_ref(
            refs, known_tables, m.group(1), _extract_update_targets(m.group(2))
        )

    for m in _ON_CONFLICT_RE.finditer(content):
        insert_match = list(_INSERT_RE.finditer(content[: m.start()]))
        if insert_match:
            _add_column_ref(
                refs,
                known_tables,
                insert_match[-1].group(1),
                _extract_column_names(m.group(1)),
            )

    for m in _RETURNING_RE.finditer(content):
        table = _find_preceding_table(content, m.start())
        if table:
            _add_column_ref(
                refs, known_tables, table, _extract_column_names(m.group(1))
            )

    return refs


def check_column_references(
    repos_root: Path,
    repos: list[str] | None = None,
) -> ColumnReferenceResult:
    """Check that Python SQL references match migration DDL columns.

    Builds canonical schemas from migration files, then scans Python files
    under src/ for SQL string literals.  Reports violations where referenced
    columns do not exist in the canonical DDL.

    Tables with ambiguous schemas (NAME_CONFLICT across repos) are reported
    as ambiguous — no column validation is attempted.
    """
    table_columns, ambiguous_tables = _build_canonical_schemas(repos_root, repos)
    result = ColumnReferenceResult(ambiguous_tables=ambiguous_tables)

    known_tables = set(table_columns.keys())

    # Scan Python files in each repo
    if repos:
        dirs = [repos_root / r for r in repos]
    else:
        dirs = [
            d for d in repos_root.iterdir() if d.is_dir() and not d.name.startswith(".")
        ]

    for repo_dir in dirs:
        src_dir = repo_dir / "src"
        if not src_dir.is_dir():
            continue

        try:
            repo_name = repo_dir.relative_to(repos_root).parts[0]
        except ValueError:
            repo_name = repo_dir.name

        for py_file in src_dir.rglob("*.py"):
            refs = _scan_python_for_column_refs(py_file, known_tables)
            for table, columns in refs.items():
                if table in ambiguous_tables:
                    continue
                canonical = table_columns.get(table, set())
                for col in columns:
                    if col not in canonical:
                        result.violations.append(
                            ColumnViolation(
                                table=table,
                                column=col,
                                python_file=py_file,
                                repo=repo_name,
                            )
                        )

    return result


def format_column_violations(result: ColumnReferenceResult) -> str:
    """Format column-reference violations for human-readable output."""
    lines: list[str] = []

    if result.ambiguous_tables:
        lines.append(
            f"{Fore.YELLOW}SCHEMA_AMBIGUOUS: {len(result.ambiguous_tables)} table(s) "
            f"defined with conflicting schemas across repos — "
            f"column validation skipped:{Style.RESET_ALL}"
        )
        for t in sorted(result.ambiguous_tables):
            lines.append(f"  - {t}")
        lines.append("")

    if not result.violations:
        if not result.ambiguous_tables:
            lines.append(
                f"{Fore.GREEN}No column-reference violations found.{Style.RESET_ALL}"
            )
        return "\n".join(lines)

    lines.append(
        f"{Fore.RED}Found {len(result.violations)} column-reference "
        f"violation(s):{Style.RESET_ALL}"
    )
    lines.append("")

    for v in result.violations:
        lines.append(
            f"  {Fore.RED}MISSING_COLUMN{Style.RESET_ALL}: "
            f"`{v.column}` in table `{v.table}`"
        )
        lines.append(f"    file: {v.repo}/{v.python_file.name}")
    lines.append("")

    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
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
    parser.add_argument(
        "--check-columns",
        action="store_true",
        help="Also check that Python SQL references match migration DDL columns.",
    )
    parser.add_argument(
        "--suppressions-file",
        type=Path,
        default=None,
        help="YAML file listing known intentional conflicts to suppress.",
    )
    parser.add_argument(
        "--warn-columns",
        action="store_true",
        help="Treat column-reference violations as warnings (exit 0).",
    )
    return parser


def main() -> None:
    """CLI entry point."""
    init()  # Initialize colorama
    args = _build_parser().parse_args()

    if not args.repos_root.is_dir():
        print(
            f"{Fore.RED}Error: repos-root '{args.repos_root}' "
            f"is not a directory.{Style.RESET_ALL}",
            file=sys.stderr,
        )
        sys.exit(1)

    suppressed_tables = (
        load_suppressions(args.suppressions_file) if args.suppressions_file else set()
    )

    all_conflicts = detect_conflicts(args.repos_root, args.repos)
    conflicts, suppressed = filter_suppressed_conflicts(
        all_conflicts, suppressed_tables
    )

    print(format_conflicts(conflicts))

    if suppressed:
        print(
            f"{Fore.CYAN}Suppressed {len(suppressed)} known conflict(s) "
            f"via suppressions file.{Style.RESET_ALL}\n"
        )

    has_column_issues = False
    if args.check_columns:
        col_result = check_column_references(args.repos_root, args.repos)
        print(format_column_violations(col_result))
        has_column_issues = bool(col_result.violations)

    if args.warn_only:
        return

    if conflicts or (has_column_issues and not args.warn_columns):
        sys.exit(1)


if __name__ == "__main__":
    main()
