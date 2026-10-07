# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Cross-repo Kafka boundary parity checker.

Reads the boundary manifest (kafka_boundaries.yaml) and verifies that
every declared Kafka boundary still holds: the producer file references
the topic, and the consumer file references the topic.

Detects:
  - Producer emits a topic but consumer file is missing or doesn't reference it
  - Consumer subscribes to a topic but producer file is missing or doesn't reference it
  - Files referenced in the manifest that no longer exist

Exit codes:
  0 — all boundaries are in parity
  1 — one or more mismatches found

OMN-5640: Layer 1 — Static Kafka Boundary Parity
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path

import yaml

PENDING_GRACE_PERIOD_DAYS = 14


@dataclass
class BoundaryEntry:
    """A single Kafka boundary declaration from the manifest."""

    topic_name: str
    producer_repo: str
    consumer_repo: str
    producer_file: str
    consumer_file: str
    topic_pattern: str
    event_schema: str
    status: str = "active"  # "active" | "pending"
    pending_since: str = ""  # ISO date string, e.g. "2026-03-24"
    pending_reason: str = ""


@dataclass
class ParityResult:
    """Result of checking a single boundary."""

    boundary: BoundaryEntry
    producer_ok: bool
    consumer_ok: bool
    producer_file_exists: bool
    consumer_file_exists: bool
    error: str = ""


@dataclass
class ParityReport:
    """Aggregated parity check results."""

    results: list[ParityResult] = field(default_factory=list)

    @property
    def has_mismatches(self) -> bool:
        return any(not r.producer_ok or not r.consumer_ok for r in self.results)

    @property
    def mismatch_count(self) -> int:
        return sum(1 for r in self.results if not r.producer_ok or not r.consumer_ok)


def load_manifest(manifest_path: Path) -> list[BoundaryEntry]:
    """Load and parse the boundary manifest YAML."""
    content = manifest_path.read_text()
    data = yaml.safe_load(content)

    if not isinstance(data, dict) or "boundaries" not in data:
        msg = f"Invalid manifest: missing 'boundaries' key in {manifest_path}"
        raise ValueError(msg)

    entries: list[BoundaryEntry] = []
    for item in data["boundaries"]:
        entries.append(
            BoundaryEntry(
                topic_name=item["topic_name"],
                producer_repo=item["producer_repo"],
                consumer_repo=item["consumer_repo"],
                producer_file=item["producer_file"],
                consumer_file=item["consumer_file"],
                topic_pattern=item["topic_pattern"],
                event_schema=item.get("event_schema", ""),
                status=item.get("status", "active"),
                pending_since=item.get("pending_since", ""),
                pending_reason=item.get("pending_reason", ""),
            )
        )
    return entries


def check_file_for_topic(
    file_path: Path,
    topic_pattern: str,
    topic_name: str,
) -> tuple[bool, bool]:
    """Check if a file exists and contains a reference to the topic.

    Returns:
        (file_exists, topic_found)
    """
    if not file_path.is_file():
        return False, False

    content = file_path.read_text()

    # First try the regex pattern from the manifest
    if re.search(topic_pattern, content):
        return True, True

    # Fall back to literal topic name search
    if topic_name in content:
        return True, True

    # Try just the event-name segment (e.g. "agent-actions" from
    # "onex.evt.omniclaude.agent-actions.v1")
    parts = topic_name.split(".")
    if len(parts) >= 4:  # noqa: PLR2004  Why: topic format has 4+ dot-separated segments
        event_name = parts[3]
        if event_name in content:
            return True, True

    return True, False


def check_boundary(
    entry: BoundaryEntry,
    repos_root: Path,
) -> ParityResult:
    """Check a single boundary for parity."""
    producer_path = repos_root / entry.producer_repo / entry.producer_file
    consumer_path = repos_root / entry.consumer_repo / entry.consumer_file

    producer_exists, producer_found = check_file_for_topic(
        producer_path, entry.topic_pattern, entry.topic_name
    )
    consumer_exists, consumer_found = check_file_for_topic(
        consumer_path, entry.topic_pattern, entry.topic_name
    )

    error_parts: list[str] = []
    if not producer_exists:
        error_parts.append(
            f"producer file missing: {entry.producer_repo}/{entry.producer_file}"
        )
    elif not producer_found:
        error_parts.append(
            f"topic not found in producer: {entry.producer_repo}/{entry.producer_file}"
        )
    if not consumer_exists:
        error_parts.append(
            f"consumer file missing: {entry.consumer_repo}/{entry.consumer_file}"
        )
    elif not consumer_found:
        error_parts.append(
            f"topic not found in consumer: {entry.consumer_repo}/{entry.consumer_file}"
        )

    return ParityResult(
        boundary=entry,
        producer_ok=producer_found,
        consumer_ok=consumer_found,
        producer_file_exists=producer_exists,
        consumer_file_exists=consumer_exists,
        error="; ".join(error_parts),
    )


