# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Shared helpers for governance-oriented CI checkers."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

_DOC_PATTERNS: tuple[str, ...] = ("diagnosis-*.md", "audit-*.md")
_VALID_EVIDENCE_KINDS: frozenset[str] = frozenset({"live_state", "historical_record"})
_VALID_VERIFICATION_STATUSES: frozenset[str] = frozenset(
    {"verified", "bulk_stamped_unverified"}
)
_ELIGIBLE_SECTION_NAMES: frozenset[str] = frozenset(
    {
        "files",
        "deliverables",
        "acceptance criteria",
        "dod",
        "required",
        "required content",
        "required deliverables",
        "required map columns",
    }
)
_IGNORED_SECTION_NAMES: frozenset[str] = frozenset(
    {"context", "background", "related", "examples"}
)
_SECTION_HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<name>.+?)\s*$")
_SECTION_LABEL_RE = re.compile(
    r"^\s*(?:\*\*)?(?P<name>[A-Za-z][A-Za-z0-9 /()&_-]{1,60})(?:\*\*)?\s*:\s*$"
)
_BACKTICK_PATH_RE = re.compile(r"`(?P<path>(?:\.{1,2}/)?[^`\n]*[\\/][^`\n]+?)`")
_BARE_PATH_RE = re.compile(
    r"(?<!://)(?<![A-Za-z0-9_./-])(?P<path>(?:\.{1,2}/)?"
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+(?:/)?)"
)
_TOPIC_RE = re.compile(r"onex\.(?:evt|cmd)\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+\.v\d+")
_REQUIRED_PATHS_RE = re.compile(
    r"^\s*required_paths\s*:\s*(?P<value>.*)$",
    flags=re.IGNORECASE,
)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


def _normalize_repo_path(raw_path: str) -> str:
    path = raw_path.strip().strip("`").replace("\\", "/")
    if path.startswith("./"):
        path = path[2:]
    path = path.rstrip(".,;:)")
    if "(" in path and path.count("/") == 0:
        return ""
    return path


def _parse_iso_datetime(raw_value: str) -> datetime:
    normalized = raw_value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        msg = "verified_live_at must include timezone information"
        raise ValueError(msg)
    return parsed.astimezone(UTC)


