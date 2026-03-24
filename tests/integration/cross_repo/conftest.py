# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""
Cross-repo integration test fixtures.

These tests verify that Kafka event schemas are compatible across
repository boundaries. They do NOT require a running Kafka broker.

Run: uv run pytest tests/integration/cross_repo/ -v
"""

import os
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


@pytest.fixture
def boundary_manifest() -> list[dict]:  # type: ignore[type-arg]
    """Load the cross-repo Kafka boundary manifest."""
    with BOUNDARIES_PATH.open() as f:
        data = yaml.safe_load(f)
    return data.get("boundaries", [])  # type: ignore[no-any-return]


@pytest.fixture
def omni_home() -> Path:
    """Path to the omni_home registry.

    Resolved from OMNI_HOME env var first. Falls back to a repo-relative
    convention path only when derivation succeeds unambiguously — i.e. the
    derived candidate contains an ``omniclaude`` subdirectory.

    Raises RuntimeError with a clear message if neither strategy resolves.
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

    msg = (
        "Cannot locate omni_home. Set the OMNI_HOME environment variable "
        "to the absolute path of the omni_home registry directory. "
        f"Attempted derivation resolved to '{candidate}' but no 'omniclaude' "
        "subdirectory was found there."
    )
    raise RuntimeError(msg)
