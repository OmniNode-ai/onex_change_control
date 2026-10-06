# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Validate agent configuration YAML files against ModelAgentConfig.

Usage:
    validate-agent-yaml <file1.yaml> [file2.yaml ...]
    validate-agent-yaml plugins/onex/agents/configs/*.yaml

Exit codes:
    0: All files valid
    1: One or more files invalid
    2: Usage error
"""

import sys
from pathlib import Path
from typing import NoReturn

import yaml
from pydantic import ValidationError

from onex_change_control.models.model_agent_config import ModelAgentConfig

CLI_VERSION = "1.0.0"

_MAX_INPUT_DISPLAY_LENGTH = 50


def _print_stderr(message: str) -> None:
    print(message, file=sys.stderr)


def _print_stdout(message: str) -> None:
    print(message)


def _format_validation_error(error: ValidationError) -> str:
    lines = ["Validation errors:"]
    for err in error.errors():
        loc_parts = [str(part) for part in err["loc"]]
        path = ".".join(loc_parts) if loc_parts else "(root)"
        lines.append(f"  - {path}: {err['msg']} [{err['type']}]")
        if "input" in err:
            input_val = err["input"]
            if (
                isinstance(input_val, str)
                and len(input_val) > _MAX_INPUT_DISPLAY_LENGTH
            ):
                input_val = input_val[:_MAX_INPUT_DISPLAY_LENGTH] + "..."
            lines.append(f"    Input: {input_val!r}")
    return "\n".join(lines)


def validate_file(file_path: Path) -> bool:
    if not file_path.exists():
        _print_stderr(f"[ERROR] File not found: {file_path}")
        return False

    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        _print_stderr(f"[ERROR] YAML parse error in '{file_path}':\n  {e}")
        return False

    if not isinstance(data, dict):
        type_name = type(data).__name__ if data is not None else "NoneType"
        _print_stderr(
            f"[ERROR] Invalid YAML in '{file_path}': expected dict, got {type_name}",
        )
        return False

    try:
        ModelAgentConfig.model_validate(data)
    except ValidationError as e:
        _print_stderr(f"[ERROR] Validation failed for '{file_path}':")
        _print_stderr(_format_validation_error(e))
        return False

    _print_stdout(f"[OK] {file_path}")
    return True


def main() -> NoReturn:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        _print_stdout(__doc__ or "")
        sys.exit(0 if args else 2)

    if args[0] in ("-v", "--version"):
        _print_stdout(f"validate-agent-yaml v{CLI_VERSION}")
        sys.exit(0)

    files = [Path(arg) for arg in args]
    _print_stdout(f"[INFO] Validating {len(files)} agent config(s)...\n")

    results = [validate_file(f) for f in files]

    valid_count = sum(results)
    total_count = len(results)
    invalid_count = total_count - valid_count

    _print_stdout("")
    if all(results):
        _print_stdout(f"[OK] All {total_count} file(s) valid")
    else:
        _print_stderr(f"[ERROR] {invalid_count}/{total_count} file(s) invalid")

    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