def _serialize_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def parse_front_matter(text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a top-of-file YAML front-matter block."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, None

    for idx in range(1, len(lines)):
        if lines[idx].strip() != "---":
            continue
        block = "\n".join(lines[1:idx])
        try:
            parsed = yaml.safe_load(block) or {}
        except yaml.YAMLError as exc:
            return None, str(exc)
        if not isinstance(parsed, dict):
            return None, "front matter must be a YAML mapping"
        return parsed, None

    return None, "front matter is not closed"


def collect_diagnosis_docs(
    *,
    paths: list[str] | None = None,
    docs_root: str | None = None,
) -> list[Path]:
    """Collect diagnosis/audit docs from explicit paths or a docs root."""
    discovered: list[Path] = []

    for raw_path in paths or []:
        path = Path(raw_path)
        if path.is_dir():
            for pattern in _DOC_PATTERNS:
                discovered.extend(path.rglob(pattern))
            continue
        if path.is_file():
            discovered.append(path)

    if not discovered and docs_root:
        root = Path(docs_root)
        for pattern in _DOC_PATTERNS:
            discovered.extend(root.rglob(pattern))

    return sorted({path.resolve() for path in discovered})


def evaluate_diagnosis_doc_freshness(
    doc_paths: list[Path],
    *,
    reference_time: datetime,
) -> dict[str, Any]:
    """Validate diagnosis/audit docs against freshness metadata rules."""
    findings: list[dict[str, Any]] = []

    for doc_path in doc_paths:
        text = doc_path.read_text(encoding="utf-8")
        front_matter, parse_error = parse_front_matter(text)
        if parse_error is not None or front_matter is None:
            findings.append(
                {
                    "path": doc_path.as_posix(),
                    "verified_live_at": None,
                    "reason": "missing_or_invalid_front_matter",
                    "detail": parse_error,
                }
            )
            continue

        missing_fields = [
            field
            for field in (
                "verified_live_at",
                "evidence_kind",
                "verification_status",
                "verified_by",
            )
            if not front_matter.get(field)
        ]
        if missing_fields:
            findings.append(
                {
                    "path": doc_path.as_posix(),
                    "verified_live_at": _serialize_json_value(
                        front_matter.get("verified_live_at")
                    ),
                    "reason": f"missing_required_field:{missing_fields[0]}",
                }
            )
            continue

        evidence_kind = str(front_matter["evidence_kind"])
        verification_status = str(front_matter["verification_status"])

        if evidence_kind not in _VALID_EVIDENCE_KINDS:
            findings.append(
                {
                    "path": doc_path.as_posix(),
                    "verified_live_at": _serialize_json_value(
                        front_matter.get("verified_live_at")
                    ),
                    "reason": f"invalid_evidence_kind:{evidence_kind}",
                }
            )
            continue

        if verification_status not in _VALID_VERIFICATION_STATUSES:
            findings.append(
                {
                    "path": doc_path.as_posix(),
                    "verified_live_at": _serialize_json_value(
                        front_matter.get("verified_live_at")
                    ),
                    "reason": f"invalid_verification_status:{verification_status}",
                }
            )
            continue

        try:
            verified_live_at = _parse_iso_datetime(
                str(front_matter["verified_live_at"])
            )
        except ValueError:
            findings.append(
                {
                    "path": doc_path.as_posix(),
                    "verified_live_at": _serialize_json_value(
                        front_matter.get("verified_live_at")
                    ),
                    "reason": "invalid_verified_live_at",
                }
            )
            continue

        if (
            evidence_kind == "live_state"
            and verification_status == "bulk_stamped_unverified"
        ):
            findings.append(
                {
                    "path": doc_path.as_posix(),
                    "verified_live_at": verified_live_at.isoformat(),
                    "reason": "bulk_stamped_unverified_live_state",
                }
            )
            continue

        if (
            evidence_kind == "live_state"
            and verification_status == "verified"
            and reference_time - verified_live_at > timedelta(hours=24)
        ):
            findings.append(
                {
                    "path": doc_path.as_posix(),
                    "verified_live_at": verified_live_at.isoformat(),
                    "reason": "stale_live_state",
                }
            )

    return {
        "checked_at": reference_time.isoformat(),
        "scanned_docs": len(doc_paths),
        "stale_docs": findings,
    }


def _extract_section_name(line: str) -> str | None:
    match = _SECTION_HEADER_RE.match(line)
    if match:
        return match.group("name").strip()
    match = _SECTION_LABEL_RE.match(line)
    if match:
        return match.group("name").strip()
    return None


def _normalize_section_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", name.lower())).strip()


def split_markdown_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown text into named sections."""
    sections: list[tuple[str, str]] = []
    current_name = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        section_name = _extract_section_name(line)
        if section_name is None:
            current_lines.append(line)
            continue
        sections.append((current_name, "\n".join(current_lines)))
        current_name = section_name
        current_lines = []

    sections.append((current_name, "\n".join(current_lines)))
    return sections


def extract_paths_from_text(text: str) -> list[str]:
    """Extract file or directory shaped paths from free-form text."""
    extracted: list[str] = []

    for match in _BACKTICK_PATH_RE.finditer(text):
        normalized = _normalize_repo_path(match.group("path"))
        if "/" in normalized and "://" not in normalized:
            extracted.append(normalized)

    for match in _BARE_PATH_RE.finditer(text):
        normalized = _normalize_repo_path(match.group("path"))
        if "/" in normalized and "://" not in normalized:
            extracted.append(normalized)

    return _dedupe(extracted)


def _coerce_required_paths(value: Any) -> list[str]:
    if isinstance(value, str):
        normalized = _normalize_repo_path(value)
        return [normalized] if normalized else []
    if isinstance(value, list):
        paths = [
            normalized
            for item in value
            if isinstance(item, str)
            for normalized in [_normalize_repo_path(item)]
            if normalized
        ]
        return _dedupe(paths)
    return []


def _extract_required_paths_from_yaml_block(block: str) -> list[str]:
    try:
        parsed = yaml.safe_load(block)
    except yaml.YAMLError:
        return []
    if not isinstance(parsed, dict) or "required_paths" not in parsed:
        return []
    return _coerce_required_paths(parsed["required_paths"])


def _extract_required_paths_from_fenced_blocks(description: str) -> list[str]:
    fenced_blocks = re.findall(
        r"```(?:yaml|yml)?\n(.*?)```",
        description,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in fenced_blocks:
        paths = _extract_required_paths_from_yaml_block(block)
        if paths:
            return paths
    return []


def _extract_required_paths_inline(match: re.Match[str]) -> list[str]:
    inline_value = match.group("value").strip()
    if not inline_value:
        return []
    paths = _extract_required_paths_from_yaml_block(f"required_paths: {inline_value}")
    if paths:
        return paths
    return _coerce_required_paths(inline_value)


def _collect_required_paths_block(lines: list[str], start_idx: int) -> list[str]:
    block_lines: list[str] = []
    for following in lines[start_idx + 1 :]:
        if _extract_section_name(following) is not None and block_lines:
            break
        stripped = following.strip()
        if not stripped:
            if block_lines:
                break
            continue
        if following.lstrip().startswith("- "):
            block_lines.append(following)
            continue
        if following.lstrip().startswith("* "):
            indent = following[: len(following) - len(following.lstrip())]
            block_lines.append(f"{indent}- {following.lstrip()[2:]}")
            continue
        if following.startswith((" ", "\t")):
            block_lines.append(following)
            continue
        break
    return block_lines


def _extract_required_paths_from_line_block(
    lines: list[str],
    start_idx: int,
) -> list[str]:
    block_lines = _collect_required_paths_block(lines, start_idx)
    if not block_lines:
        return []
    return _extract_required_paths_from_yaml_block(
        "required_paths:\n" + "\n".join(block_lines)
    )


def extract_required_paths_metadata(description: str) -> list[str]:
    """Extract explicit required_paths metadata from a ticket description."""
    fenced_paths = _extract_required_paths_from_fenced_blocks(description)
    if fenced_paths:
        return fenced_paths

    lines = description.splitlines()
    for idx, line in enumerate(lines):
        match = _REQUIRED_PATHS_RE.match(line)
        if match is None:
            continue
        inline_paths = _extract_required_paths_inline(match)
        if inline_paths:
            return inline_paths
        block_paths = _extract_required_paths_from_line_block(lines, idx)
        if block_paths:
            return block_paths

    return []


def extract_section_aware_required_paths(description: str) -> list[str]:
    """Extract required paths from deliverable-like sections only."""
    extracted: list[str] = []
    for section_name, section_text in split_markdown_sections(description):
        normalized_name = _normalize_section_name(section_name)
        if not normalized_name:
            continue
        if normalized_name in _IGNORED_SECTION_NAMES:
            continue
        if normalized_name not in _ELIGIBLE_SECTION_NAMES:
            continue
        extracted.extend(extract_paths_from_text(section_text))
    return _dedupe(extracted)


def extract_required_paths(description: str) -> tuple[list[str], str]:
    """Extract required paths, preferring explicit metadata."""
    metadata_paths = extract_required_paths_metadata(description)
    if metadata_paths:
        return metadata_paths, "metadata"

    section_paths = extract_section_aware_required_paths(description)
    if section_paths:
        return section_paths, "sections"

    return [], "none"


def _matches_required_path(required_path: str, pr_file: str) -> bool:
    if required_path.endswith("/"):
        return pr_file.startswith(required_path)
    return pr_file == required_path


def evaluate_ticket_file_intersection(
    *,
    ticket_id: str,
    description: str,
    pr_files: list[str],
) -> dict[str, Any]:
    """Evaluate whether a PR touches paths required by a ticket."""
    required_paths, source = extract_required_paths(description)
    if not required_paths:
        return {
            "status": "inapplicable",
            "ticket_id": ticket_id,
            "required_paths_source": source,
            "required_paths": [],
            "matched_paths": [],
            "missing_paths": [],
            "pr_files": pr_files,
        }

    matched_paths = [
        required_path
        for required_path in required_paths
        if any(_matches_required_path(required_path, pr_file) for pr_file in pr_files)
    ]
    missing_paths = [
        required_path
        for required_path in required_paths
        if required_path not in matched_paths
    ]

    return {
        "status": "pass" if matched_paths else "fail",
        "ticket_id": ticket_id,
        "required_paths_source": source,
        "required_paths": required_paths,
        "matched_paths": matched_paths,
        "missing_paths": missing_paths,
        "pr_files": pr_files,
    }


def _dedupe_normalized_paths(values: list[str]) -> list[str]:
    return _dedupe([_normalize_repo_path(value) for value in values if value.strip()])


def _load_pr_files_from_list(parsed: list[Any]) -> list[str]:
    if all(isinstance(item, str) for item in parsed):
        return _dedupe_normalized_paths([str(item) for item in parsed])
    if all(isinstance(item, dict) for item in parsed):
        return _dedupe_normalized_paths(
            [str(item["path"]) for item in parsed if "path" in item]
        )
    return []


def _load_pr_files_from_dict(parsed: dict[str, Any]) -> list[str]:
    files = parsed.get("files")
    if isinstance(files, list):
        return _load_pr_files_from_list(files)

    pr_files = parsed.get("pr_files")
    if isinstance(pr_files, list):
        return _load_pr_files_from_list(pr_files)

    return []


def load_pr_files(raw_text: str) -> list[str]:
    """Load PR files from JSON or newline-delimited text."""
    stripped = raw_text.strip()
    if not stripped:
        return []

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return _dedupe_normalized_paths(stripped.splitlines())

    if isinstance(parsed, list):
        return _load_pr_files_from_list(parsed)
    if isinstance(parsed, dict):
        return _load_pr_files_from_dict(parsed)
    return []


def extract_topics(text: str) -> list[str]:
    """Extract ONEX topic literals from markdown or YAML text."""
    return _dedupe([match.group(0) for match in _TOPIC_RE.finditer(text)])


def evaluate_integration_map_freshness(
    *,
    map_path: Path,
    plan_paths: list[Path],
) -> dict[str, Any]:
    """Check that plan topics are present in the integration map."""
    map_text = map_path.read_text(encoding="utf-8")
    map_topics = set(extract_topics(map_text))

    missing_topics: list[dict[str, Any]] = []
    for plan_path in plan_paths:
        plan_topics = extract_topics(plan_path.read_text(encoding="utf-8"))
        missing = sorted(topic for topic in plan_topics if topic not in map_topics)
        if not missing:
            continue
        missing_topics.append(
            {
                "path": plan_path.as_posix(),
                "topics": missing,
            }
        )

    return {
        "map_path": map_path.as_posix(),
        "plan_files": [path.as_posix() for path in plan_paths],
        "missing_topics": missing_topics,
    }
