# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for doc freshness models, enums, and scanners."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from onex_change_control.enums.enum_doc_reference_type import EnumDocReferenceType
from onex_change_control.enums.enum_doc_staleness_verdict import EnumDocStalenessVerdict
from onex_change_control.models.model_doc_cross_ref_check import ModelDocCrossRefCheck
from onex_change_control.models.model_doc_freshness_result import (
    ModelDocFreshnessResult,
)
from onex_change_control.models.model_doc_freshness_sweep_report import (
    ModelDocFreshnessSweepReport,
    ModelRepoDocSummary,
)
from onex_change_control.models.model_doc_reference import ModelDocReference
from onex_change_control.scanners.doc_reference_extractor import (
    extract_all_references,
    extract_class_names,
    extract_commands,
    extract_env_vars,
    extract_file_paths,
    extract_function_names,
    extract_urls,
)
from onex_change_control.scanners.doc_staleness_detector import (
    assign_verdict,
    compute_staleness_score,
)

# ── Enum Tests ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEnumDocReferenceType:
    """Tests for EnumDocReferenceType."""

    def test_all_values_exist(self) -> None:
        assert EnumDocReferenceType.FILE_PATH == "FILE_PATH"
        assert EnumDocReferenceType.FUNCTION_NAME == "FUNCTION_NAME"
        assert EnumDocReferenceType.CLASS_NAME == "CLASS_NAME"
        assert EnumDocReferenceType.COMMAND == "COMMAND"
        assert EnumDocReferenceType.URL == "URL"
        assert EnumDocReferenceType.ENV_VAR == "ENV_VAR"

    def test_enum_count(self) -> None:
        assert len(EnumDocReferenceType) == 6


@pytest.mark.unit
class TestEnumDocStalenessVerdict:
    """Tests for EnumDocStalenessVerdict."""

    def test_all_values_exist(self) -> None:
        assert EnumDocStalenessVerdict.FRESH == "FRESH"
        assert EnumDocStalenessVerdict.STALE == "STALE"
        assert EnumDocStalenessVerdict.BROKEN == "BROKEN"
        assert EnumDocStalenessVerdict.UNKNOWN == "UNKNOWN"

    def test_enum_count(self) -> None:
        assert len(EnumDocStalenessVerdict) == 4


# ── Model Tests ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestModelDocReference:
    """Tests for ModelDocReference."""

    def test_create_minimal(self) -> None:
        ref = ModelDocReference(
            doc_path="CLAUDE.md",
            line_number=10,
            reference_type=EnumDocReferenceType.FILE_PATH,
            raw_text="src/foo/bar.py",
        )
        assert ref.doc_path == "CLAUDE.md"
        assert ref.line_number == 10
        assert ref.reference_type == EnumDocReferenceType.FILE_PATH
        assert ref.exists is None
        assert ref.resolved_target is None

    def test_frozen(self) -> None:
        ref = ModelDocReference(
            doc_path="CLAUDE.md",
            line_number=1,
            reference_type=EnumDocReferenceType.URL,
            raw_text="http://localhost:8080",
        )
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="frozen"):
            ref.exists = True  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        ref = ModelDocReference(
            doc_path="docs/README.md",
            line_number=42,
            reference_type=EnumDocReferenceType.ENV_VAR,
            raw_text="KAFKA_BOOTSTRAP_SERVERS",
            resolved_target="/home/user/.omnibase/.env",
            exists=True,
        )
        data = ref.model_dump()
        restored = ModelDocReference.model_validate(data)
        assert restored == ref

    def test_each_reference_type(self) -> None:
        for ref_type in EnumDocReferenceType:
            ref = ModelDocReference(
                doc_path="test.md",
                line_number=1,
                reference_type=ref_type,
                raw_text="test_value",
            )
            assert ref.reference_type == ref_type


@pytest.mark.unit
class TestModelDocFreshnessResult:
    """Tests for ModelDocFreshnessResult."""

    def test_create_fresh(self) -> None:
        result = ModelDocFreshnessResult(
            doc_path="CLAUDE.md",
            repo="omniclaude",
            doc_last_modified=datetime.now(tz=UTC),
            staleness_score=0.0,
            verdict=EnumDocStalenessVerdict.FRESH,
        )
        assert result.verdict == EnumDocStalenessVerdict.FRESH
        assert result.staleness_score == 0.0

    def test_broken_verdict_requires_broken_refs(self) -> None:
        with pytest.raises(ValueError, match="BROKEN but no broken references"):
            ModelDocFreshnessResult(
                doc_path="test.md",
                repo="test",
                doc_last_modified=datetime.now(tz=UTC),
                staleness_score=0.5,
                verdict=EnumDocStalenessVerdict.BROKEN,
                broken_references=[],
            )

    def test_broken_verdict_with_refs(self) -> None:
        broken_ref = ModelDocReference(
            doc_path="test.md",
            line_number=1,
            reference_type=EnumDocReferenceType.FILE_PATH,
            raw_text="src/deleted.py",
            exists=False,
        )
        result = ModelDocFreshnessResult(
            doc_path="test.md",
            repo="test",
            doc_last_modified=datetime.now(tz=UTC),
            staleness_score=0.6,
            verdict=EnumDocStalenessVerdict.BROKEN,
            broken_references=[broken_ref],
        )
        assert len(result.broken_references) == 1

    def test_unknown_verdict(self) -> None:
        result = ModelDocFreshnessResult(
            doc_path="empty.md",
            repo="test",
            doc_last_modified=datetime.now(tz=UTC),
            staleness_score=0.0,
            verdict=EnumDocStalenessVerdict.UNKNOWN,
        )
        assert result.verdict == EnumDocStalenessVerdict.UNKNOWN


