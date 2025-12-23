#!/usr/bin/env python3
"""Export JSON schemas from Pydantic models.

This script exports JSON schemas for ModelDayClose and ModelTicketContract
to schemas/<schema_version>/ directories. It produces deterministic output
and generates a manifest file with schema file hashes and export tool version.

Usage:
    poetry run python scripts/export_json_schema.py
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from onex_change_control.models import ModelDayClose, ModelTicketContract

# Export script version (increment when script logic changes)
EXPORT_SCRIPT_VERSION = "1.0.0"

# Schema version (must match schema_version in models)
SCHEMA_VERSION = "1.0.0"


def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def export_json_schema(
    model_class: type["BaseModel"],
    schema_name: str,
    output_dir: Path,
) -> Path:
    """Export JSON schema for a Pydantic model.

    Args:
        model_class: Pydantic model class to export
        schema_name: Name for the schema file (e.g., "day_close")
        output_dir: Directory to write schema file to

    Returns:
        Path to the exported schema file

    """
    # Generate JSON schema using Pydantic
    schema = model_class.model_json_schema(mode="serialization")

    # Add export metadata to $comment
    if "$comment" not in schema:
        schema["$comment"] = ""
    export_comment = (
        f"Exported by onex-change-control export_json_schema.py "
        f"v{EXPORT_SCRIPT_VERSION}"
    )
    schema["$comment"] = (f"{schema.get('$comment', '')}\n{export_comment}").strip()

    # Write schema file with deterministic formatting
    output_file = output_dir / f"{schema_name}.schema.json"
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(
            schema,
            f,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        f.write("\n")  # Trailing newline for consistency

    return output_file


def create_manifest(output_dir: Path, schema_files: list[Path]) -> Path:
    """Create manifest.json with schema file metadata.

    Args:
        output_dir: Directory containing schema files
        schema_files: List of schema file paths

    Returns:
        Path to the manifest file

    """
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "export_script_version": EXPORT_SCRIPT_VERSION,
        "schemas": [],
    }

    for schema_file in sorted(schema_files):
        file_hash = calculate_file_hash(schema_file)
        manifest["schemas"].append(
            {
                "file": schema_file.name,
                "sha256": file_hash,
            }
        )

    # Write manifest with deterministic formatting
    manifest_file = output_dir / "manifest.json"
    with manifest_file.open("w", encoding="utf-8") as f:
        json.dump(
            manifest,
            f,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        f.write("\n")  # Trailing newline for consistency

    return manifest_file


def main() -> int:
    """Export JSON schemas from Pydantic models."""
    # Determine project root (parent of scripts/)
    project_root = Path(__file__).parent.parent
    schemas_dir = project_root / "schemas" / SCHEMA_VERSION

    # Create schemas directory if it doesn't exist
    schemas_dir.mkdir(parents=True, exist_ok=True)

    # Export schemas
    schema_files = []
    try:
        day_close_file = export_json_schema(
            ModelDayClose,
            "day_close",
            schemas_dir,
        )
        schema_files.append(day_close_file)
        print(f"✓ Exported day_close.schema.json to {schemas_dir}")  # noqa: T201

        ticket_contract_file = export_json_schema(
            ModelTicketContract,
            "ticket_contract",
            schemas_dir,
        )
        schema_files.append(ticket_contract_file)
        print(  # noqa: T201
            f"✓ Exported ticket_contract.schema.json to {schemas_dir}"
        )

        # Create manifest
        create_manifest(schemas_dir, schema_files)
        print(f"✓ Created manifest.json in {schemas_dir}")  # noqa: T201

        print(  # noqa: T201
            f"\n✓ Schema export complete: {len(schema_files)} schemas exported"
        )
    except (OSError, ValueError, TypeError) as e:
        print(f"✗ Error exporting schemas: {e}", file=sys.stderr)  # noqa: T201
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())
