# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""
Cross-repo contract test: Python event payloads vs omnidash Zod schemas.

Prevents the class of bug from OMN-6405 where Python emits null for None fields
but TypeScript Zod schemas used .optional() (which rejects null). The fix was
.nullable(), but this test ensures the schemas stay aligned with actual wire format.

Strategy: Load actual Kafka event fixtures captured from the running Python runtime,
then validate that every field with a null value corresponds to a .nullable() (not
.optional()) declaration in the omnidash Zod schema file.
"""

import json
import re
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_RELPATH = Path("shared") / "schemas" / "event-envelope.ts"

# Map from Kafka event fixture to the Zod schema name that validates its payload
FIXTURE_SCHEMA_MAP = {
    "node_introspection_event.json": "NodeIntrospectionPayloadSchema",
    "node_heartbeat_event.json": "NodeHeartbeatPayloadSchema",
}

# Known violations that exist in omnidash main and are tracked for fix.
# These fields use .optional() in the Zod schema but Python emits null.
# Tracked by OMN-6405 — remove entries as omnidash schemas are fixed.
KNOWN_VIOLATIONS: dict[str, set[str]] = {
    "NodeIntrospectionPayloadSchema": {"event_bus"},
    "NodeHeartbeatPayloadSchema": {"cpu_usage_percent", "memory_usage_mb"},
}


def _resolve_omnidash_schema_file() -> Path | None:
    """Resolve the omnidash event-envelope.ts schema file.

    Search order:
    1. OMNI_HOME env var (canonical for local dev)
    2. Sibling of repo root (worktree layout: ../omnidash)
    3. omni_home canonical (parent of repo root's parent)
    """
    import os

    candidates: list[Path] = []

    env = os.environ.get("OMNI_HOME")
    if env:
        candidates.append(Path(env) / "omnidash" / _SCHEMA_RELPATH)

    # Sibling of repo root (CI layout: ../omnidash alongside checkout)
    # Also covers omni_home canonical layout where repo root parent IS omni_home.
    candidates.append(_REPO_ROOT.parent / "omnidash" / _SCHEMA_RELPATH)

    for c in candidates:
        if c.exists():
            return c
    return None


def _extract_schema_block(ts_source: str, schema_name: str) -> str:
    """Extract the z.object({...}) block for a named schema from TypeScript source."""
    start_pattern = rf"export const {schema_name}\s*=\s*z\.object\(\{{"
    match = re.search(start_pattern, ts_source)
    assert match, f"Could not find {schema_name} in event-envelope.ts"

    # Count braces to find the matching close
    start = match.start()
    brace_count = 0
    for i, ch in enumerate(ts_source[start:], start):
        if ch == "{":
            brace_count += 1
        elif ch == "}":
            brace_count -= 1
            if brace_count == 0:
                return ts_source[start : i + 1]
    return ts_source[start:]


def _find_null_fields(payload: dict) -> list[str]:  # type: ignore[type-arg]
    """Return top-level field names whose value is null."""
    return sorted(k for k, v in payload.items() if v is None)


def _find_optional_only_fields(schema_block: str) -> set[str]:
    """Return field names that use .optional() but NOT .nullable() or .nullish().

    These fields will reject null values from Python, causing Zod validation
    failures that silently drop events.
    """
    optional_only: set[str] = set()
    for line in schema_block.split("\n"):
        # Match field declarations like "  field_name: z.something().optional(),"
        field_match = re.match(r"\s*(\w+)\s*:", line)
        if field_match:
            field_name = field_match.group(1)
            rest = line[field_match.end() :]
            if (
                ".optional()" in rest
                and ".nullable()" not in rest
                and ".nullish()" not in rest
            ):
                optional_only.add(field_name)
    return optional_only


@pytest.mark.integration
class TestPythonNullVsTsOptional:
    """Validate that every null-valued field in Python event fixtures
    has a .nullable() annotation in the omnidash Zod schema.

    Failure means: a Python model field emits null on the wire, but the
    omnidash Zod schema uses .optional() (which rejects null). This will
    silently drop events at runtime -- the exact bug from OMN-6405.
    """

    @pytest.fixture
    def ts_source(self) -> str:
        """Load the omnidash event-envelope.ts Zod schema source text."""
        schema_file = _resolve_omnidash_schema_file()
        if schema_file is None:
            pytest.skip(
                "omnidash event-envelope.ts not found. Set OMNI_HOME env var "
                "or clone omnidash as a sibling of onex_change_control."
            )
        return schema_file.read_text()

    @pytest.mark.parametrize(
        ("fixture_name", "schema_name"), FIXTURE_SCHEMA_MAP.items()
    )
    def test_null_fields_are_nullable_not_optional(
        self, ts_source: str, fixture_name: str, schema_name: str
    ) -> None:
        """Assert null-valued fixture fields use .nullable() not .optional() in Zod."""
        fixture_path = FIXTURES_DIR / fixture_name
        if not fixture_path.exists():
            pytest.skip(
                f"Fixture {fixture_name} not found -- run fixture capture first"
            )

        raw = json.loads(fixture_path.read_text())
        # Events are wrapped in an envelope -- extract the payload
        payload = raw.get("payload", raw)

        null_fields = _find_null_fields(payload)
        if not null_fields:
            return  # No null fields to check

        schema_block = _extract_schema_block(ts_source, schema_name)
        optional_only = _find_optional_only_fields(schema_block)

        all_violations = sorted(f for f in null_fields if f in optional_only)
        known = KNOWN_VIOLATIONS.get(schema_name, set())
        new_violations = [f for f in all_violations if f not in known]
        assert not new_violations, (
            f"Python emits null for {new_violations} but omnidash {schema_name} uses "
            f".optional() (rejects null). Fix: change to .nullable() in "
            f"shared/schemas/event-envelope.ts. See OMN-6405."
        )
        # Warn about known violations so they stay visible in test output
        stale_known = known - set(all_violations)
        if stale_known:
            pytest.fail(
                f"KNOWN_VIOLATIONS for {schema_name} lists {sorted(stale_known)} "
                f"but they are no longer violations. Remove them from the allowlist."
            )
