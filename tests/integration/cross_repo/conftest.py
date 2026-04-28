# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""
Cross-repo integration test fixtures.

These tests verify that Kafka event schemas are compatible across
repository boundaries. They do NOT require a running Kafka broker.

Run: uv run pytest tests/integration/cross_repo/ -v
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

BOUNDARIES_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "onex_change_control"
    / "boundaries"
    / "kafka_boundaries.yaml"
)

PENDING_GRACE_PERIOD_DAYS = 14


def _is_pending_within_grace(entry: dict[str, object]) -> bool:
    """Mirror check_boundary_parity.py grace period logic."""
    if entry.get("status") != "pending":
        return False
    pending_since = entry.get("pending_since", "")
    if not pending_since:
        return True
    try:
        pending_date = datetime.strptime(str(pending_since), "%Y-%m-%d").replace(
            tzinfo=UTC
        )
    except ValueError:
        return True
    elapsed_days = (datetime.now(tz=UTC) - pending_date).days
    return elapsed_days <= PENDING_GRACE_PERIOD_DAYS


@pytest.fixture
def boundary_manifest() -> list[dict[str, object]]:
    """Load the cross-repo Kafka boundary manifest, excluding pending-grace entries.

    Mirrors the skip logic in check_boundary_parity.py so local tests and CI
    agree on which entries are enforced.
    """
    with BOUNDARIES_PATH.open() as f:
        data = yaml.safe_load(f)
    all_entries: list[dict[str, object]] = data.get("boundaries", [])
    return [e for e in all_entries if not _is_pending_within_grace(e)]


@pytest.fixture
def omni_home() -> Path:
    """Path to the omni_home registry.

    Resolved from OMNI_HOME env var first. Falls back to a repo-relative
    convention path only when derivation succeeds unambiguously — i.e. the
    derived candidate contains an ``omniclaude`` subdirectory.

    Calls pytest.skip() with a clear message if neither strategy resolves,
    so tests that depend on this fixture are skipped rather than erroring in CI.
    """
    env_path = os.environ.get("OMNI_HOME")
    if env_path:
        return Path(env_path)

    # Derive from this file's location:
    # tests/integration/cross_repo/conftest.py
    # → repo root is 4 parents up → omni_home is repo root's parent
    repo_root = Path(__file__).parent.parent.parent.parent
    candidate = repo_root.parent
    if (candidate / "omniclaude").exists():
        return candidate

    pytest.skip(
        "Cannot locate omni_home. Set the OMNI_HOME environment variable "
        "to the absolute path of the omni_home registry directory. "
        f"Attempted derivation resolved to '{candidate}' but no 'omniclaude' "
        "subdirectory was found there."
    )
