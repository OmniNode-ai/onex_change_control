# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for ModelAutopilotCycleRecord."""

import pytest
import yaml

from onex_change_control.enums.enum_autopilot import (
    EnumAutopilotCycleStatus,
    EnumAutopilotStepStatus,
)
from onex_change_control.models.model_autopilot_cycle import (
    ModelAutopilotCycleRecord,
    ModelAutopilotStepResult,
)


@pytest.mark.unit
class TestModelAutopilotCycleRecord:
    def test_roundtrip(self) -> None:
        record = ModelAutopilotCycleRecord(
            schema_version="1.0.0",
            cycle_id="autopilot-close-out-20260322-abc123",
            mode="close-out",
            started_at="2026-03-22T14:30:00Z",
            completed_at="2026-03-22T14:45:00Z",
            steps=[
                ModelAutopilotStepResult(
                    step="merge-sweep", status=EnumAutopilotStepStatus.COMPLETED
                ),
                ModelAutopilotStepResult(
                    step="integration-sweep",
                    status=EnumAutopilotStepStatus.COMPLETED,
                ),
                ModelAutopilotStepResult(
                    step="release",
                    status=EnumAutopilotStepStatus.SKIPPED,
                    reason="no new merges",
                ),
                ModelAutopilotStepResult(
                    step="redeploy", status=EnumAutopilotStepStatus.COMPLETED
                ),
                ModelAutopilotStepResult(
                    step="close-day", status=EnumAutopilotStepStatus.COMPLETED
                ),
            ],
            overall_status=EnumAutopilotCycleStatus.COMPLETE,
        )
        dumped = yaml.safe_dump(record.model_dump(mode="json"))
        reloaded = yaml.safe_load(dumped)
        restored = ModelAutopilotCycleRecord.model_validate(reloaded)
        assert restored.cycle_id == record.cycle_id
        assert len(restored.steps) == 5
        assert restored.overall_status == EnumAutopilotCycleStatus.COMPLETE

    def test_not_run_step_detected(self) -> None:
        record = ModelAutopilotCycleRecord(
            schema_version="1.0.0",
            cycle_id="test",
            mode="close-out",
            started_at="2026-03-22T00:00:00Z",
            steps=[
                ModelAutopilotStepResult(
                    step="merge-sweep", status=EnumAutopilotStepStatus.COMPLETED
                ),
                ModelAutopilotStepResult(
                    step="integration-sweep",
                    status=EnumAutopilotStepStatus.NOT_RUN,
                ),
            ],
            overall_status=EnumAutopilotCycleStatus.INCOMPLETE,
        )
        not_run = [
            s for s in record.steps if s.status == EnumAutopilotStepStatus.NOT_RUN
        ]
        assert len(not_run) == 1
        assert not_run[0].step == "integration-sweep"

    def test_skipped_without_reason_raises(self) -> None:
        """Skipped step without reason must be rejected."""
        with pytest.raises(ValueError, match="non-empty reason"):
            ModelAutopilotStepResult(
                step="release",
                status=EnumAutopilotStepStatus.SKIPPED,
                reason=None,
            )

    def test_skipped_with_empty_reason_raises(self) -> None:
        """Skipped step with empty string reason must be rejected."""
        with pytest.raises(ValueError, match="non-empty reason"):
            ModelAutopilotStepResult(
                step="release",
                status=EnumAutopilotStepStatus.SKIPPED,
                reason="",
            )

    def test_overall_status_is_enum(self) -> None:
        """overall_status uses EnumAutopilotCycleStatus, not free-form string."""
        record = ModelAutopilotCycleRecord(
            schema_version="1.0.0",
            cycle_id="test",
            mode="close-out",
            started_at="2026-03-22T00:00:00Z",
            overall_status=EnumAutopilotCycleStatus.HALTED,
        )
        assert isinstance(record.overall_status, EnumAutopilotCycleStatus)
