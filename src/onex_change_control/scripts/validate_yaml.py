#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Validate YAML files against Pydantic schema models.

This script validates YAML artifacts (day_close.yaml, ticket contracts) against
the canonical Pydantic models. It provides actionable error messages with paths
and reasons for validation failures.

Usage:
    poetry run validate-yaml <file1.yaml> [file2.yaml ...]
    poetry run validate-yaml drift/day_close/2025-12-21.yaml
    poetry run validate-yaml contracts/*.yaml

Exit codes:
    0: All files valid
    1: One or more files invalid
    2: Usage error (no files specified, file not found, etc.)
"""

import sys
from pathlib import Path
from typing import NoReturn

import yaml
from omnibase_core.models.ticket.model_contract_dod_item import ModelContractDodItem
from pydantic import ValidationError

from onex_change_control.kafka.governance_emitter import emit_governance_check_completed
from onex_change_control.models import ModelDayClose, ModelTicketContract
from onex_change_control.models.model_dod_check import ModelDodEvidenceItem
from onex_change_control.validation.contract_shape_v1 import V1_DIR

# CLI version (increment when CLI logic changes)
CLI_VERSION = "1.0.0"

# Maximum length for truncated input display
_MAX_INPUT_DISPLAY_LENGTH = 50


def _print_stderr(message: str) -> None:
    """Print message to stderr."""
    print(message, file=sys.stderr)


def _print_stdout(message: str) -> None:
    """Print message to stdout."""
    print(message)


def print_error(message: str) -> None:
    """Print error message to stderr."""
    _print_stderr(f"[ERROR] {message}")


def print_success(message: str) -> None:
    """Print success message to stdout."""
    _print_stdout(f"[OK] {message}")


def print_info(message: str) -> None:
    """Print info message to stdout."""
    _print_stdout(f"[INFO] {message}")


def detect_schema_type(file_path: Path, data: dict[str, object]) -> str:
    """Detect whether the YAML file is a day_close, ticket_contract, or the
    separate OMN-15669 contract-shape-v1 schema.

    Detection logic:
    1. Path-based: If path contains 'day_close' -> day_close
    2. Path-based: If a 'contracts/v1/' (or 'contracts\\v1\\') path segment is
       present -> contract_shape_v1 (OMN-15669's own `occ-contract/v1`
       namespace — structurally distinct from the legacy ticket contract and
       validated by its own dedicated gate, never by this CLI's
       ModelTicketContract path; checked before the generic 'contract'
       substring match below so it is not shadowed by it)
    3. Path-based: If path contains 'contract' -> ticket_contract
    4. Content-based: If 'date' and 'invariants_checked' fields exist -> day_close
    5. Content-based: If 'schema_version' == 'occ-contract/v1' -> contract_shape_v1
    6. Content-based: If 'ticket_id' field exists -> ticket_contract
    7. Default: Fail with error

    Args:
        file_path: Path to the YAML file
        data: Parsed YAML data

    Returns:
        Schema type: 'day_close', 'ticket_contract', or 'contract_shape_v1'

    Raises:
        ValueError: If schema type cannot be determined

    """
    path_str = str(file_path).lower().replace("\\", "/")

    # Path-based detection
    if "day_close" in path_str:
        return "day_close"
    if "contracts/v1/" in path_str:
        return "contract_shape_v1"
    if "contract" in path_str:
        return "ticket_contract"

    # Content-based detection
    if isinstance(data, dict):
        if "date" in data and "invariants_checked" in data:
            return "day_close"
        if data.get("schema_version") == "occ-contract/v1":
            return "contract_shape_v1"
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


# OMN-18056. `binds_ac` AGAINST AN INSTALLED CORE THAT PREDATES IT.
#
# This CLI validates `contracts/OMN-*.yaml` against omnibase_core's
# `ModelTicketContract`, whose `dod_evidence` is
# `list[ModelContractDodItem]` -- `extra="forbid"`. omnibase_core dca2ee2c
# (#1666) adds `binds_ac` to that model, but at the time of writing that commit
# sits on core's `dev` and is in NO released tag: `git tag --contains dca2ee2c`
# is empty, the newest release is 0.47.5, and this repo pins
# `omnibase-core>=0.46.8,<0.47.0` (0.46.13 installed). Measured against that
# installed core, `ModelContractDodItem.model_validate({... "binds_ac": ["AC1"]})`
# raises `extra_forbidden`.
#
# That matters more here than at any other gate: the `Validate Contract YAML
# (OMN-8808)` job runs `validate-yaml contracts/OMN-*.yaml` over the WHOLE
# corpus and is a member of `STRICT_GATE_JOBS` in `scripts/ci/ci_summary_gate.py`,
# so it sits under the required `CI Summary` umbrella. Without the shim below,
# the first contract in the corpus to declare a binding turns EVERY subsequent
# OCC pull request red, not just its own.
#
# So: when -- and only when -- the installed core model does not know the field,
# each declaring item is validated against the OCC-local `ModelDodEvidenceItem`
# (which does know it, and which owns the label rule), and the field is then
# withheld from the copy handed to core's model. The rest of the item, and the
# rest of the contract, still meet core's model unchanged.
#
# THIS IS FORWARD COMPATIBILITY WITH ONE NAMED FIELD, NOT A LOOSENING. The
# predicate is read off core's own model at import time, so the moment a core
# release carrying dca2ee2c is pinned here, `CORE_KNOWS_BINDS_AC` is True, the
# helper returns its input unchanged, and core validates the field itself. It
# deletes itself behaviourally rather than needing to be remembered. Nothing
# else is stripped: an unknown field that is not exactly `binds_ac` still
# reaches core's model and is still refused.
CORE_KNOWS_BINDS_AC = "binds_ac" in ModelContractDodItem.model_fields

_BINDS_AC_FIELD = "binds_ac"


def withhold_unreleased_binds_ac(data: dict[str, object]) -> dict[str, object]:
    """Validate declared ``binds_ac`` locally, then hide it from core's model.

    A no-op when the installed core model already knows the field, when the
    contract declares no ``dod_evidence``, or when no item declares a binding.

    Raises:
        ValidationError: when a declaring item does not satisfy the OCC-local
            ``ModelDodEvidenceItem`` -- so a malformed binding is a validation
            failure here exactly as it would be at the compliance gate, never
            something this helper quietly discards.

    """
    if CORE_KNOWS_BINDS_AC:
        return data
    items = data.get("dod_evidence")
    if not isinstance(items, list):
        return data
    if not any(isinstance(item, dict) and _BINDS_AC_FIELD in item for item in items):
        return data

    withheld: list[object] = []
    for item in items:
        if not isinstance(item, dict) or _BINDS_AC_FIELD not in item:
            withheld.append(item)
            continue
        # Raises ValidationError on a malformed label; the caller renders it
        # through the same formatter as every other validation failure.
        ModelDodEvidenceItem.model_validate(item)
        withheld.append({k: v for k, v in item.items() if k != _BINDS_AC_FIELD})
    return {**data, "dod_evidence": withheld}


def _load_yaml_file(file_path: Path) -> dict[str, object] | None:
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
            f"Invalid YAML structure in '{file_path}': expected dict, got {type_name}",
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
    # `contracts/v1/` files are the dedicated contract-shape-v1 schema
    # (schemas/occ_contract_v1.schema.yaml, enforced by `check-contract-shape-v1`),
    # not the generic ModelTicketContract wrapper this script validates against.
    # ModelTicketContract is extra="forbid" and enforces schema_version as SemVer,
    # so a v1 contract's interface/dependencies/cases/exclusions blocks and its
    # schema_version="occ-contract/v1" marker are unrepresentable there by design
    # (see contract_shape_v1.py's V1_DIR comment — "one shape per path"). Route
    # corpus-wide scans around this directory instead of failing every v1
    # contract against the wrong model.
    if V1_DIR in str(file_path).replace("\\", "/"):
        print_info(
            f"{file_path}: skipped (validated by check-contract-shape-v1 instead)"
        )
        return True

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

    # contracts/v1/ (OMN-15669's `occ-contract/v1` shape) is a distinct
    # schema from the legacy ticket contract and is not representable as a
    # ModelTicketContract (non-SemVer schema_version, extra top-level fields
    # such as `interface`/`dependencies`/`cases`/`exclusions`). Its structural
    # validation is owned by the dedicated check-contract-shape-v1 gate; this
    # CLI only confirms the file parses as YAML (already done above) and does
    # not re-run legacy ticket_contract validation against it.
    if schema_type == "contract_shape_v1":
        print_success(
            f"{file_path} ({schema_type}, validated by check-contract-shape-v1)"
        )
        return True

    # Select model class
    model_class = ModelDayClose if schema_type == "day_close" else ModelTicketContract

    # Validate
    try:
        if schema_type != "day_close":
            data = withhold_unreleased_binds_ac(data)
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
    invalid_count = total_count - valid_count

    _print_stdout("")
    passed = all(results)
    if passed:
        print_success(f"All {total_count} file(s) valid")
    else:
        print_error(f"{invalid_count}/{total_count} file(s) invalid")

    # Emit governance event (best-effort — never blocks CLI exit)

    emit_governance_check_completed(
        check_type="yaml-validation",
        target=", ".join(str(f) for f in files),
        passed=passed,
        violation_count=invalid_count,
        details={"total_files": total_count, "valid_files": valid_count},
    )

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
