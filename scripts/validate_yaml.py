#!/usr/bin/env python3
"""Validate YAML files against Pydantic schema models.

This script validates YAML artifacts (day_close.yaml, ticket contracts) against
the canonical Pydantic models. It provides actionable error messages with paths
and reasons for validation failures.

Usage:
    poetry run python scripts/validate_yaml.py <file1.yaml> [file2.yaml ...]
    poetry run python scripts/validate_yaml.py drift/day_close/2025-12-21.yaml
    poetry run python scripts/validate_yaml.py contracts/*.yaml

Exit codes:
    0: All files valid
    1: One or more files invalid
    2: Usage error (no files specified, file not found, etc.)
"""

import sys
from pathlib import Path
from typing import NoReturn

import yaml
from pydantic import ValidationError

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from onex_change_control.models import ModelDayClose, ModelTicketContract

# CLI version (increment when CLI logic changes)
CLI_VERSION = "1.0.0"

# Maximum length for truncated input display
_MAX_INPUT_DISPLAY_LENGTH = 50


def _print_stderr(message: str) -> None:
    """Print message to stderr."""
    print(message, file=sys.stderr)  # noqa: T201


def _print_stdout(message: str) -> None:
    """Print message to stdout."""
    print(message)  # noqa: T201


def print_error(message: str) -> None:
    """Print error message to stderr."""
    _print_stderr(f"[ERROR] {message}")


def print_success(message: str) -> None:
    """Print success message to stdout."""
    _print_stdout(f"[OK] {message}")


def print_info(message: str) -> None:
    """Print info message to stdout."""
    _print_stdout(f"[INFO] {message}")


def detect_schema_type(file_path: Path, data: dict) -> str:
    """Detect whether the YAML file is a day_close or ticket_contract.

    Detection logic:
    1. Path-based: If path contains 'day_close' -> day_close
    2. Path-based: If path contains 'contract' -> ticket_contract
    3. Content-based: If 'date' and 'plan_summary' fields exist -> day_close
    4. Content-based: If 'ticket_id' field exists -> ticket_contract
    5. Default: Fail with error

    Args:
        file_path: Path to the YAML file
        data: Parsed YAML data

    Returns:
        Schema type: 'day_close' or 'ticket_contract'

    Raises:
        ValueError: If schema type cannot be determined

    """
    path_str = str(file_path).lower()

    # Path-based detection
    if "day_close" in path_str:
        return "day_close"
    if "contract" in path_str:
        return "ticket_contract"

    # Content-based detection
    if isinstance(data, dict):
        if "date" in data and "plan_summary" in data:
            return "day_close"
        if "ticket_id" in data:
            return "ticket_contract"

    msg = (
        f"Cannot determine schema type for '{file_path}'. "
        "File path should contain 'day_close' or 'contract', "
        "or content should match expected schema structure."
    )
    raise ValueError(msg)


def format_validation_error(error: ValidationError) -> str:
    """Format Pydantic validation error for human readability.

    Args:
        error: Pydantic ValidationError

    Returns:
        Formatted error string with paths and reasons

    """
    lines = ["Validation errors:"]
    for err in error.errors():
        # Build path string (e.g., "process_changes_today.0.pr")
        loc_parts = [str(part) for part in err["loc"]]
        path = ".".join(loc_parts) if loc_parts else "(root)"

        # Get error type and message
        error_type = err["type"]
        msg = err["msg"]

        # Format line with path, type, and message
        lines.append(f"  - {path}: {msg} [{error_type}]")

        # Add input value hint if available
        if "input" in err:
            input_val = err["input"]
            if (
                isinstance(input_val, str)
                and len(input_val) > _MAX_INPUT_DISPLAY_LENGTH
            ):
                input_val = input_val[:_MAX_INPUT_DISPLAY_LENGTH] + "..."
            lines.append(f"    Input: {input_val!r}")

    return "\n".join(lines)


def _load_yaml_file(file_path: Path) -> dict | None:
    """Load and parse a YAML file.

    Args:
        file_path: Path to the YAML file

    Returns:
        Parsed YAML data as dict, or None if loading failed

    """
    if not file_path.exists():
        print_error(f"File not found: {file_path}")
        return None

    if not file_path.is_file():
        print_error(f"Not a file: {file_path}")
        return None

    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print_error(f"YAML parse error in '{file_path}':\n  {e}")
        return None

    if data is None:
        print_error(f"Empty file: {file_path}")
        return None

    if not isinstance(data, dict):
        type_name = type(data).__name__
        print_error(
            f"Invalid YAML structure in '{file_path}': expected dict, got {type_name}"
        )
        return None

    return data


def validate_file(file_path: Path) -> bool:
    """Validate a single YAML file against the appropriate Pydantic model.

    Args:
        file_path: Path to the YAML file

    Returns:
        True if valid, False if invalid

    """
    # Load YAML
    data = _load_yaml_file(file_path)
    if data is None:
        return False

    # Detect schema type
    try:
        schema_type = detect_schema_type(file_path, data)
    except ValueError as e:
        print_error(str(e))
        return False

    # Select model class
    model_class = ModelDayClose if schema_type == "day_close" else ModelTicketContract

    # Validate
    try:
        model_class.model_validate(data)
    except ValidationError as e:
        print_error(f"Validation failed for '{file_path}' ({schema_type}):")
        _print_stderr(format_validation_error(e))
        return False

    print_success(f"{file_path} ({schema_type})")
    return True


def print_usage() -> None:
    """Print usage information."""
    _print_stdout(__doc__ or "")


def main() -> NoReturn:
    """Run the YAML validation CLI."""
    args = sys.argv[1:]

    # Handle help flags
    if not args or args[0] in ("-h", "--help"):
        print_usage()
        sys.exit(0 if args else 2)

    # Handle version flag
    if args[0] in ("-v", "--version"):
        _print_stdout(f"validate_yaml.py v{CLI_VERSION}")
        sys.exit(0)

    # Validate files
    files = [Path(arg) for arg in args]
    print_info(f"Validating {len(files)} file(s)...")
    _print_stdout("")

    results = [validate_file(f) for f in files]

    # Summary
    valid_count = sum(results)
    total_count = len(results)

    _print_stdout("")
    if all(results):
        print_success(f"All {total_count} file(s) valid")
        sys.exit(0)
    else:
        invalid_count = total_count - valid_count
        print_error(f"{invalid_count}/{total_count} file(s) invalid")
        sys.exit(1)


if __name__ == "__main__":
    main()
