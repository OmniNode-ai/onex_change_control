# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the Kafka boundary parity checker.

OMN-5640: Layer 1 — Static Kafka Boundary Parity
"""

from __future__ import annotations

from pathlib import Path

import pytest

from onex_change_control.scripts.check_boundary_parity import (
    BoundaryEntry,
    ParityReport,
    ParityResult,
    check_boundary,
    check_file_for_topic,
    format_report,
    load_manifest,
    main,
    run_parity_check,
)


@pytest.fixture
def sample_boundary() -> BoundaryEntry:
    return BoundaryEntry(
        topic_name="onex.evt.omniclaude.session-started.v1",
        producer_repo="repo_a",
        consumer_repo="repo_b",
        producer_file="src/topics.py",
        consumer_file="topics.yaml",
        topic_pattern=r"session-started\.v1",
        event_schema="ModelSessionStarted",
    )


@pytest.fixture
def repos_root(tmp_path: Path) -> Path:
    """Create a mock repos root with producer and consumer files."""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()

    # Producer file
    (repo_a / "src").mkdir()
    (repo_a / "src" / "topics.py").write_text(
        'SESSION_STARTED = "onex.evt.omniclaude.session-started.v1"\n'
    )

    # Consumer file
    (repo_b / "topics.yaml").write_text(
        '- topic: "onex.evt.omniclaude.session-started.v1"\n'
        '  handler: "projectSessionStarted"\n'
    )

    return tmp_path


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    """Create a minimal manifest file."""
    manifest = tmp_path / "kafka_boundaries.yaml"
    manifest.write_text(
        """version: "1"
boundaries:
  - topic_name: "onex.evt.omniclaude.session-started.v1"
    producer_repo: repo_a
    consumer_repo: repo_b
    producer_file: "src/topics.py"
    consumer_file: "topics.yaml"
    topic_pattern: "session-started\\\\.v1"
    event_schema: "ModelSessionStarted"
  - topic_name: "onex.evt.omniclaude.missing-topic.v1"
    producer_repo: repo_a
    consumer_repo: repo_b
    producer_file: "src/topics.py"
    consumer_file: "topics.yaml"
    topic_pattern: "missing-topic\\\\.v1"
    event_schema: "ModelMissingTopic"
