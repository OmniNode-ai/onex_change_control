"""Validate manifest.json hashes and tool version traceability.

This script verifies that:
1. manifest.json exists and is valid JSON
2. All schema files listed in manifest exist
3. SHA256 hashes in manifest match actual file contents
4. Export script version is recorded for traceability

Usage:
    poetry run python scripts/validate_manifest.py

Exit codes:
    0: All validations passed
    1: One or more validation failures
    2: Usage error (manifest not found, invalid JSON)
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# Schema version to validate (must match export script)
SCHEMA_VERSION = "1.0.0"

# Exit codes
EXIT_SUCCESS = 0
EXIT_VALIDATION_ERROR = 1
EXIT_USAGE_ERROR = 2


def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA256 hash of a file.

    Args:
        file_path: Path to the file to hash

    Returns:
        Hex-encoded SHA256 hash

    """
    sha256 = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def print_error(message: str) -> None:
    """Print error message to stderr."""
    print(f"❌ {message}", file=sys.stderr)  # noqa: T201


def print_success(message: str) -> None:
    """Print success message to stdout."""
    print(f"✅ {message}")  # noqa: T201


def print_info(message: str) -> None:
    """Print info message to stdout."""
    print(f"[INFO] {message}")  # noqa: T201


def validate_manifest(  # noqa: C901, PLR0912
    manifest_path: Path, schemas_dir: Path
) -> tuple[bool, list[str]]:
    """Validate manifest.json contents and hashes.

    Args:
        manifest_path: Path to manifest.json
        schemas_dir: Directory containing schema files

    Returns:
        Tuple of (success, list of error messages)

    """
    errors: list[str] = []

    # Load manifest
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest: dict[str, Any] = json.load(f)
    except json.JSONDecodeError as e:
        return False, [f"Invalid JSON in manifest: {e}"]
    except OSError as e:
        return False, [f"Cannot read manifest: {e}"]

    # Check required fields
    required_fields = ["schema_version", "export_script_version", "schemas"]
    for field in required_fields:
        if field not in manifest:
            errors.append(f"Missing required field: '{field}'")

    if errors:
        return False, errors

    # Validate schema_version
    if manifest["schema_version"] != SCHEMA_VERSION:
        errors.append(
            f"Schema version mismatch: manifest has '{manifest['schema_version']}', "
            f"expected '{SCHEMA_VERSION}'"
        )

    # Validate export_script_version is present and non-empty
    if not manifest["export_script_version"]:
        errors.append("export_script_version is empty (traceability violation)")

    # Validate schemas array
    schemas = manifest.get("schemas", [])
    if not isinstance(schemas, list):
        errors.append(f"'schemas' should be a list, got {type(schemas).__name__}")
        return False, errors

    if not schemas:
        errors.append("'schemas' list is empty")
        return False, errors

    # Validate each schema entry
    for schema_entry in schemas:
        if not isinstance(schema_entry, dict):
            errors.append(
                f"Schema entry should be dict, got {type(schema_entry).__name__}"
            )
            continue

        file_name = schema_entry.get("file")
        expected_hash = schema_entry.get("sha256")

        if not file_name:
            errors.append("Schema entry missing 'file' field")
            continue

        if not expected_hash:
            errors.append(f"Schema '{file_name}' missing 'sha256' hash")
            continue

        # Check file exists
        schema_file = schemas_dir / file_name
        if not schema_file.exists():
            errors.append(f"Schema file not found: '{file_name}'")
            continue

        # Validate hash
        actual_hash = calculate_file_hash(schema_file)
        if actual_hash != expected_hash:
            errors.append(
                f"Hash mismatch for '{file_name}': "
                f"expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
            )

    return len(errors) == 0, errors


def main() -> int:
    """Run manifest validation.

    Returns:
        Exit code: 0 if valid, 1 if invalid, 2 if manifest not found

    """
    project_root = Path(__file__).parent.parent
    schemas_dir = project_root / "schemas" / SCHEMA_VERSION
    manifest_path = schemas_dir / "manifest.json"

    print_info(f"Validating manifest at: {manifest_path.relative_to(project_root)}")
    print()  # noqa: T201

    # Check manifest exists
    if not manifest_path.exists():
        print_error(f"Manifest not found: {manifest_path}")
        print_info(
            "Run 'poetry run python scripts/export_json_schema.py' to generate it"
        )
        return EXIT_USAGE_ERROR

    # Validate manifest
    success, errors = validate_manifest(manifest_path, schemas_dir)

    if not success:
        print_error(f"Manifest validation failed with {len(errors)} error(s):")
        for error in errors:
            print(f"  - {error}", file=sys.stderr)  # noqa: T201
        return EXIT_VALIDATION_ERROR

    # Load manifest to show summary
    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    print_success("Manifest validation passed")
    print()  # noqa: T201
    print(f"  Schema version: {manifest['schema_version']}")  # noqa: T201
    print(f"  Export script version: {manifest['export_script_version']}")  # noqa: T201
    print(f"  Schema files: {len(manifest['schemas'])}")  # noqa: T201

    for schema in manifest["schemas"]:
        print(f"    - {schema['file']} ({schema['sha256'][:16]}...)")  # noqa: T201

    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
