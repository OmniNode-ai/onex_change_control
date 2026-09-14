# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for eval comparator logic."""

from datetime import UTC, datetime
from typing import NamedTuple

import pytest

from onex_change_control.enums.enum_eval_metric_type import EnumEvalMetricType
from onex_change_control.enums.enum_eval_mode import EnumEvalMode
from onex_change_control.enums.enum_eval_verdict import EnumEvalVerdict
from onex_change_control.eval.comparator import compute_eval_report
from onex_change_control.models.model_eval_run import (
    ModelEvalMetric,
    ModelEvalRun,
)


class _RunMetrics(NamedTuple):
    latency: float = 1000.0
    tokens: float = 500.0
    success_rate: float = 1.0
    pattern_hit_rate: float | None = None


_DEFAULT_METRICS = _RunMetrics()


def _run(
    *,
    task_id: str,
    mode: EnumEvalMode,
    m: _RunMetrics = _DEFAULT_METRICS,
) -> ModelEvalRun:
    metrics = [
        ModelEvalMetric(
            metric_type=EnumEvalMetricType.LATENCY_MS, value=m.latency, unit="ms"
        ),
        ModelEvalMetric(
            metric_type=EnumEvalMetricType.TOKEN_COUNT, value=m.tokens, unit="count"
        ),
        ModelEvalMetric(
            metric_type=EnumEvalMetricType.SUCCESS_RATE,
            value=m.success_rate,
            unit="ratio",
        ),
    ]
    if m.pattern_hit_rate is not None:
        metrics.append(
            ModelEvalMetric(
                metric_type=EnumEvalMetricType.PATTERN_HIT_RATE,
                value=m.pattern_hit_rate,
                unit="ratio",
            )
        )
    return ModelEvalRun(
        run_id=f"{mode.value}-{task_id}",
        task_id=task_id,
        mode=mode,
        started_at=datetime(2026, 3, 27, 10, 0, tzinfo=UTC),
        completed_at=datetime(2026, 3, 27, 10, 5, tzinfo=UTC),
        success=m.success_rate >= 0.5,
        metrics=metrics,
        git_sha="abc123",
        env_snapshot={},
    )


@pytest.mark.unit
class TestComparatorPairing:
    def test_matched_runs_produce_pairs(self) -> None:
        on_runs = [_run(task_id="t1", mode=EnumEvalMode.ONEX_ON)]
        off_runs = [_run(task_id="t1", mode=EnumEvalMode.ONEX_OFF)]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert len(report.pairs) == 1
        assert report.pairs[0].task_id == "t1"

    def test_unmatched_runs_excluded(self) -> None:
        on_runs = [_run(task_id="t1", mode=EnumEvalMode.ONEX_ON)]
        off_runs = [_run(task_id="t2", mode=EnumEvalMode.ONEX_OFF)]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert len(report.pairs) == 0

    def test_mixed_matched_and_unmatched(self) -> None:
        on_runs = [
            _run(task_id="t1", mode=EnumEvalMode.ONEX_ON),
            _run(task_id="t2", mode=EnumEvalMode.ONEX_ON),
        ]
        off_runs = [
            _run(task_id="t1", mode=EnumEvalMode.ONEX_OFF),
            _run(task_id="t3", mode=EnumEvalMode.ONEX_OFF),
        ]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert len(report.pairs) == 1
        assert report.pairs[0].task_id == "t1"