"""
    )
    return manifest


class TestCheckFileForTopic:
    """Tests for file content scanning."""

    def test_file_exists_and_topic_found(self, tmp_path: Path) -> None:
        f = tmp_path / "topics.py"
        f.write_text('TOPIC = "onex.evt.omniclaude.session-started.v1"')
        exists, found = check_file_for_topic(
            f, r"session-started\.v1", "onex.evt.omniclaude.session-started.v1"
        )
        assert exists
        assert found

    def test_file_exists_topic_not_found(self, tmp_path: Path) -> None:
        f = tmp_path / "topics.py"
        f.write_text('TOPIC = "onex.evt.omniclaude.other-topic.v1"')
        exists, found = check_file_for_topic(
            f, r"session-started\.v1", "onex.evt.omniclaude.session-started.v1"
        )
        assert exists
        assert not found

    def test_file_missing(self, tmp_path: Path) -> None:
        f = tmp_path / "nonexistent.py"
        exists, found = check_file_for_topic(
            f, r"session-started\.v1", "onex.evt.omniclaude.session-started.v1"
        )
        assert not exists
        assert not found

    def test_event_name_fallback(self, tmp_path: Path) -> None:
        """The checker should find a topic by its event-name segment."""
        f = tmp_path / "consumer.yaml"
        # File contains the event-name but not the full topic string
        f.write_text("handler: session-started\n")
        exists, found = check_file_for_topic(
            f, r"will-not-match-regex", "onex.evt.omniclaude.session-started.v1"
        )
        assert exists
        assert found


class TestLoadManifest:
    """Tests for YAML manifest loading."""

    def test_load_valid_manifest(self, manifest_path: Path) -> None:
        entries = load_manifest(manifest_path)
        assert len(entries) == 2
        assert entries[0].topic_name == "onex.evt.omniclaude.session-started.v1"
        assert entries[0].producer_repo == "repo_a"
        assert entries[1].topic_name == "onex.evt.omniclaude.missing-topic.v1"

    def test_load_invalid_manifest(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("not_boundaries: true\n")
        with pytest.raises(ValueError, match="missing 'boundaries' key"):
            load_manifest(bad)


class TestCheckBoundary:
    """Tests for single boundary checking."""

    def test_both_sides_ok(
        self, sample_boundary: BoundaryEntry, repos_root: Path
    ) -> None:
        result = check_boundary(sample_boundary, repos_root)
        assert result.producer_ok
        assert result.consumer_ok
        assert result.error == ""

    def test_producer_missing(
        self, sample_boundary: BoundaryEntry, repos_root: Path
    ) -> None:
        # Remove producer file
        (repos_root / "repo_a" / "src" / "topics.py").unlink()
        result = check_boundary(sample_boundary, repos_root)
        assert not result.producer_ok
        assert result.consumer_ok
        assert "producer file missing" in result.error

    def test_consumer_missing(
        self, sample_boundary: BoundaryEntry, repos_root: Path
    ) -> None:
        # Remove consumer file
        (repos_root / "repo_b" / "topics.yaml").unlink()
        result = check_boundary(sample_boundary, repos_root)
        assert result.producer_ok
        assert not result.consumer_ok
        assert "consumer file missing" in result.error

    def test_topic_not_in_producer(
        self, sample_boundary: BoundaryEntry, repos_root: Path
    ) -> None:
        # Overwrite producer with unrelated content
        (repos_root / "repo_a" / "src" / "topics.py").write_text(
            'TOPIC = "onex.evt.omniclaude.unrelated.v1"\n'
        )
        result = check_boundary(sample_boundary, repos_root)
        assert not result.producer_ok
        assert result.consumer_ok
        assert "topic not found in producer" in result.error


class TestRunParityCheck:
    """Tests for the full parity check flow."""

    def test_mixed_results(self, manifest_path: Path, repos_root: Path) -> None:
        report = run_parity_check(manifest_path, repos_root)
        assert len(report.results) == 2
        # First boundary should be OK
        assert report.results[0].producer_ok
        assert report.results[0].consumer_ok
        # Second boundary (missing-topic) should fail
        assert not report.results[1].producer_ok or not report.results[1].consumer_ok
        assert report.has_mismatches
        assert report.mismatch_count == 1


class TestFormatReport:
    """Tests for report formatting."""

    def test_all_ok_report(self) -> None:
        entry = BoundaryEntry(
            topic_name="test-topic",
            producer_repo="a",
            consumer_repo="b",
            producer_file="p.py",
            consumer_file="c.yaml",
            topic_pattern="test",
            event_schema="Model",
        )
        report = ParityReport(
            results=[
                ParityResult(
                    boundary=entry,
                    producer_ok=True,
                    consumer_ok=True,
                    producer_file_exists=True,
                    consumer_file_exists=True,
                )
            ]
        )
        text = format_report(report)
        assert "All boundaries are in parity" in text
        assert "MISMATCH: 0" in text

    def test_mismatch_report(self) -> None:
        entry = BoundaryEntry(
            topic_name="broken-topic",
            producer_repo="a",
            consumer_repo="b",
            producer_file="p.py",
            consumer_file="c.yaml",
            topic_pattern="test",
            event_schema="Model",
        )
        report = ParityReport(
            results=[
                ParityResult(
                    boundary=entry,
                    producer_ok=False,
                    consumer_ok=True,
                    producer_file_exists=True,
                    consumer_file_exists=True,
                    error="topic not found in producer",
                )
            ]
        )
        text = format_report(report)
        assert "MISMATCH: 1" in text
        assert "broken-topic" in text


class TestMainCLI:
    """Tests for the CLI entry point."""

    def test_success_exit_code(self, repos_root: Path, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            """version: "1"
