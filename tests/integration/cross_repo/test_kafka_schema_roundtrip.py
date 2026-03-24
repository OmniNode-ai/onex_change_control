# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""
Cross-repo Kafka schema round-trip tests.

For each schema registered in SCHEMA_TEST_DATA, verify:
1. Producer model can be instantiated with test data
2. Producer model serializes to valid JSON
3. The same model class can deserialize the JSON (canonical schema stability)

Does NOT require Kafka broker — tests Pydantic model compatibility only.

TRUTH CLAIM: These tests prove canonical shared-schema stability — i.e. that a
model class round-trips through JSON without data loss or type coercion errors.
They prove producer→consumer compatibility only when both sides use the *same*
model class. Where producers and consumers use distinct model surfaces (e.g.
omnidash has its own consumer DTO), producer→consumer model-pair tests must be
added explicitly — same-class round-trips will not catch that divergence.

SCHEMA_TEST_DATA: A registry mapping Python class names to their import paths
and test data. The class names are real importable identifiers; they may differ
from the informational event_schema labels in kafka_boundaries.yaml.

CONSISTENCY GUARDS:
  Stale-entry guard: Verifies each SCHEMA_TEST_DATA class can still be imported.
  If a class is renamed or moved, this guard catches it immediately.

  Coverage-gap guard: Tracks the count of omniclaude boundary schemas (by
  event_schema label) not yet registered in SCHEMA_TEST_DATA. Fails when the
  gap GROWS (new schema added without a test), passes when stable or shrinking.

ADDING NEW SCHEMAS: When adding a new omniclaude schema, add it to
SCHEMA_TEST_DATA and verify test_omniclaude_schemas_coverage_gap passes.

Run with: OMNI_HOME=/path/to/omni_home uv run pytest tests/integration/cross_repo/ -v
"""

import importlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

_SESSION_ID = uuid4()
_CORRELATION_ID = uuid4()
_CAUSATION_ID = uuid4()
_EMITTED_AT = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)


def _add_repo_to_path(omni_home: Path, repo: str) -> None:
    """Add a sibling repo's src/ to sys.path for import."""
    src = omni_home / repo / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _add_omniclaude_deps_to_path(omni_home: Path) -> None:
    """Add omniclaude and its sibling repo dependencies to sys.path.

    omniclaude/hooks/schemas.py imports from omnibase_core and omnibase_spi.
    All sibling repo src/ directories must be on sys.path for the import to work.
    This must be called before importing any omniclaude module.
    """
    for repo in ("omniclaude", "omnibase_core", "omnibase_spi"):
        _add_repo_to_path(omni_home, repo)


def _check_omniclaude_importable(omni_home: Path) -> str | None:
    """Return None if omniclaude.hooks.schemas can be imported, else the error msg."""
    _add_omniclaude_deps_to_path(omni_home)
    error: str | None = None
    try:
        importlib.import_module("omniclaude.hooks.schemas")
    except ImportError as e:
        error = str(e)
    return error


# ---------------------------------------------------------------------------
# SCHEMA_TEST_DATA: executable registry of schemas under active cross-repo
# round-trip coverage.
#
# Keys are real Python class names. import_path + class_name must be importable
# when OMNI_HOME is set and the repo's src/ is on sys.path.
#
# MAINTENANCE RULE: When a new omniclaude schema is added to kafka_boundaries.yaml,
# decrement KNOWN_COVERAGE_GAP in test_omniclaude_schemas_coverage_gap and add
# an entry here with valid test_data.
# ---------------------------------------------------------------------------
SCHEMA_TEST_DATA: dict[str, dict] = {  # type: ignore[type-arg]
    "ModelHookPromptSubmittedPayload": {
        "import_path": "omniclaude.hooks.schemas",
        "repo": "omniclaude",
        "test_data": {
            "entity_id": _SESSION_ID,
            "session_id": str(_SESSION_ID),
            "correlation_id": _CORRELATION_ID,
            "causation_id": _CAUSATION_ID,
            "emitted_at": _EMITTED_AT,
            "prompt_id": uuid4(),
            "prompt_preview": "test prompt preview",
            "prompt_length": 42,
        },
    },
    "ModelHookToolExecutedPayload": {
        "import_path": "omniclaude.hooks.schemas",
        "repo": "omniclaude",
        "test_data": {
            "entity_id": _SESSION_ID,
            "session_id": str(_SESSION_ID),
            "correlation_id": _CORRELATION_ID,
            "causation_id": _CAUSATION_ID,
            "emitted_at": _EMITTED_AT,
            "tool_execution_id": uuid4(),
            "tool_name": "Read",
        },
    },
}

