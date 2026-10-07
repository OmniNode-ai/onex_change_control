# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for scripts/migrate_file_exists_receipts.py (OMN-9890)."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from migrate_file_exists_receipts import (
    _REQUIRED_FIELDS,
    _is_stale,
    _migrate,
    migrate_receipt_file,
)

_STALE_RECEIPT = {
    "ticket_id": "OMN-9647",
    "evidence_item_id": "dod-001",
    "check_type": "file_exists",
    "check_value": "drift/dod_receipts/OMN-9647/dod-001/file_exists.yaml",
    "status": "PASS",
    "run_timestamp": "2026-04-24 17:42:32+00:00",
    "commit_sha": "96385d92",
    "runner": "local",
    "actual_output": "FOUND",
    "exit_code": 0,
    "pr_number": 914,
}

_ALREADY_MIGRATED = {
    **_STALE_RECEIPT,
    "schema_version": "1.0.0",
    "verifier": "omn-9788-migration-v1",
    "probe_command": (
        'grep -q "migrated: true"'
        ' "drift/dod_receipts/OMN-9647/dod-001/file_exists.yaml"'
    ),
    "probe_stdout": "migrated: true",
}

_FAIL_RECEIPT = {
    **_STALE_RECEIPT,
    "status": "FAIL",
}

_ADVISORY_RECEIPT = {
    **_STALE_RECEIPT,
    "status": "ADVISORY",
}


class TestIsStale:
    def test_stale_pass_receipt_missing_all_fields(self) -> None:
        assert _is_stale(_STALE_RECEIPT) is True

    def test_already_migrated_receipt_not_stale(self) -> None:
        assert _is_stale(_ALREADY_MIGRATED) is False

    def test_fail_receipt_not_stale(self) -> None:
        assert _is_stale(_FAIL_RECEIPT) is False

    def test_advisory_receipt_not_stale(self) -> None:
        assert _is_stale(_ADVISORY_RECEIPT) is False

    def test_partial_migration_still_stale(self) -> None:
        partial = {**_STALE_RECEIPT, "schema_version": "1.0.0"}
        assert _is_stale(partial) is True

    def test_all_four_fields_present_not_stale(self) -> None:
        complete = {
            **_STALE_RECEIPT,
            "schema_version": "1.0.0",
            "verifier": "v1",
            "probe_command": "grep -q x f",
            "probe_stdout": "x",
        }
        assert _is_stale(complete) is False


class TestMigrate:
    def test_migrate_adds_all_four_fields(self) -> None:
        result = _migrate(
            _STALE_RECEIPT, Path("drift/dod_receipts/OMN-9647/dod-001/file_exists.yaml")
        )
        for field in _REQUIRED_FIELDS:
            assert field in result, f"missing: {field}"

    def test_migrate_schema_version_is_semver(self) -> None:
        result = _migrate(_STALE_RECEIPT, Path("f.yaml"))
        assert result["schema_version"] == "1.0.0"

    def test_migrate_verifier_distinct_from_runner(self) -> None:
        result = _migrate(_STALE_RECEIPT, Path("f.yaml"))
        assert result["verifier"] != result["runner"]

    def test_migrate_probe_stdout_is_marker(self) -> None:
        result = _migrate(_STALE_RECEIPT, Path("f.yaml"))
        assert result["probe_stdout"] == "migrated: true"

    def test_migrate_preserves_existing_fields(self) -> None:
        result = _migrate(_STALE_RECEIPT, Path("f.yaml"))
        assert result["ticket_id"] == "OMN-9647"
        assert result["status"] == "PASS"
        assert result["runner"] == "local"

    def test_migrate_idempotent_on_existing_fields(self) -> None:
        partial = {**_STALE_RECEIPT, "schema_version": "2.0.0"}
        result = _migrate(partial, Path("f.yaml"))
        assert result["schema_version"] == "2.0.0"

    def test_migrate_probe_command_references_check_value(self) -> None:
        result = _migrate(_STALE_RECEIPT, Path("f.yaml"))
        assert _STALE_RECEIPT["check_value"] in result["probe_command"]


class TestMigrateReceiptFile:
    def _write_stale(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n" + yaml.safe_dump(_STALE_RECEIPT, sort_keys=False),
            encoding="utf-8",
        )

    def test_apply_writes_migrated_receipt(self, tmp_path: Path) -> None:
        receipt_path = tmp_path / "file_exists.yaml"
        self._write_stale(receipt_path)

        was_migrated = migrate_receipt_file(receipt_path, apply=True)
        assert was_migrated is True

        written = yaml.safe_load(receipt_path.read_text())
        for field in _REQUIRED_FIELDS:
            assert field in written, f"missing after apply: {field}"

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        receipt_path = tmp_path / "file_exists.yaml"
        self._write_stale(receipt_path)
        original = receipt_path.read_text()

        was_migrated = migrate_receipt_file(receipt_path, apply=False)
        assert was_migrated is True
        assert receipt_path.read_text() == original

    def test_already_migrated_skipped(self, tmp_path: Path) -> None:
        receipt_path = tmp_path / "file_exists.yaml"
        receipt_path.write_text(
            "---\n" + yaml.safe_dump(_ALREADY_MIGRATED, sort_keys=False),
            encoding="utf-8",
        )
        snapshot = receipt_path.read_text()

        was_migrated = migrate_receipt_file(receipt_path, apply=True)
        assert was_migrated is False
        assert receipt_path.read_text() == snapshot

    def test_fail_receipt_skipped(self, tmp_path: Path) -> None:
        receipt_path = tmp_path / "file_exists.yaml"
        receipt_path.write_text(
            "---\n" + yaml.safe_dump(_FAIL_RECEIPT, sort_keys=False),
            encoding="utf-8",
        )
        was_migrated = migrate_receipt_file(receipt_path, apply=True)
        assert was_migrated is False

    def test_idempotent_double_apply(self, tmp_path: Path) -> None:
        receipt_path = tmp_path / "file_exists.yaml"
        self._write_stale(receipt_path)

        migrate_receipt_file(receipt_path, apply=True)
        after_first = receipt_path.read_text()

        was_migrated = migrate_receipt_file(receipt_path, apply=True)
        assert was_migrated is False
        assert receipt_path.read_text() == after_first

    def test_preserves_leading_dashes(self, tmp_path: Path) -> None:
        receipt_path = tmp_path / "file_exists.yaml"
        self._write_stale(receipt_path)

        migrate_receipt_file(receipt_path, apply=True)
        assert receipt_path.read_text().startswith("---")