boundaries:
  - topic_name: "onex.evt.omniclaude.session-started.v1"
    producer_repo: repo_a
    consumer_repo: repo_b
    producer_file: "src/topics.py"
    consumer_file: "topics.yaml"
    topic_pattern: "session-started\\\\.v1"
    event_schema: "Model"
"""
        )
        exit_code = main(
            [
                "--manifest",
                str(manifest),
                "--repos-root",
                str(repos_root),
            ]
        )
        assert exit_code == 0

    def test_failure_exit_code(self, repos_root: Path, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            """version: "1"
boundaries:
  - topic_name: "onex.evt.omniclaude.nonexistent.v1"
    producer_repo: repo_a
    consumer_repo: repo_b
    producer_file: "src/topics.py"
    consumer_file: "topics.yaml"
    topic_pattern: "nonexistent\\\\.v1"
    event_schema: "Model"
"""
        )
        exit_code = main(
            [
                "--manifest",
                str(manifest),
                "--repos-root",
                str(repos_root),
            ]
        )
        assert exit_code == 1

    def test_json_output(
        self, repos_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import json

        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            """version: "1"
boundaries:
  - topic_name: "onex.evt.omniclaude.session-started.v1"
    producer_repo: repo_a
    consumer_repo: repo_b
    producer_file: "src/topics.py"
    consumer_file: "topics.yaml"
    topic_pattern: "session-started\\\\.v1"
    event_schema: "Model"