def _is_pending_within_grace(entry: BoundaryEntry) -> bool:
    """Check if a pending entry is within its grace period.

    Returns True if the entry should be skipped (within grace period).
    Returns False if the entry is expired (grace period exceeded) or not pending.
    """
    if entry.status != "pending":
        return False
    if not entry.pending_since:
        return False

    from datetime import datetime

    try:
        pending_date = datetime.strptime(entry.pending_since, "%Y-%m-%d").replace(
            tzinfo=UTC
        )
    except ValueError:
        return False  # unparseable date — treat as active (don't skip)

    elapsed_days = (datetime.now(tz=UTC) - pending_date).days
    return elapsed_days <= PENDING_GRACE_PERIOD_DAYS


def run_parity_check(
    manifest_path: Path,
    repos_root: Path,
) -> ParityReport:
    """Run parity checks on all boundaries in the manifest."""
    entries = load_manifest(manifest_path)
    report = ParityReport()

    for entry in entries:
        if entry.status == "pending":
            if _is_pending_within_grace(entry):
                # Within grace period — skip with informational note
                print(
                    f"  PENDING (grace): {entry.topic_name} "
                    f"({entry.producer_repo} -> {entry.consumer_repo}) "
                    f"— {entry.pending_reason or 'no reason given'}"
                )
                continue

            # Grace period expired — treat as an error
            from datetime import datetime

            pending_date = datetime.strptime(entry.pending_since, "%Y-%m-%d").replace(
                tzinfo=UTC
            )
            elapsed_days = (datetime.now(tz=UTC) - pending_date).days
            result = ParityResult(
                boundary=entry,
                producer_ok=False,
                consumer_ok=False,
                producer_file_exists=True,
                consumer_file_exists=False,
                error=(
                    f"EXPIRED PENDING: {entry.topic_name} has been pending for "
                    f"{elapsed_days} days (since {entry.pending_since}, "
                    f"grace period is {PENDING_GRACE_PERIOD_DAYS} days). "
                    f"Reason: {entry.pending_reason or 'none'}"
                ),
            )
            report.results.append(result)
            continue

        result = check_boundary(entry, repos_root)
        report.results.append(result)

    return report


