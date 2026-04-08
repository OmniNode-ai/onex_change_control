# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for dependency history model."""
from datetime import datetime, timezone

from onex_change_control.models.model_dependency_history import (
    ModelDependencyHistory,
    ModelDependencySnapshot,
)


class TestModelDependencyHistory:
    def test_initial_state_is_stable(self) -> None:
        history = ModelDependencyHistory(
            state="stable",
            snapshots=[],
            persistent_hotspots=[],
        )
        assert history.state == "stable"

    def test_snapshot_records_counts(self) -> None:
        snap = ModelDependencySnapshot(
            observed_at=datetime.now(timezone.utc),
            edge_count=5,
            wave_count=3,
            hotspot_count=1,
        )
        assert snap.edge_count == 5