"""
        )
        main(
            [
                "--manifest",
                str(manifest),
                "--repos-root",
                str(repos_root),
                "--json",
            ]
        )
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["total"] == 1
        assert data["ok"] == 1
        assert data["mismatches"] == 0

    def test_missing_manifest(self, tmp_path: Path) -> None:
        exit_code = main(
            [
                "--manifest",
                str(tmp_path / "nonexistent.yaml"),
                "--repos-root",
                str(tmp_path),
            ]
        )
        assert exit_code == 1

    def test_missing_repos_root(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text('version: "1"\nboundaries: []\n')
        exit_code = main(
            [
                "--manifest",
                str(manifest),
                "--repos-root",
                str(tmp_path / "nonexistent"),
            ]
        )
        assert exit_code == 1


class TestBundledManifest:
    """Tests for the bundled kafka_boundaries.yaml."""

    def test_bundled_manifest_loads(self) -> None:
        """The bundled manifest should parse without errors."""
        manifest_path = (
            Path(__file__).parent.parent
            / "src"
            / "onex_change_control"
            / "boundaries"
            / "kafka_boundaries.yaml"
        )
        entries = load_manifest(manifest_path)
        assert len(entries) > 0
        # All entries should have required fields
        for entry in entries:
            assert entry.topic_name
            assert entry.producer_repo
            assert entry.consumer_repo
            assert entry.producer_file
            assert entry.consumer_file
            assert entry.topic_pattern

    def test_bundled_manifest_no_duplicate_topics(self) -> None:
        """Each producer-consumer pair for a topic should be unique."""
        manifest_path = (
            Path(__file__).parent.parent
            / "src"
            / "onex_change_control"
            / "boundaries"
            / "kafka_boundaries.yaml"
        )
        entries = load_manifest(manifest_path)
        seen: set[tuple[str, str, str]] = set()
        for entry in entries:
            key = (entry.topic_name, entry.producer_repo, entry.consumer_repo)
            assert key not in seen, f"Duplicate boundary: {key}"
            seen.add(key)

    def test_bundled_manifest_topic_format(self) -> None:
        """All topic names should follow ONEX canonical format."""
        import re

        manifest_path = (
            Path(__file__).parent.parent
            / "src"
            / "onex_change_control"
            / "boundaries"
            / "kafka_boundaries.yaml"
        )
        entries = load_manifest(manifest_path)
        topic_pattern = re.compile(
            r"^onex\.(evt|cmd)\.[a-z][a-z0-9_-]*\.[a-z][a-z0-9-]*\.v\d+$"
        )
        for entry in entries:
            assert topic_pattern.match(entry.topic_name), (
                f"Topic does not match ONEX format: {entry.topic_name}"
            )


class TestCheckSchemas:
    """Tests for event_schema existence validation (OMN-5773)."""

    def test_detects_missing_schema_class(self, tmp_path: Path) -> None:
        """event_schema references a class that doesn't exist in producer repo."""
        from onex_change_control.scripts.check_boundary_parity import check_schemas

        # Create producer repo with no matching class
        producer = tmp_path / "repo_a" / "src"
        producer.mkdir(parents=True)
        (producer / "models.py").write_text("class UnrelatedModel:\n    pass\n")

        boundaries = [
            BoundaryEntry(
                topic_name="onex.evt.test.topic.v1",
                producer_repo="repo_a",
                consumer_repo="repo_b",
                producer_file="src/models.py",
                consumer_file="consumer.py",
                topic_pattern="topic",
                event_schema="ModelDoesNotExist",
            )
        ]

        result = check_schemas(boundaries, tmp_path)
        assert len(result.errors) == 1
        assert result.errors[0].kind == "SCHEMA_NOT_FOUND"

    def test_warns_on_missing_field_in_consumer(self, tmp_path: Path) -> None:
        """Producer model has field 'session_id' but consumer doesn't reference it."""
        from onex_change_control.scripts.check_boundary_parity import check_schemas

        # Create producer repo with model class
        producer = tmp_path / "repo_a" / "src"
        producer.mkdir(parents=True)
        (producer / "models.py").write_text(
            "class ModelTestPayload:\n"
            "    session_id: str\n"
            "    correlation_id: str\n"
            "    timestamp: float\n"
        )

        # Create consumer that only references some fields
        consumer = tmp_path / "repo_b"
        consumer.mkdir(parents=True)
        (consumer / "handler.py").write_text(
            "# Consumer handler\ndata.correlation_id\ndata.timestamp\n"
        )

        boundaries = [
            BoundaryEntry(
                topic_name="onex.evt.test.topic.v1",
                producer_repo="repo_a",
                consumer_repo="repo_b",
                producer_file="src/models.py",
                consumer_file="handler.py",
                topic_pattern="topic",
                event_schema="ModelTestPayload",
            )
        ]

        result = check_schemas(boundaries, tmp_path)
        assert len(result.warnings) >= 1
        assert result.warnings[0].kind == "FIELD_REFERENCE_DRIFT"

    def test_schema_not_found_is_error_not_warning(self, tmp_path: Path) -> None:
        """SCHEMA_NOT_FOUND must be ERROR level, never WARNING."""
        from onex_change_control.scripts.check_boundary_parity import check_schemas

        # Empty producer repo
        (tmp_path / "repo_a").mkdir()

        boundaries = [
            BoundaryEntry(
                topic_name="onex.evt.test.topic.v1",
                producer_repo="repo_a",
                consumer_repo="repo_b",
                producer_file="src/models.py",
                consumer_file="handler.py",
                topic_pattern="topic",
                event_schema="ModelMissing",
            )
        ]

        result = check_schemas(boundaries, tmp_path)
        assert all(f.kind == "SCHEMA_NOT_FOUND" for f in result.errors)
        assert "SCHEMA_NOT_FOUND" not in [w.kind for w in result.warnings]

    def test_empty_event_schema_skipped(self, tmp_path: Path) -> None:
        """Boundaries with empty event_schema are skipped."""
        from onex_change_control.scripts.check_boundary_parity import check_schemas

        boundaries = [
            BoundaryEntry(
                topic_name="onex.evt.test.topic.v1",
                producer_repo="repo_a",
                consumer_repo="repo_b",
                producer_file="src/models.py",
                consumer_file="handler.py",
                topic_pattern="topic",
                event_schema="",
            )
        ]

        result = check_schemas(boundaries, tmp_path)
        assert len(result.errors) == 0
        assert len(result.warnings) == 0
