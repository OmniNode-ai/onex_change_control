"""Tests for JSON schema export script."""

import json
import subprocess
import sys
from pathlib import Path

# SHA256 hash length in hex characters
_SHA256_HEX_LENGTH = 64


def test_export_script_runs_successfully() -> None:
    """Test that the export script runs without errors."""
    script_path = Path(__file__).parent.parent / "scripts" / "export_json_schema.py"
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"


def test_schema_files_exist() -> None:
    """Test that schema files are created."""
    project_root = Path(__file__).parent.parent
    schemas_dir = project_root / "schemas" / "1.0.0"

    day_close_schema = schemas_dir / "day_close.schema.json"
    ticket_contract_schema = schemas_dir / "ticket_contract.schema.json"
    manifest = schemas_dir / "manifest.json"

    assert day_close_schema.exists(), "day_close.schema.json should exist"
    assert ticket_contract_schema.exists(), "ticket_contract.schema.json should exist"
    assert manifest.exists(), "manifest.json should exist"


def test_schema_files_are_valid_json() -> None:
    """Test that schema files contain valid JSON."""
    project_root = Path(__file__).parent.parent
    schemas_dir = project_root / "schemas" / "1.0.0"

    day_close_schema = schemas_dir / "day_close.schema.json"
    ticket_contract_schema = schemas_dir / "ticket_contract.schema.json"
    manifest = schemas_dir / "manifest.json"

    # Parse JSON files
    with day_close_schema.open() as f:
        day_close_data = json.load(f)
    with ticket_contract_schema.open() as f:
        ticket_contract_data = json.load(f)
    with manifest.open() as f:
        manifest_data = json.load(f)

    # Verify structure
    assert isinstance(day_close_data, dict), "day_close schema should be a dict"
    assert isinstance(ticket_contract_data, dict), (
        "ticket_contract schema should be a dict"
    )
    assert isinstance(manifest_data, dict), "manifest should be a dict"

    # Verify manifest structure
    assert "schema_version" in manifest_data
    assert "export_script_version" in manifest_data
    assert "schemas" in manifest_data
    assert isinstance(manifest_data["schemas"], list)


def test_manifest_contains_correct_files() -> None:
    """Test that manifest lists all schema files."""
    project_root = Path(__file__).parent.parent
    schemas_dir = project_root / "schemas" / "1.0.0"

    manifest = schemas_dir / "manifest.json"
    with manifest.open() as f:
        manifest_data = json.load(f)

    schema_files = {item["file"] for item in manifest_data["schemas"]}
    assert "day_close.schema.json" in schema_files
    assert "ticket_contract.schema.json" in schema_files


def test_manifest_contains_hashes() -> None:
    """Test that manifest contains SHA256 hashes for schema files."""
    project_root = Path(__file__).parent.parent
    schemas_dir = project_root / "schemas" / "1.0.0"

    manifest = schemas_dir / "manifest.json"
    with manifest.open() as f:
        manifest_data = json.load(f)

    for schema_item in manifest_data["schemas"]:
        assert "sha256" in schema_item
        assert len(schema_item["sha256"]) == _SHA256_HEX_LENGTH, (
            f"SHA256 hash should be {_SHA256_HEX_LENGTH} characters"
        )


def test_schema_contains_export_metadata() -> None:
    """Test that exported schemas contain export metadata in $comment."""
    project_root = Path(__file__).parent.parent
    schemas_dir = project_root / "schemas" / "1.0.0"

    day_close_schema = schemas_dir / "day_close.schema.json"
    with day_close_schema.open() as f:
        day_close_data = json.load(f)

    assert "$comment" in day_close_data
    assert "export_json_schema.py" in day_close_data["$comment"]


def test_schema_determinism() -> None:
    """Test that schema export produces deterministic output."""
    project_root = Path(__file__).parent.parent
    schemas_dir = project_root / "schemas" / "1.0.0"

    # Read current schema files
    day_close_schema = schemas_dir / "day_close.schema.json"
    ticket_contract_schema = schemas_dir / "ticket_contract.schema.json"
    manifest = schemas_dir / "manifest.json"

    with day_close_schema.open() as f:
        day_close_content = f.read()
    with ticket_contract_schema.open() as f:
        ticket_contract_content = f.read()
    with manifest.open() as f:
        manifest_content = f.read()

    # Re-run export script
    script_path = project_root / "scripts" / "export_json_schema.py"
    subprocess.run(  # noqa: S603
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    # Read exported files again
    with day_close_schema.open() as f:
        day_close_content_after = f.read()
    with ticket_contract_schema.open() as f:
        ticket_contract_content_after = f.read()
    with manifest.open() as f:
        manifest_content_after = f.read()

    # Content should be identical (deterministic)
    assert day_close_content == day_close_content_after
    assert ticket_contract_content == ticket_contract_content_after
    assert manifest_content == manifest_content_after
