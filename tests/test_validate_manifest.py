"""Tests for the validate_manifest.py script.

These tests verify that manifest validation works correctly.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from scripts.validate_manifest import calculate_file_hash, validate_manifest


def run_manifest_validation(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the validate_manifest.py script with given arguments.

    Args:
        *args: Additional command-line arguments

    Returns:
        CompletedProcess with captured stdout and stderr

    """
    cmd = [sys.executable, "scripts/validate_manifest.py", *args]
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )


class TestManifestValidationIntegration:
    """Integration tests for the manifest validation script."""

    def test_existing_manifest_passes(self) -> None:
        """Test that the existing manifest passes validation."""
        result = run_manifest_validation()
        assert result.returncode == 0
        assert "Manifest validation passed" in result.stdout

    def test_shows_schema_version(self) -> None:
        """Test that the script displays schema version."""
        result = run_manifest_validation()
        assert result.returncode == 0
        assert "Schema version:" in result.stdout

    def test_shows_export_script_version(self) -> None:
        """Test that the script displays export script version."""
        result = run_manifest_validation()
        assert result.returncode == 0
        assert "Export script version:" in result.stdout

    def test_shows_schema_files_count(self) -> None:
        """Test that the script displays schema file count."""
        result = run_manifest_validation()
        assert result.returncode == 0
        assert "Schema files:" in result.stdout


class TestManifestValidationLogic:
    """Tests for manifest validation logic."""

    def test_validates_correct_manifest(self, tmp_path: Path) -> None:
        """Test that a correct manifest passes validation."""
        # Create a valid manifest and schema file
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        schema_content = '{"type": "object"}\n'
        schema_file = schemas_dir / "test.schema.json"
        schema_file.write_text(schema_content)

        # Calculate correct hash
        sha256 = hashlib.sha256(schema_content.encode()).hexdigest()

        manifest = {
            "schema_version": "1.0.0",
            "export_script_version": "1.0.0",
            "schemas": [{"file": "test.schema.json", "sha256": sha256}],
        }
        manifest_file = schemas_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        success, errors = validate_manifest(manifest_file, schemas_dir)
        assert success is True
        assert len(errors) == 0

    def test_detects_missing_schema_file(self, tmp_path: Path) -> None:
        """Test that missing schema files are detected."""
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        manifest = {
            "schema_version": "1.0.0",
            "export_script_version": "1.0.0",
            "schemas": [{"file": "missing.schema.json", "sha256": "abc123"}],
        }
        manifest_file = schemas_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        success, errors = validate_manifest(manifest_file, schemas_dir)
        assert success is False
        assert any("not found" in e for e in errors)

    def test_detects_hash_mismatch(self, tmp_path: Path) -> None:
        """Test that hash mismatches are detected."""
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        schema_file = schemas_dir / "test.schema.json"
        schema_file.write_text('{"type": "object"}\n')

        # Use wrong hash
        manifest = {
            "schema_version": "1.0.0",
            "export_script_version": "1.0.0",
            "schemas": [{"file": "test.schema.json", "sha256": "wronghash123"}],
        }
        manifest_file = schemas_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        success, errors = validate_manifest(manifest_file, schemas_dir)
        assert success is False
        assert any("Hash mismatch" in e for e in errors)

    def test_detects_missing_required_fields(self, tmp_path: Path) -> None:
        """Test that missing required fields are detected."""
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        # Missing schema_version
        manifest = {
            "export_script_version": "1.0.0",
            "schemas": [],
        }
        manifest_file = schemas_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        success, errors = validate_manifest(manifest_file, schemas_dir)
        assert success is False
        assert any("schema_version" in e for e in errors)

    def test_detects_empty_export_script_version(self, tmp_path: Path) -> None:
        """Test that empty export_script_version is detected (traceability)."""
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        schema_content = '{"type": "object"}\n'
        schema_file = schemas_dir / "test.schema.json"
        schema_file.write_text(schema_content)
        sha256 = hashlib.sha256(schema_content.encode()).hexdigest()

        manifest = {
            "schema_version": "1.0.0",
            "export_script_version": "",  # Empty - traceability violation
            "schemas": [{"file": "test.schema.json", "sha256": sha256}],
        }
        manifest_file = schemas_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        success, errors = validate_manifest(manifest_file, schemas_dir)
        assert success is False
        assert any("traceability" in e.lower() for e in errors)

    def test_detects_empty_schemas_list(self, tmp_path: Path) -> None:
        """Test that empty schemas list is detected."""
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        manifest = {
            "schema_version": "1.0.0",
            "export_script_version": "1.0.0",
            "schemas": [],  # Empty
        }
        manifest_file = schemas_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        success, errors = validate_manifest(manifest_file, schemas_dir)
        assert success is False
        assert any("empty" in e.lower() for e in errors)

    def test_detects_invalid_json(self, tmp_path: Path) -> None:
        """Test that invalid JSON is detected."""
        schemas_dir = tmp_path / "schemas"
        schemas_dir.mkdir()

        manifest_file = schemas_dir / "manifest.json"
        manifest_file.write_text("{ invalid json }")

        success, errors = validate_manifest(manifest_file, schemas_dir)
        assert success is False
        assert any("Invalid JSON" in e for e in errors)


class TestHashCalculation:
    """Tests for file hash calculation."""

    def test_calculate_file_hash(self, tmp_path: Path) -> None:
        """Test that file hashes are calculated correctly."""
        test_file = tmp_path / "test.txt"
        content = "Hello, World!\n"
        test_file.write_text(content)

        expected_hash = hashlib.sha256(content.encode()).hexdigest()
        actual_hash = calculate_file_hash(test_file)

        assert actual_hash == expected_hash

    def test_hash_is_deterministic(self, tmp_path: Path) -> None:
        """Test that hash calculation is deterministic."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("consistent content")

        hash1 = calculate_file_hash(test_file)
        hash2 = calculate_file_hash(test_file)

        assert hash1 == hash2