# Number of omniclaude boundary event_schema labels not yet registered in
# SCHEMA_TEST_DATA. Decrement as schemas are added. The guard fails if this
# grows (regression) or shrinks without update (coverage improved, update needed).
# NOTE: event_schema labels in kafka_boundaries.yaml are informational and may
# not match importable Python class names 1:1 — some labels are placeholders.
KNOWN_COVERAGE_GAP = 24  # All 24 unique omniclaude boundary schemas pending coverage


@pytest.mark.integration
class TestKafkaSchemaRoundTrip:
    """Verify producer→consumer schema compatibility for each boundary."""

    def test_boundary_manifest_is_not_empty(
        self,
        boundary_manifest: list[dict],  # type: ignore[type-arg]
    ) -> None:
        """Sanity: manifest should have boundaries."""
        assert len(boundary_manifest) > 0, "kafka_boundaries.yaml is empty"

    def test_all_boundary_topics_have_valid_pattern(
        self,
        boundary_manifest: list[dict],  # type: ignore[type-arg]
    ) -> None:
        """Each boundary must have a non-empty topic_pattern regex."""
        for entry in boundary_manifest:
            assert entry.get("topic_pattern"), (
                f"Boundary {entry.get('topic_name')} has no topic_pattern"
            )

    def test_known_schemas_round_trip(self, omni_home: Path) -> None:
        """For each known schema, serialize and deserialize to verify compatibility."""
        err = _check_omniclaude_importable(omni_home)
        if err:
            pytest.skip(
                f"omniclaude not importable (missing deps: {err}). "
                "Install omniclaude's full dependencies or run in an environment "
                "where omniclaude is installed."
            )
        for schema_name, config in SCHEMA_TEST_DATA.items():
            try:
                module = importlib.import_module(config["import_path"])
                model_class = getattr(module, schema_name)
            except (ImportError, AttributeError) as e:
                pytest.fail(
                    f"Cannot import {schema_name} from {config['import_path']}: {e}"
                )

            # Instantiate with test data
            instance = model_class(**config["test_data"])

            # Serialize to JSON (what the Kafka producer sends)
            json_str = instance.model_dump_json()

            # Deserialize from JSON (what the consumer receives)
            parsed = json.loads(json_str)
            restored = model_class.model_validate(parsed)

            # Verify round-trip fidelity
            assert instance == restored, (
                f"Round-trip failed for {schema_name}: "
                f"original={instance!r}, restored={restored!r}"
            )

    def test_schema_test_data_entries_are_importable(self, omni_home: Path) -> None:
        """Stale-entry guard: each SCHEMA_TEST_DATA class must still be importable.

        If a class is renamed, moved, or removed, this test catches the stale entry.
        Skips gracefully if omniclaude's transitive deps are not available.
        """
        err = _check_omniclaude_importable(omni_home)
        if err:
            pytest.skip(f"omniclaude not importable (missing deps: {err})")
        stale = []
        for schema_name, config in SCHEMA_TEST_DATA.items():
            try:
                module = importlib.import_module(config["import_path"])
                if not hasattr(module, schema_name):
                    stale.append(
                        f"{schema_name}: class not found in {config['import_path']}"
                    )
            except ImportError as e:
                stale.append(f"{schema_name}: import failed — {e}")

        assert not stale, (
            f"SCHEMA_TEST_DATA has {len(stale)} stale/broken entries: " + str(stale)
        )

    def test_omniclaude_schemas_coverage_gap(
        self,
        boundary_manifest: list[dict],  # type: ignore[type-arg]
    ) -> None:
        """Coverage-gap guard: tracks omniclaude schemas not yet in SCHEMA_TEST_DATA.

        Fails when the gap GROWS (regression: new schema added without a test).
        Fails when the gap SHRINKS (coverage improved: decrement KNOWN_COVERAGE_GAP).
        """
        omniclaude_schemas = sorted(
            {
                entry["event_schema"]
                for entry in boundary_manifest
                if (
                    entry.get("producer_repo") == "omniclaude"
                    and entry.get("event_schema")
                )
            }
        )
        # Note: SCHEMA_TEST_DATA uses Python class names; boundary event_schema labels
        # are informational. Count omniclaude schemas not appearing as any key in
        # SCHEMA_TEST_DATA (approximate match — schema names often match class names).
        missing_count = sum(1 for s in omniclaude_schemas if s not in SCHEMA_TEST_DATA)

        if missing_count > KNOWN_COVERAGE_GAP:
            pytest.fail(
                f"Coverage gap GREW: expected {KNOWN_COVERAGE_GAP} uncovered "
                f"omniclaude schemas, found {missing_count}. "
                "Add the new schema to SCHEMA_TEST_DATA."
            )
        if missing_count < KNOWN_COVERAGE_GAP:
            pytest.fail(
                f"Coverage improved: gap shrank from {KNOWN_COVERAGE_GAP} to "
                f"{missing_count}. Decrement KNOWN_COVERAGE_GAP to {missing_count}."
            )