@pytest.mark.unit
class TestModelDocFreshnessSweepReport:
    """Tests for ModelDocFreshnessSweepReport."""

    def test_create_empty_report(self) -> None:
        report = ModelDocFreshnessSweepReport(
            timestamp=datetime.now(tz=UTC),
            total_docs=0,
            fresh_count=0,
            stale_count=0,
            broken_count=0,
            unknown_count=0,
            total_references=0,
            broken_reference_count=0,
            stale_reference_count=0,
        )
        assert report.total_docs == 0

    def test_aggregation_with_mixed_verdicts(self) -> None:
        fresh = ModelDocFreshnessResult(
            doc_path="fresh.md",
            repo="test",
            doc_last_modified=datetime.now(tz=UTC),
            staleness_score=0.0,
            verdict=EnumDocStalenessVerdict.FRESH,
        )
        broken_ref = ModelDocReference(
            doc_path="broken.md",
            line_number=1,
            reference_type=EnumDocReferenceType.FILE_PATH,
            raw_text="src/gone.py",
            exists=False,
        )
        broken = ModelDocFreshnessResult(
            doc_path="broken.md",
            repo="test",
            doc_last_modified=datetime.now(tz=UTC) - timedelta(days=60),
            staleness_score=0.8,
            verdict=EnumDocStalenessVerdict.BROKEN,
            broken_references=[broken_ref],
        )
        report = ModelDocFreshnessSweepReport(
            timestamp=datetime.now(tz=UTC),
            repos_scanned=["test"],
            total_docs=2,
            fresh_count=1,
            stale_count=0,
            broken_count=1,
            unknown_count=0,
            total_references=1,
            broken_reference_count=1,
            stale_reference_count=0,
            results=[fresh, broken],
            top_stale_docs=["broken.md"],
        )
        assert report.total_docs == 2
        assert report.broken_count == 1
        assert len(report.top_stale_docs) == 1

    def test_per_repo_summary(self) -> None:
        summary = ModelRepoDocSummary(
            repo="omniclaude",
            total_docs=10,
            fresh=7,
            stale=2,
            broken=1,
            broken_references=3,
        )
        assert summary.fresh + summary.stale + summary.broken <= summary.total_docs


@pytest.mark.unit
class TestModelDocCrossRefCheck:
    """Tests for ModelDocCrossRefCheck."""

    def test_create(self) -> None:
        check = ModelDocCrossRefCheck(
            instruction="src/omniclaude/hooks/",
            line_number=42,
            check_type="path",
            verified=True,
            evidence="Path exists: /path/to/src/omniclaude/hooks/",
        )
        assert check.verified is True
        assert check.check_type == "path"


