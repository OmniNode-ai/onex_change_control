# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Reference extractor for documentation files.

Extracts code references (file paths, class names, function names,
commands, URLs, env vars, PR numbers, and explicit ticket-state claims)
from Markdown files with line numbers.
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

_PR_REPO_ALIASES = (
    "OCC",
    "omnibase_core",
    "omniclaude",
    "omnidash",
    "omnimarket",
    "onex_change_control",
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

_PR_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_/-])(?:(?P<repo>"
    + "|".join(re.escape(repo) for repo in _PR_REPO_ALIASES)
    + r")\s*)?#(?P<number>[1-9][0-9]{1,6})(?![A-Za-z0-9_/-])"
)

_TICKET_STATE_PATTERN = re.compile(
    r"\b(?P<ticket>OMN-[0-9]{3,6})\b",
    re.IGNORECASE,
)

# Work-item table rows in planning docs (e.g. ROLLING_SEVEN_DAY_PLAN.md) use a
# short lettered label as the first cell: ``| A5 |``, ``| B2 |``, ``| X4 |``,
# ``| C4-wip |``. This intentionally does NOT match ticket-id-as-label tables
# (``| OMN-14976 |``, since ``M`` is not a digit) or header/separator rows
# (``| # |``, ``|---|``, since ``#``/``-`` are not ``[A-Z][0-9]``).
_WORK_ITEM_ROW_PATTERN = re.compile(
    r"^\|\s*(?P<label>[A-Z][0-9]{1,3}(?:-[a-z]+)?)\s*\|"
)

_OMN_TICKET_TOKEN_PATTERN = re.compile(r"\bOMN-[0-9]{3,6}\b", re.IGNORECASE)

_TICKET_STATE_WORDS = {
    "backlog": "Backlog",
    "todo": "Backlog",
    "to do": "Backlog",
    "in progress": "In Progress",
    "started": "In Progress",
    "done": "Done",
    "completed": "Done",
    "complete": "Done",
    "canceled": "Canceled",
    "cancelled": "Canceled",
}

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


def extract_pr_numbers(doc_path: str, lines: list[str]) -> list[ModelDocReference]:
    """Extract GitHub PR citations such as ``omnimarket#1034`` or ``#1034``."""
    results: list[ModelDocReference] = []
    for idx, line in enumerate(lines):
        if _is_inside_no_freshness_block(lines, idx):
            continue
        for match in _PR_NUMBER_PATTERN.finditer(line):
            repo = (match.group("repo") or "").strip()
            number = match.group("number")
            raw = f"{repo}#{number}" if repo else f"#{number}"
            results.append(
                ModelDocReference(
                    doc_path=doc_path,
                    line_number=idx + 1,
                    reference_type=EnumDocReferenceType.PR_NUMBER,
                    raw_text=raw,
                )
            )
    return results


def extract_ticket_state_claims(
    doc_path: str, lines: list[str]
) -> list[ModelDocReference]:
    """Extract explicit Linear ticket-state claims from prose and tables.

    The raw text is normalized as ``OMN-12345:<State>`` so resolvers can compare
    it to live Linear state without depending on the original sentence shape.
    """
    results: list[ModelDocReference] = []
    for idx, line in enumerate(lines):
        if _is_inside_no_freshness_block(lines, idx):
            continue
        for match in _TICKET_STATE_PATTERN.finditer(line):
            ticket = match.group("ticket").upper()
            context = line[match.end() : match.end() + 128].lower()
            state = None
            for needle, normalized in _TICKET_STATE_WORDS.items():
                if needle in context:
                    state = normalized
                    break
            if state is None:
                continue
            results.append(
                ModelDocReference(
                    doc_path=doc_path,
                    line_number=idx + 1,
                    reference_type=EnumDocReferenceType.TICKET_STATE,
                    raw_text=f"{ticket}:{state}",
                )
            )
    return results


def extract_uncited_work_item_rows(
    doc_path: str, lines: list[str]
) -> list[ModelDocReference]:
    """Flag work-item table rows with no OMN-ticket or PR citation.

    Planning docs such as ``ROLLING_SEVEN_DAY_PLAN.md`` track work in
    ``| LABEL | Work | Proof |`` tables (``| A5 |``, ``| B2 |``, ``| X4 |``,
    ``| C4-wip |``, ...). A row with neither an ``OMN-XXXX`` ticket nor a
    ``repo#NNN``/``#NNN`` PR citation anywhere in its own text has no live
    surface any checker (or session) can resolve it against.

    This is deliberately a pure presence/absence structural fact -- it does
    NOT try to classify the row's prose as "open" or "closed" (that requires
    semantic negation judgment a regex cannot make reliably; a prototype of
    that approach false-positived on a row that legitimately remains open
    behind a partial landing -- see OMN-15105). A citation-free row is simply
    unverifiable either way, which is itself the actionable finding: it is
    exactly the shape of row most likely to silently drift stale, because
    nothing anywhere can cross-check it.
    """
    results: list[ModelDocReference] = []
    for idx, line in enumerate(lines):
        if _is_inside_no_freshness_block(lines, idx):
            continue
        match = _WORK_ITEM_ROW_PATTERN.match(line)
        if match is None:
            continue
        label = match.group("label")
        has_ticket = _OMN_TICKET_TOKEN_PATTERN.search(line) is not None
        has_pr = _PR_NUMBER_PATTERN.search(line) is not None
        if has_ticket or has_pr:
            continue
        snippet = line.strip()
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."
        results.append(
            ModelDocReference(
                doc_path=doc_path,
                line_number=idx + 1,
                reference_type=EnumDocReferenceType.UNCITED_WORK_ITEM,
                raw_text=f"{label}: {snippet}",
            )
        )
    return results


def extract_all_references(doc_path: str | Path) -> list[ModelDocReference]:
    """Extract all references from a documentation file.

    Args:
        doc_path: Path to the .md file to scan. Accepts str or PosixPath.

    Returns:
        List of all extracted references with line numbers.
    """
    doc_path_str = str(doc_path)
    path = Path(doc_path_str)
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").splitlines()

    references: list[ModelDocReference] = []
    references.extend(extract_file_paths(doc_path_str, lines))
    references.extend(extract_class_names(doc_path_str, lines))
    references.extend(extract_function_names(doc_path_str, lines))
    references.extend(extract_commands(doc_path_str, lines))
    references.extend(extract_urls(doc_path_str, lines))
    references.extend(extract_env_vars(doc_path_str, lines))
    references.extend(extract_pr_numbers(doc_path_str, lines))
    references.extend(extract_ticket_state_claims(doc_path_str, lines))
    references.extend(extract_uncited_work_item_rows(doc_path_str, lines))

    return references
