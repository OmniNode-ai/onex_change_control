# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for ModelEvalRun, ModelEvalRunPair, and ModelEvalMetric."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from onex_change_control.enums.enum_eval_metric_type import EnumEvalMetricType
from onex_change_control.enums.enum_eval_mode import EnumEvalMode
from onex_change_control.enums.enum_eval_verdict import EnumEvalVerdict
from onex_change_control.models.model_eval_run import (
    ModelEvalMetric,
    ModelEvalRun,
    ModelEvalRunPair,
)


def _make_metric(
    metric_type: EnumEvalMetricType = EnumEvalMetricType.LATENCY_MS,
    value: float = 1500.0,
    unit: str = "ms",
) -> ModelEvalMetric:
    return ModelEvalMetric(metric_type=metric_type, value=value, unit=unit)


def _make_run(
    *,
    mode: EnumEvalMode = EnumEvalMode.ONEX_ON,
    run_id: str = "run-001",
    success: bool = True,
    metrics: list[ModelEvalMetric] | None = None,
) -> ModelEvalRun:
    return ModelEvalRun(
        run_id=run_id,
        task_id="eval-001-fix-import",
        mode=mode,
        started_at=datetime(2026, 3, 27, 10, 0, 0, tzinfo=UTC),
        completed_at=datetime(2026, 3, 27, 10, 5, 0, tzinfo=UTC),
        success=success,
        metrics=metrics
        or [
            _make_metric(EnumEvalMetricType.LATENCY_MS, 1500.0, "ms"),
            _make_metric(EnumEvalMetricType.TOKEN_COUNT, 800.0, "count"),
            _make_metric(EnumEvalMetricType.SUCCESS_RATE, 1.0, "ratio"),
        ],
        git_sha="abc123def456",
        env_snapshot={
            "ENABLE_PATTERN_ENFORCEMENT": "true"
            if mode == EnumEvalMode.ONEX_ON
            else "false"
        },
    )


@pytest.mark.unit
class TestModelEvalMetric:
    def test_create_valid(self) -> None:
        m = _make_metric()
        assert m.metric_type == EnumEvalMetricType.LATENCY_MS
        assert m.value == 1500.0
        assert m.unit == "ms"

    def test_frozen(self) -> None:
        m = _make_metric()
        with pytest.raises(ValidationError):
            m.value = 0.0  # type: ignore[misc]


@pytest.mark.unit
class TestModelEvalRun:
    def test_create_valid(self) -> None:
        run = _make_run()
        assert run.run_id == "run-001"
        assert run.mode == EnumEvalMode.ONEX_ON
        assert run.success is True
        assert len(run.metrics) == 3

    def test_get_metric_found(self) -> None:
        run = _make_run()
        val = run.get_metric(EnumEvalMetricType.LATENCY_MS)
        assert val == 1500.0

    def test_get_metric_not_found(self) -> None:
        run = _make_run()
        val = run.get_metric(EnumEvalMetricType.RETRY_COUNT)
        assert val is None

    def test_env_snapshot(self) -> None:
        on_run = _make_run(mode=EnumEvalMode.ONEX_ON)
        off_run = _make_run(mode=EnumEvalMode.ONEX_OFF, run_id="run-002")
        assert on_run.env_snapshot["ENABLE_PATTERN_ENFORCEMENT"] == "true"
        assert off_run.env_snapshot["ENABLE_PATTERN_ENFORCEMENT"] == "false"

    def test_serialization_roundtrip(self) -> None:
        run = _make_run()
        data = run.model_dump()
        restored = ModelEvalRun(**data)
        assert restored == run

    def test_json_roundtrip(self) -> None:
        run = _make_run()
        json_str = run.model_dump_json()
        restored = ModelEvalRun.model_validate_json(json_str)
        assert restored == run


@pytest.mark.unit
class TestModelEvalRunPair:
    def test_create_valid(self) -> None:
        on_run = _make_run(mode=EnumEvalMode.ONEX_ON, run_id="run-on")
        off_run = _make_run(mode=EnumEvalMode.ONEX_OFF, run_id="run-off")
        pair = ModelEvalRunPair(
            task_id="eval-001-fix-import",
            onex_on_run=on_run,
            onex_off_run=off_run,
            delta_metrics={"latency_ms": -200.0, "token_count": -50.0},
            verdict=EnumEvalVerdict.ONEX_BETTER,
        )
        assert pair.verdict == EnumEvalVerdict.ONEX_BETTER
        assert pair.delta_metrics["latency_ms"] == -200.0

    def test_delta_positive_means_onex_costs_more(self) -> None:
        """Positive delta = ONEX uses more (worse for cost metrics)."""
        on_run = _make_run(mode=EnumEvalMode.ONEX_ON, run_id="run-on")
        off_run = _make_run(mode=EnumEvalMode.ONEX_OFF, run_id="run-off")
        pair = ModelEvalRunPair(
            task_id="eval-001-fix-import",
            onex_on_run=on_run,
            onex_off_run=off_run,
            delta_metrics={"latency_ms": 500.0, "token_count": 200.0},
            verdict=EnumEvalVerdict.ONEX_WORSE,
        )
        assert pair.delta_metrics["latency_ms"] > 0
        assert pair.verdict == EnumEvalVerdict.ONEX_WORSE

    def test_neutral_verdict(self) -> None:
        on_run = _make_run(mode=EnumEvalMode.ONEX_ON, run_id="run-on")
        off_run = _make_run(mode=EnumEvalMode.ONEX_OFF, run_id="run-off")
        pair = ModelEvalRunPair(
            task_id="eval-001-fix-import",
            onex_on_run=on_run,
            onex_off_run=off_run,
            delta_metrics={"latency_ms": 10.0, "token_count": 5.0},
            verdict=EnumEvalVerdict.NEUTRAL,
        )
        assert pair.verdict == EnumEvalVerdict.NEUTRAL

    def test_serialization_roundtrip(self) -> None:
        on_run = _make_run(mode=EnumEvalMode.ONEX_ON, run_id="run-on")
        off_run = _make_run(mode=EnumEvalMode.ONEX_OFF, run_id="run-off")
        pair = ModelEvalRunPair(
            task_id="eval-001-fix-import",
            onex_on_run=on_run,
            onex_off_run=off_run,
            delta_metrics={"latency_ms": -200.0},
            verdict=EnumEvalVerdict.ONEX_BETTER,
        )
        data = pair.model_dump()
        restored = ModelEvalRunPair(**data)
        assert restored == pair