# ── Extractor Tests ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDocReferenceExtractor:
    """Tests for doc_reference_extractor module."""

    def test_extract_file_paths(self) -> None:
        lines = [
            "See `src/omnibase_infra/nodes/node_foo/node.py` for details.",
            "Also check `tests/unit/test_bar.py`.",
            "Not a path: `some_variable`.",
        ]
        refs = extract_file_paths("test.md", lines)
        assert len(refs) == 2
        assert refs[0].raw_text == "src/omnibase_infra/nodes/node_foo/node.py"
        assert refs[1].raw_text == "tests/unit/test_bar.py"

    def test_extract_file_paths_in_plain_text(self) -> None:
        lines = [
            "The file is at src/omnibase_core/models/foo.py in the repo.",
        ]
        refs = extract_file_paths("test.md", lines)
        assert len(refs) >= 1

    def test_extract_class_names(self) -> None:
        lines = [
            "Use `ModelTicketContract` for validation.",
            "The `EnumDriftSeverity` enum has 4 values.",
            "Also `ServiceRouter` and `HandlerEventEmitter`.",
            "But not `some_function` or `UPPER_CASE`.",
        ]
        refs = extract_class_names("test.md", lines)
        assert len(refs) == 4
        names = {r.raw_text for r in refs}
        assert "ModelTicketContract" in names
        assert "EnumDriftSeverity" in names

    def test_extract_function_names(self) -> None:
        lines = [
            "Call `classify_node()` to classify.",
            "Also `validate_schema()` is useful.",
            "Not a function: `ModelFoo`.",
        ]
        refs = extract_function_names("test.md", lines)
        assert len(refs) == 2
        assert refs[0].raw_text == "classify_node"

    def test_extract_commands_from_code_blocks(self) -> None:
        lines = [
            "Run tests:",
            "```bash",
            "uv run pytest tests/unit/ -v",
            "docker compose up -d",
            "echo 'hello'",  # not a recognized command prefix
            "```",
        ]
        refs = extract_commands("test.md", lines)
        assert len(refs) == 2
        assert refs[0].reference_type == EnumDocReferenceType.COMMAND

    def test_extract_urls(self) -> None:
        lines = [
            "Visit http://localhost:8080 for the dashboard.",
            "API docs at https://api.example.com/v1/docs.",
        ]
        refs = extract_urls("test.md", lines)
        assert len(refs) == 2

    def test_extract_env_vars(self) -> None:
        lines = [
            "Set `KAFKA_BOOTSTRAP_SERVERS` in your env.",
            "Also `POSTGRES_PASSWORD` is required.",
            "But `SOME_RANDOM_WORD` should not match.",
            "`ENABLE_REAL_TIME_EVENTS` controls events.",
        ]
        refs = extract_env_vars("test.md", lines)
        assert len(refs) == 3
        names = {r.raw_text for r in refs}
        assert "KAFKA_BOOTSTRAP_SERVERS" in names
        assert "POSTGRES_PASSWORD" in names
        assert "ENABLE_REAL_TIME_EVENTS" in names
        assert "SOME_RANDOM_WORD" not in names

    def test_no_freshness_check_annotation(self) -> None:
        lines = [
            "<!-- no-freshness-check -->",
            "See `src/deleted/path.py` for info.",
            "",
            "",
            "",
            "",
            "",
            "See `src/real/path.py` for info.",
        ]
        refs = extract_file_paths("test.md", lines)
        # Line 1 (index 1) should be skipped (within 5 lines of annotation)
        # Line 7 (index 7) is >5 lines away, should be found
        assert len(refs) == 1
        assert refs[0].raw_text == "src/real/path.py"

    def test_extract_all_references_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Test Doc\n")
            f.write("Check `src/foo/bar.py` and `ModelFoo` class.\n")
            f.write("Set `KAFKA_BOOTSTRAP_SERVERS` env var.\n")
            f.write("Visit http://localhost:3000 for dashboard.\n")
            f.write("```bash\n")
            f.write("uv run pytest tests/\n")
            f.write("```\n")
            f.flush()
            refs = extract_all_references(f.name)

        assert len(refs) >= 4  # file path, class, env var, url, command
        ref_types = {r.reference_type for r in refs}
        assert EnumDocReferenceType.FILE_PATH in ref_types
        assert EnumDocReferenceType.CLASS_NAME in ref_types
        assert EnumDocReferenceType.ENV_VAR in ref_types
        assert EnumDocReferenceType.URL in ref_types
        assert EnumDocReferenceType.COMMAND in ref_types

        # Clean up
        Path(f.name).unlink()


# ── Staleness Score Tests ───────────────────────────────────────────────────


@pytest.mark.unit
class TestStalenessScoring:
    """Tests for staleness score computation and verdict assignment."""

    def test_score_zero_references(self) -> None:
        assert compute_staleness_score(0, 0, 0) == 0.0

    def test_score_all_broken(self) -> None:
        score = compute_staleness_score(10, 10, 0)
        assert score == 0.6  # 0.6 * 1.0 + 0.4 * 0.0

    def test_score_all_stale(self) -> None:
        score = compute_staleness_score(10, 0, 10)
        assert score == 0.4  # 0.6 * 0.0 + 0.4 * 1.0

    def test_score_all_fresh(self) -> None:
        assert compute_staleness_score(10, 0, 0) == 0.0

    def test_score_mixed(self) -> None:
        score = compute_staleness_score(10, 3, 2)
        expected = 0.6 * 0.3 + 0.4 * 0.2  # 0.18 + 0.08 = 0.26
        assert abs(score - expected) < 0.001

    def test_score_capped_at_1(self) -> None:
        score = compute_staleness_score(1, 1, 1)
        assert score <= 1.0

    def test_verdict_unknown_no_refs(self) -> None:
        verdict = assign_verdict(0.0, 0, 0, 60.0)
        assert verdict == EnumDocStalenessVerdict.UNKNOWN

    def test_verdict_broken(self) -> None:
        verdict = assign_verdict(0.6, 5, 10, 60.0)
        assert verdict == EnumDocStalenessVerdict.BROKEN

    def test_verdict_stale(self) -> None:
        verdict = assign_verdict(0.4, 0, 10, 60.0)
        assert verdict == EnumDocStalenessVerdict.STALE

    def test_verdict_fresh(self) -> None:
        verdict = assign_verdict(0.1, 0, 10, 60.0)
        assert verdict == EnumDocStalenessVerdict.FRESH

    def test_verdict_stale_needs_age(self) -> None:
        # High score but doc is recent -- should be FRESH
        verdict = assign_verdict(0.4, 0, 10, 10.0)
        assert verdict == EnumDocStalenessVerdict.FRESH
