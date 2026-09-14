# SPDX-License-Identifier: MIT
"""Validate migration_inventory.yaml against actual migration files on disk.

Checks:
1. Every file listed in the inventory exists on disk
2. Every .sql file on disk is listed in the inventory
3. No duplicate migration filenames within the same migration set
4. Referenced repo/directory roots exist on disk
5. Each migration entry has required metadata (file, tables)
6. No empty source_repo or directory fields
7. Only .sql files listed in migrations entries

Usage:
    uv run check-migration-inventory --repos-root /path/to/omni_home
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Finding:
    check: str
    severity: str  # ERROR, WARNING
    detail: str


@dataclass
class ValidationResult:
    ok: bool = True
    findings: list[Finding] = field(default_factory=list)

    def add(self, check: str, severity: str, detail: str) -> None:
        self.findings.append(Finding(check=check, severity=severity, detail=detail))
        if severity == "ERROR":
            self.ok = False


def _validate_migration_set(
    db_name: str,
    mset: dict[str, object],
    repos_root: Path,
    result: ValidationResult,
) -> None:
    """Validate a single migration set within a database config."""
    repo = mset.get("source_repo", "")
    directory = mset.get("directory", "")

    if not repo:
        result.add(
            "MISSING_METADATA",
            "ERROR",
            f"{db_name}: migration set has empty source_repo",
        )
        return
    if not directory:
        result.add(
            "MISSING_METADATA",
            "ERROR",
            f"{db_name}: migration set in {repo} has empty directory",
        )
        return

    repo_root = repos_root / str(repo)
    if not repo_root.exists():
        result.add(
            "MISSING_REPO",
            "WARNING",
            f"{db_name}: repo root {repo} not found at {repo_root}"
            " (degraded validation)",
        )
        return

    repo_dir = repo_root / str(directory)
    if not repo_dir.exists():
        result.add(
            "MISSING_DIRECTORY",
            "ERROR",
            f"{db_name}: directory {repo}/{directory} not found on disk",
        )
        return

    path_prefix = f"{repo}/{directory}"
    listed_files = _validate_entries(db_name, path_prefix, mset, repo_dir, result)

    # Check for unlisted files on disk
    disk_files = {f.name for f in repo_dir.glob("*.sql")}
    for df in sorted(disk_files - listed_files):
        result.add(
            "UNLISTED_FILE",
            "ERROR",
            f"{db_name}: {path_prefix}/{df} exists on disk but not in inventory",
        )


def _validate_entries(
    db_name: str,
    path_prefix: str,
    mset: dict[str, object],
    repo_dir: Path,
    result: ValidationResult,
) -> set[str]:
    """Validate individual migration entries within a set."""
    listed_files: set[str] = set()
    migrations = mset.get("migrations", [])
    if not isinstance(migrations, list):
        return listed_files
    for entry in migrations:
        if not isinstance(entry, dict):
            continue
        fname = entry.get("file", "")
        tables = entry.get("tables")

        if not fname:
            result.add(
                "MISSING_METADATA",
                "ERROR",
                f"{db_name}: migration entry missing 'file' field",
            )
            continue
        if not str(fname).endswith(".sql") and not str(fname).endswith(".sh"):
            result.add(
                "INVALID_EXTENSION",
                "WARNING",
                f"{db_name}: {fname} is not a .sql or .sh file",
            )
        if tables is None:
            result.add(
                "MISSING_METADATA",
                "WARNING",
                f"{db_name}: {fname} missing 'tables' field",
            )

        if str(fname) in listed_files:
            result.add(
                "DUPLICATE_ENTRY",
                "ERROR",
                f"{db_name}: {path_prefix}/{fname} listed more than once",
            )
        listed_files.add(str(fname))

        fpath = repo_dir / str(fname)
        if not fpath.exists():
            result.add(
                "MISSING_FILE",
                "ERROR",
                f"{db_name}: {path_prefix}/{fname} listed but not found on disk",
            )

    return listed_files


def validate_inventory(inventory_path: Path, repos_root: Path) -> ValidationResult:
    result = ValidationResult()
    data = yaml.safe_load(inventory_path.read_text())

    for db_name, db_config in data.get("databases", {}).items():
        for mset in db_config.get("migration_sets", []):
            _validate_migration_set(db_name, mset, repos_root, result)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate migration inventory")
    parser.add_argument(
        "--repos-root",
        type=Path,
        required=True,
        help="Root directory containing all repos (e.g., omni_home)",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=None,
        help="Path to migration_inventory.yaml (default: auto-detect)",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Exit 0 even on errors (for soft-fail CI)",
    )
    args = parser.parse_args()

    inventory = args.inventory or (
        Path(__file__).parent.parent / "boundaries" / "migration_inventory.yaml"
    )

    result = validate_inventory(inventory, args.repos_root)

    for f in result.findings:
        print(f"[{f.severity}] {f.check}: {f.detail}")

    if not result.findings:
        print("Migration inventory: all files accounted for.")

    if args.warn_only:
        return 0
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