@pytest.mark.unit
class TestComparatorVerdicts:
    def test_success_rate_improvement_is_better(self) -> None:
        on_runs = [
            _run(
                task_id="t1", mode=EnumEvalMode.ONEX_ON, m=_RunMetrics(success_rate=1.0)
            )
        ]
        off_runs = [
            _run(
                task_id="t1",
                mode=EnumEvalMode.ONEX_OFF,
                m=_RunMetrics(success_rate=0.5),
            )
        ]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert report.pairs[0].verdict == EnumEvalVerdict.ONEX_BETTER

    def test_success_rate_degradation_is_worse(self) -> None:
        on_runs = [
            _run(
                task_id="t1", mode=EnumEvalMode.ONEX_ON, m=_RunMetrics(success_rate=0.3)
            )
        ]
        off_runs = [
            _run(
                task_id="t1",
                mode=EnumEvalMode.ONEX_OFF,
                m=_RunMetrics(success_rate=0.8),
            )
        ]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert report.pairs[0].verdict == EnumEvalVerdict.ONEX_WORSE

    def test_latency_reduction_above_threshold_is_better(self) -> None:
        """ONEX ON 800ms vs OFF 1000ms = -20% = better."""
        on_runs = [
            _run(task_id="t1", mode=EnumEvalMode.ONEX_ON, m=_RunMetrics(latency=800.0))
        ]
        off_runs = [
            _run(
                task_id="t1", mode=EnumEvalMode.ONEX_OFF, m=_RunMetrics(latency=1000.0)
            )
        ]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert report.pairs[0].verdict == EnumEvalVerdict.ONEX_BETTER

    def test_latency_increase_above_threshold_is_worse(self) -> None:
        """ONEX ON 1200ms vs OFF 1000ms = +20% = worse."""
        on_runs = [
            _run(task_id="t1", mode=EnumEvalMode.ONEX_ON, m=_RunMetrics(latency=1200.0))
        ]
        off_runs = [
            _run(
                task_id="t1", mode=EnumEvalMode.ONEX_OFF, m=_RunMetrics(latency=1000.0)
            )
        ]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert report.pairs[0].verdict == EnumEvalVerdict.ONEX_WORSE

    def test_latency_at_threshold_boundary_is_neutral(self) -> None:
        """ONEX ON 905ms vs OFF 1000ms = -9.5% = below 10% = neutral."""
        on_runs = [
            _run(task_id="t1", mode=EnumEvalMode.ONEX_ON, m=_RunMetrics(latency=905.0))
        ]
        off_runs = [
            _run(
                task_id="t1", mode=EnumEvalMode.ONEX_OFF, m=_RunMetrics(latency=1000.0)
            )
        ]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert report.pairs[0].verdict == EnumEvalVerdict.NEUTRAL

    def test_token_reduction_above_threshold_is_better(self) -> None:
        """ONEX ON 450 tokens vs OFF 500 = -10% > 5% threshold = better."""
        on_runs = [
            _run(task_id="t1", mode=EnumEvalMode.ONEX_ON, m=_RunMetrics(tokens=450.0))
        ]
        off_runs = [
            _run(task_id="t1", mode=EnumEvalMode.ONEX_OFF, m=_RunMetrics(tokens=500.0))
        ]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert report.pairs[0].verdict == EnumEvalVerdict.ONEX_BETTER

    def test_equal_metrics_is_neutral(self) -> None:
        on_runs = [_run(task_id="t1", mode=EnumEvalMode.ONEX_ON)]
        off_runs = [_run(task_id="t1", mode=EnumEvalMode.ONEX_OFF)]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert report.pairs[0].verdict == EnumEvalVerdict.NEUTRAL


@pytest.mark.unit
class TestComparatorSummary:
    def test_summary_with_mixed_verdicts(self) -> None:
        on_runs = [
            _run(task_id="t1", mode=EnumEvalMode.ONEX_ON, m=_RunMetrics(latency=700.0)),
            _run(
                task_id="t2", mode=EnumEvalMode.ONEX_ON, m=_RunMetrics(latency=1300.0)
            ),
            _run(task_id="t3", mode=EnumEvalMode.ONEX_ON),
        ]
        off_runs = [
            _run(
                task_id="t1", mode=EnumEvalMode.ONEX_OFF, m=_RunMetrics(latency=1000.0)
            ),
            _run(
                task_id="t2", mode=EnumEvalMode.ONEX_OFF, m=_RunMetrics(latency=1000.0)
            ),
            _run(task_id="t3", mode=EnumEvalMode.ONEX_OFF),
        ]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert report.summary.total_tasks == 3
        assert report.summary.onex_better_count == 1
        assert report.summary.onex_worse_count == 1
        assert report.summary.neutral_count == 1

    def test_empty_runs_produce_zero_summary(self) -> None:
        report = compute_eval_report([], [], "suite-1")
        assert report.summary.total_tasks == 0
        assert report.summary.onex_better_count == 0
        assert report.summary.avg_latency_delta_ms == 0.0

    def test_all_better_summary(self) -> None:
        on_runs = [
            _run(
                task_id=f"t{i}", mode=EnumEvalMode.ONEX_ON, m=_RunMetrics(latency=700.0)
            )
            for i in range(5)
        ]
        off_runs = [
            _run(
                task_id=f"t{i}",
                mode=EnumEvalMode.ONEX_OFF,
                m=_RunMetrics(latency=1000.0),
            )
            for i in range(5)
        ]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert report.summary.onex_better_count == 5
        assert report.summary.onex_worse_count == 0
        assert report.summary.avg_latency_delta_ms == -300.0

    def test_all_worse_summary(self) -> None:
        on_runs = [
            _run(
                task_id=f"t{i}",
                mode=EnumEvalMode.ONEX_ON,
                m=_RunMetrics(latency=1500.0),
            )
            for i in range(5)
        ]
        off_runs = [
            _run(
                task_id=f"t{i}",
                mode=EnumEvalMode.ONEX_OFF,
                m=_RunMetrics(latency=1000.0),
            )
            for i in range(5)
        ]
        report = compute_eval_report(on_runs, off_runs, "suite-1")
        assert report.summary.onex_worse_count == 5
        assert report.summary.onex_better_count == 0

    def test_suite_id_propagated(self) -> None:
        report = compute_eval_report([], [], "my-suite", "2.0.0")
        assert report.suite_id == "my-suite"
        assert report.suite_version == "2.0.0"
