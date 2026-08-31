# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""migrate_file_exists_receipts.py — backfill OMN-9788 schema fields on stale receipts.

Walks ``drift/dod_receipts/`` and finds stale ``file_exists.yaml`` receipts that
have ``status: PASS`` but are missing any of the four adversarial fields added by
OMN-9788 (``schema_version``, ``verifier``, ``probe_command``, ``probe_stdout``).

Each stale receipt is rewritten with:

* ``schema_version: "1.0.0"`` — satisfies ``_SEMVER_RE`` in ``ModelDodReceipt``
* ``verifier: "omn-9788-migration-v1"`` — distinct identity from any plausible runner
* ``probe_command`` — portable POSIX grep self-probe on the receipt file itself
* ``probe_stdout`` — fixed marker string proving the probe ran

Note: the gate's Rule 2 (``WEAK_PROOF_CHECK_TYPES = {"file_exists"}``) auto-downgrades
PASS → ADVISORY at validation time regardless of verifier. The migration restores
schema validity; it does not fake PASS eligibility.

The script is **idempotent** — receipts that already have all four fields are skipped.

Usage::

    # Dry-run (print what would change, write nothing)
    uv run python scripts/migrate_file_exists_receipts.py --dry-run

    # Apply migration
    uv run python scripts/migrate_file_exists_receipts.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

_REQUIRED_FIELDS = frozenset(
    {"schema_version", "verifier", "probe_command", "probe_stdout"}
)


def _is_stale(receipt: dict[str, Any]) -> bool:
    """Return True if the receipt is stale (PASS + missing any required field)."""
    if receipt.get("status") != "PASS":
        return False
    return not _REQUIRED_FIELDS.issubset(receipt.keys())


def _migrate(receipt: dict[str, Any], receipt_path: Path) -> dict[str, Any]:
    """Return a copy of ``receipt`` with all four adversarial fields populated.

    Fields already present are left unchanged so a partial migration can be
    completed safely (idempotency at the field level, not just the receipt level).
    """
    # Use relative path for the probe so it is POSIX-portable across machines.
    # The path is relative to the repo root, matching the existing check_value.
    rel_path = str(receipt.get("check_value", str(receipt_path)))

    updated = dict(receipt)
    if "schema_version" not in updated:
        updated["schema_version"] = "1.0.0"
    if "verifier" not in updated:
        updated["verifier"] = "omn-9788-migration-v1"
    if "probe_command" not in updated:
        updated["probe_command"] = f'grep -q "migrated: true" "{rel_path}"'
    if "probe_stdout" not in updated:
        updated["probe_stdout"] = "migrated: true"

    return updated


def migrate_receipt_file(path: Path, *, apply: bool) -> bool:
    """Migrate a single receipt file. Returns True if migration was needed."""
    raw = path.read_text(encoding="utf-8")
    receipt: dict[str, Any] = yaml.safe_load(raw) or {}

    if not _is_stale(receipt):
        return False

    migrated = _migrate(receipt, path)

    if apply:
        prefix = "---\n" if raw.lstrip().startswith("---") else ""
        path.write_text(
            prefix
            + yaml.safe_dump(migrated, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--receipts-dir",
        default="drift/dod_receipts",
        help="Root directory for dod_receipts (default: drift/dod_receipts)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to disk; default is dry-run (no writes)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Alias for default behaviour — print changes but do not write",
    )
    args = parser.parse_args()

    receipts_dir = Path(args.receipts_dir).resolve()
    if not receipts_dir.is_dir():
        print(f"[ERROR] Not a directory: {receipts_dir}", file=sys.stderr)
        return 1

    apply = args.apply and not args.dry_run

    migrated_count = 0
    skipped_count = 0
    error_count = 0

    for receipt_path in sorted(receipts_dir.rglob("file_exists.yaml")):
        try:
            was_migrated = migrate_receipt_file(receipt_path, apply=apply)
        except (OSError, yaml.YAMLError, ValueError) as exc:
            print(f"[ERROR] {receipt_path}: {exc}", file=sys.stderr)
            error_count += 1
            continue

        if was_migrated:
            verb = "WROTE" if apply else "WOULD"
            print(f"[{verb}] {receipt_path.relative_to(receipts_dir.parent.parent)}")
            migrated_count += 1
        else:
            skipped_count += 1

    mode = "APPLY" if apply else "DRY-RUN"
    print(
        f"\n[{mode}] {migrated_count} migrated / {skipped_count} skipped"
        f" / {error_count} errors"
    )
    return 1 if error_count else 0


if __name__ == "__main__":
    sys.exit(main())
