# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""End-to-end integration test for DoD sweep handler + CLI."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from onex_change_control.handlers.handler_dod_sweep import run_dod_sweep
from onex_change_control.models.model_dod_sweep import ModelDodSweepResult


@pytest.mark.integration
class TestDodSweepE2E:
    """End-to-end DoD sweep integration tests."""

    def test_handler_to_json_roundtrip(self, tmp_path: Path) -> None:
        """Handler result serializes to JSON and deserializes back to same model."""
        with patch(
            "onex_change_control.handlers.handler_dod_sweep.fetch_completed_tickets",
            return_value=[
                {
                    "id": "OMN-1000",
                    "title": "Test ticket",
                    "completedAt": "2026-03-24",
                }
            ],
        ):
            result = run_dod_sweep(
                contracts_dir=tmp_path,
                since_days=7,
                api_key="test-key",
            )

        # Serialize to JSON and back
        json_str = result.model_dump_json(indent=2)
        parsed = json.loads(json_str)

        # Verify roundtrip
        restored = ModelDodSweepResult(**parsed)
        assert restored.schema_version == result.schema_version
        assert restored.run_id == result.run_id
        assert restored.total_tickets == result.total_tickets
        assert restored.mode == "batch"
        assert len(restored.tickets) == 1
        assert restored.tickets[0].ticket_id == "OMN-1000"

    def test_handler_empty_sweep_produces_valid_json(self, tmp_path: Path) -> None:
        """Empty sweep (no tickets) still produces valid ModelDodSweepResult JSON."""
        with patch(
            "onex_change_control.handlers.handler_dod_sweep.fetch_completed_tickets",
            return_value=[],
        ):
            result = run_dod_sweep(
                contracts_dir=tmp_path,
                since_days=1,
                api_key="test-key",
            )

        json_str = result.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["total_tickets"] == 0
        assert parsed["schema_version"] == "1.0.0"
        assert "tickets" in parsed

    def test_handler_multiple_tickets_aggregation(self, tmp_path: Path) -> None:
        """Multiple tickets produce correct aggregate counts."""
        # Create a contract for one ticket
        (tmp_path / "OMN-2000.yaml").write_text("test: true")

        fake_tickets = [
            {"id": "OMN-2000", "title": "Has contract", "completedAt": "2026-03-24"},
            {"id": "OMN-2001", "title": "No contract", "completedAt": "2026-03-24"},
        ]
        with patch(
            "onex_change_control.handlers.handler_dod_sweep.fetch_completed_tickets",
            return_value=fake_tickets,
        ):
            result = run_dod_sweep(
                contracts_dir=tmp_path,
                since_days=7,
                api_key="test-key",
            )

        assert result.total_tickets == 2
        # OMN-2001 should fail (no contract), OMN-2000 may partially pass
        assert result.failed >= 1
