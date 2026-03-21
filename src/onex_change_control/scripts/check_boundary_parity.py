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
from pathlib import Path

import yaml


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
    if len(parts) >= 4:  # noqa: PLR2004
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


def run_parity_check(
    manifest_path: Path,
    repos_root: Path,
) -> ParityReport:
    """Run parity checks on all boundaries in the manifest."""
    entries = load_manifest(manifest_path)
    report = ParityReport()

    for entry in entries:
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

    if args.json_output:
        import json

        output = {
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
        print(json.dumps(output, indent=2))
    else:
        print(format_report(report))

    return 1 if report.has_mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
