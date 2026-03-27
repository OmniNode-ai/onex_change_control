# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Reference extractor for documentation files.

Extracts code references (file paths, class names, function names,
commands, URLs, and env vars) from Markdown files with line numbers.
"""

from __future__ import annotations

import re
from pathlib import Path

from onex_change_control.enums.enum_doc_reference_type import EnumDocReferenceType
from onex_change_control.models.model_doc_reference import ModelDocReference

# Known env var prefixes that reduce false positives
_ENV_VAR_PREFIXES = (
    "KAFKA_",
    "POSTGRES_",
    "ENABLE_",
    "OMNIBASE_",
    "OMNICLAUDE_",
    "OMNIDASH_",
    "OMNIMEMORY_",
    "QDRANT_",
    "LLM_",
    "INFISICAL_",
    "GITHUB_",
    "OPENAI_",
    "ANTHROPIC_",
    "SLACK_",
    "LINEAR_",
    "ONEX_",
    "REDIS_",
    "AWS_",
    "PLUGIN_",
    "PORT",
    "HOST",
    "DATABASE",
)

# Path prefixes that indicate real file references
_PATH_PREFIXES = (
    "src/",
    "tests/",
    "scripts/",
    "docs/",
    ".github/",
    "plugins/",
    "templates/",
    "docker/",
    "deployment/",
    "consumers/",
    "contracts/",
    "monitoring/",
    "grafana/",
    "examples/",
    "drift/",
)

# Regex patterns
_FILE_PATH_PATTERN = re.compile(
    r"(?:`([^`]+)`|(?<!\w)((?:"
    + "|".join(re.escape(p) for p in _PATH_PREFIXES)
    + r")[a-zA-Z0-9_./-]+))"
)

_CLASS_PATTERN = re.compile(r"`((?:Model|Enum|Service|Handler|Node)[A-Z][a-zA-Z0-9]*)`")

_FUNCTION_PATTERN = re.compile(r"`([a-z_][a-z0-9_]*)\(\)`")

_URL_PATTERN = re.compile(r"https?://[^\s\)>\]\"']+")

_ENV_VAR_PATTERN = re.compile(r"`([A-Z][A-Z0-9_]{2,})`")

_COMMAND_PREFIXES = (
    "uv run ",
    "pytest ",
    "docker ",
    "git ",
    "ruff ",
    "mypy ",
    "python ",
    "python3 ",
    "bash ",
    "cd ",
    "curl ",
    "psql ",
    "kcat ",
    "npm ",
    "npx ",
    "pre-commit ",
)


def _is_inside_no_freshness_block(lines: list[str], line_idx: int) -> bool:
    """Check if a line is inside a <!-- no-freshness-check --> annotated block."""
    for i in range(max(0, line_idx - 5), line_idx):
        if "<!-- no-freshness-check -->" in lines[i]:
            return True
    return False


def extract_file_paths(doc_path: str, lines: list[str]) -> list[ModelDocReference]:
    """Extract file path references from doc lines."""
    results: list[ModelDocReference] = []
    for idx, line in enumerate(lines):
        if _is_inside_no_freshness_block(lines, idx):
            continue
        for match in _FILE_PATH_PATTERN.finditer(line):
            raw = match.group(1) or match.group(2)
            if not raw:
                continue
            # Filter: must look like a file path with at least one /
            if "/" not in raw:
                continue
            # Skip template/example placeholders
            if "<" in raw and ">" in raw:
                continue
            results.append(
                ModelDocReference(
                    doc_path=doc_path,
                    line_number=idx + 1,
                    reference_type=EnumDocReferenceType.FILE_PATH,
                    raw_text=raw,
                )
            )
    return results


def extract_class_names(doc_path: str, lines: list[str]) -> list[ModelDocReference]:
    """Extract class name references (Model*, Enum*, etc.) from doc lines."""
    results: list[ModelDocReference] = []
    for idx, line in enumerate(lines):
        if _is_inside_no_freshness_block(lines, idx):
            continue
        for match in _CLASS_PATTERN.finditer(line):
            results.append(
                ModelDocReference(
                    doc_path=doc_path,
                    line_number=idx + 1,
                    reference_type=EnumDocReferenceType.CLASS_NAME,
                    raw_text=match.group(1),
                )
            )
    return results


def extract_function_names(doc_path: str, lines: list[str]) -> list[ModelDocReference]:
    """Extract function name references from doc lines."""
    results: list[ModelDocReference] = []
    for idx, line in enumerate(lines):
        if _is_inside_no_freshness_block(lines, idx):
            continue
        for match in _FUNCTION_PATTERN.finditer(line):
            results.append(
                ModelDocReference(
                    doc_path=doc_path,
                    line_number=idx + 1,
                    reference_type=EnumDocReferenceType.FUNCTION_NAME,
                    raw_text=match.group(1),
                )
            )
    return results


def extract_commands(doc_path: str, lines: list[str]) -> list[ModelDocReference]:
    """Extract shell command references from code blocks."""
    results: list[ModelDocReference] = []
    in_code_block = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if not in_code_block:
            continue
        if _is_inside_no_freshness_block(lines, idx):
            continue
        # Check for command prefixes
        check_line = stripped.lstrip("$ ").lstrip("# ")
        for prefix in _COMMAND_PREFIXES:
            if check_line.startswith(prefix):
                results.append(
                    ModelDocReference(
                        doc_path=doc_path,
                        line_number=idx + 1,
                        reference_type=EnumDocReferenceType.COMMAND,
                        raw_text=check_line,
                    )
                )
                break
    return results


def extract_urls(doc_path: str, lines: list[str]) -> list[ModelDocReference]:
    """Extract URL references from doc lines."""
    results: list[ModelDocReference] = []
    for idx, line in enumerate(lines):
        if _is_inside_no_freshness_block(lines, idx):
            continue
        for match in _URL_PATTERN.finditer(line):
            results.append(
                ModelDocReference(
                    doc_path=doc_path,
                    line_number=idx + 1,
                    reference_type=EnumDocReferenceType.URL,
                    raw_text=match.group(0),
                )
            )
    return results


def extract_env_vars(doc_path: str, lines: list[str]) -> list[ModelDocReference]:
    """Extract environment variable references from doc lines.

    Only matches variables with known prefixes to reduce false positives.
    """
    results: list[ModelDocReference] = []
    for idx, line in enumerate(lines):
        if _is_inside_no_freshness_block(lines, idx):
            continue
        for match in _ENV_VAR_PATTERN.finditer(line):
            var_name = match.group(1)
            if any(var_name.startswith(prefix) for prefix in _ENV_VAR_PREFIXES):
                results.append(
                    ModelDocReference(
                        doc_path=doc_path,
                        line_number=idx + 1,
                        reference_type=EnumDocReferenceType.ENV_VAR,
                        raw_text=var_name,
                    )
                )
    return results


def extract_all_references(doc_path: str) -> list[ModelDocReference]:
    """Extract all references from a documentation file.

    Args:
        doc_path: Path to the .md file to scan.

    Returns:
        List of all extracted references with line numbers.
    """
    path = Path(doc_path)
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").splitlines()

    references: list[ModelDocReference] = []
    references.extend(extract_file_paths(doc_path, lines))
    references.extend(extract_class_names(doc_path, lines))
    references.extend(extract_function_names(doc_path, lines))
    references.extend(extract_commands(doc_path, lines))
    references.extend(extract_urls(doc_path, lines))
    references.extend(extract_env_vars(doc_path, lines))

    return references