def format_report(report: ParityReport) -> str:
    """Format the parity report for human consumption."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("Kafka Boundary Parity Report")
    lines.append("=" * 72)
    lines.append("")

    ok_count = sum(1 for r in report.results if r.producer_ok and r.consumer_ok)
    fail_count = report.mismatch_count
    total = len(report.results)

    lines.append(f"Total boundaries: {total}")
    lines.append(f"  OK:       {ok_count}")
    lines.append(f"  MISMATCH: {fail_count}")
    lines.append("")

    if fail_count > 0:
        lines.append("-" * 72)
        lines.append("MISMATCHES:")
        lines.append("-" * 72)
        for result in report.results:
            if not result.producer_ok or not result.consumer_ok:
                lines.append("")
                lines.append(f"  Topic: {result.boundary.topic_name}")
                lines.append(
                    f"  Producer: {result.boundary.producer_repo} -> "
                    f"Consumer: {result.boundary.consumer_repo}"
                )
                lines.append(f"  Error: {result.error}")
        lines.append("")

    if ok_count > 0 and fail_count == 0:
        lines.append("All boundaries are in parity.")
    elif ok_count > 0:
        lines.append("-" * 72)
        lines.append("OK boundaries:")
        lines.append("-" * 72)
        for result in report.results:
            if result.producer_ok and result.consumer_ok:
                lines.append(
                    f"  [OK] {result.boundary.topic_name} "
                    f"({result.boundary.producer_repo} -> "
                    f"{result.boundary.consumer_repo})"
                )

    lines.append("")
    return "\n".join(lines)


## ---------------------------------------------------------------------------
## Schema existence validation (OMN-5773)
## ---------------------------------------------------------------------------


@dataclass
class SchemaFinding:
    """A single schema validation finding."""

    kind: str  # "SCHEMA_NOT_FOUND" or "FIELD_REFERENCE_DRIFT"
    boundary: BoundaryEntry
    message: str


@dataclass
class SchemaCheckResult:
    """Results from --check-schemas validation."""

    errors: list[SchemaFinding] = field(default_factory=list)
    warnings: list[SchemaFinding] = field(default_factory=list)


def _find_class_in_repo(
    repos_root: Path,
    repo_name: str,
    class_name: str,
) -> tuple[Path | None, str]:
    """Search for a Python class definition in a repo.

    Returns:
        (file_path, class_body) if found, (None, "") if not found.
    """
    repo_dir = repos_root / repo_name
    if not repo_dir.is_dir():
        return None, ""

    class_re = re.compile(
        rf"^class\s+{re.escape(class_name)}\s*[\(:]",
        re.MULTILINE,
    )

    for py_file in repo_dir.rglob("*.py"):
        try:
            content = py_file.read_text()
        except OSError:
            continue

        match = class_re.search(content)
        if match:
            # Extract class body: find the colon ending the class line,
            # then collect indented lines until next top-level definition
            rest = content[match.start() :]
            lines = rest.split("\n")
            body_lines: list[str] = []
            # Skip the class definition line(s) — find first ":"
            found_colon = False
            for _i, line in enumerate(lines):
                if not found_colon:
                    if ":" in line:
                        found_colon = True
                    continue
                # After colon: collect indented lines
                if line and not line[0].isspace() and line[0] != "#":
                    break
                body_lines.append(line)
            return py_file, "\n".join(body_lines)

    return None, ""


def _extract_field_names_from_class(body: str) -> set[str]:
    """Extract field names from a Pydantic/dataclass class body.

    Matches patterns like:
        field_name: type
        field_name = value
        field_name: type = value
    """
    field_re = re.compile(r"^\s+(\w+)\s*[:=]", re.MULTILINE)
    fields: set[str] = set()
    # Filter out methods, class vars, and dunder attributes
    skip_prefixes = {"def ", "class ", "@", "#", "_"}
    for match in field_re.finditer(body):
        name = match.group(1)
        line_start = body.rfind("\n", 0, match.start()) + 1
        line = body[line_start : match.end()].strip()
        if any(line.startswith(p) for p in skip_prefixes):
            continue
        if name.startswith("__"):
            continue
        fields.add(name)
    return fields


def check_schemas(
    boundaries: list[BoundaryEntry],
    repos_root: Path,
) -> SchemaCheckResult:
    """Validate event_schema references in boundary entries.

    Two finding severities:
    1. SCHEMA_NOT_FOUND (ERROR): declared class doesn't exist in producer repo
    2. FIELD_REFERENCE_DRIFT (WARNING): producer fields not referenced in consumer
    """
    result = SchemaCheckResult()

    for entry in boundaries:
        if not entry.event_schema:
            continue

        # Check if class exists in producer repo
        class_path, class_body = _find_class_in_repo(
            repos_root, entry.producer_repo, entry.event_schema
        )

        if class_path is None:
            result.errors.append(
                SchemaFinding(
                    kind="SCHEMA_NOT_FOUND",
                    boundary=entry,
                    message=(
                        f"event_schema '{entry.event_schema}' not found in "
                        f"producer repo '{entry.producer_repo}'"
                    ),
                )
            )
            continue

        # Class found — check for field reference drift in consumer
        producer_fields = _extract_field_names_from_class(class_body)
        if not producer_fields:
            continue

        consumer_path = repos_root / entry.consumer_repo / entry.consumer_file
        if not consumer_path.is_file():
            continue

        try:
            consumer_content = consumer_path.read_text()
        except OSError:
            continue

        missing_fields: list[str] = []
        for field_name in producer_fields:
            # Check both snake_case and camelCase variants
            camel = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), field_name)
            if field_name not in consumer_content and camel not in consumer_content:
                missing_fields.append(field_name)

        if missing_fields:
            result.warnings.append(
                SchemaFinding(
                    kind="FIELD_REFERENCE_DRIFT",
                    boundary=entry,
                    message=(
                        f"Producer '{entry.event_schema}' has fields not referenced "
                        f"in consumer '{entry.consumer_repo}/{entry.consumer_file}': "
                        f"{', '.join(sorted(missing_fields))}"
                    ),
                )
            )

    return result


def format_schema_report(result: SchemaCheckResult) -> str:
    """Format schema check results for human-readable output."""
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 72)
    lines.append("Schema Existence Report")
    lines.append("=" * 72)
    lines.append("")

    if result.errors:
        lines.append(f"ERRORS: {len(result.errors)}")
        for finding in result.errors:
            lines.append(f"  [ERROR] SCHEMA_NOT_FOUND: {finding.boundary.topic_name}")
            lines.append(f"          {finding.message}")
        lines.append("")

    if result.warnings:
        lines.append(f"WARNINGS: {len(result.warnings)}")
        for finding in result.warnings:
            lines.append(
                f"  [WARN]  FIELD_REFERENCE_DRIFT: {finding.boundary.topic_name}"
            )
            lines.append(f"          {finding.message}")
        lines.append("")

    if not result.errors and not result.warnings:
        lines.append("All schema references validated successfully.")
        lines.append("")

    return "\n".join(lines)


def _resolve_manifest_path(explicit: str | None) -> Path:
    """Resolve the manifest path, defaulting to the bundled YAML."""
    if explicit:
        return Path(explicit)
    # Default: sibling of the scripts directory
    return Path(__file__).parent.parent / "boundaries" / "kafka_boundaries.yaml"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for check-boundary-parity."""
    parser = argparse.ArgumentParser(
        description="Check cross-repo Kafka boundary parity",
    )
    parser.add_argument(
        "--manifest",
        type=str,
        default=None,
        help=("Path to kafka_boundaries.yaml manifest (default: bundled in package)"),
    )
    parser.add_argument(
        "--repos-root",
        type=str,
        required=True,
        help="Root directory containing bare repo clones (e.g. /path/to/omni_home)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON instead of human-readable text",
    )
    parser.add_argument(
        "--check-schemas",
        action="store_true",
        help="Also validate event_schema references in boundary entries (OMN-5773).",
    )

    args = parser.parse_args(argv)

    manifest_path = _resolve_manifest_path(args.manifest)
    repos_root = Path(args.repos_root)

    if not manifest_path.is_file():
        print(f"ERROR: Manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    if not repos_root.is_dir():
        print(
            f"ERROR: Repos root not found: {repos_root}",
            file=sys.stderr,
        )
        return 1

    report = run_parity_check(manifest_path, repos_root)
    has_errors = report.has_mismatches

    # Run schema checks if requested
    schema_result: SchemaCheckResult | None = None
    if args.check_schemas:
        entries = load_manifest(manifest_path)
        schema_result = check_schemas(entries, repos_root)
        # SCHEMA_NOT_FOUND is ERROR-level — contributes to exit code
        if schema_result.errors:
            has_errors = True
        # FIELD_REFERENCE_DRIFT is WARNING-level — does NOT affect exit code

    if args.json_output:
        import json

        output: dict[str, object] = {
            "total": len(report.results),
            "ok": sum(1 for r in report.results if r.producer_ok and r.consumer_ok),
            "mismatches": report.mismatch_count,
            "boundaries": [
                {
                    "topic": r.boundary.topic_name,
                    "producer_repo": r.boundary.producer_repo,
                    "consumer_repo": r.boundary.consumer_repo,
                    "producer_ok": r.producer_ok,
                    "consumer_ok": r.consumer_ok,
                    "error": r.error,
                }
                for r in report.results
            ],
        }
        if schema_result is not None:
            output["schema_errors"] = [
                {
                    "kind": f.kind,
                    "topic": f.boundary.topic_name,
                    "message": f.message,
                }
                for f in schema_result.errors
            ]
            output["schema_warnings"] = [
                {
                    "kind": f.kind,
                    "topic": f.boundary.topic_name,
                    "message": f.message,
                }
                for f in schema_result.warnings
            ]
        print(json.dumps(output, indent=2))
    else:
        print(format_report(report))
        if schema_result is not None:
            print(format_schema_report(schema_result))

    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
