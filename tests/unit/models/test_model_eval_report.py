# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for ModelEvalReport and ModelEvalSummary."""

from datetime import UTC, datetime
from typing import NamedTuple

import pytest
from pydantic import ValidationError

from onex_change_control.enums.enum_eval_metric_type import EnumEvalMetricType
from onex_change_control.enums.enum_eval_mode import EnumEvalMode
from onex_change_control.enums.enum_eval_verdict import EnumEvalVerdict
from onex_change_control.models.model_eval_report import (
    ModelEvalReport,
    ModelEvalSummary,
)
from onex_change_control.models.model_eval_run import (
    ModelEvalMetric,
    ModelEvalRun,
    ModelEvalRunPair,
)


class _RunParams(NamedTuple):
    run_id: str
    mode: EnumEvalMode
    task_id: str = "eval-001"
    latency: float = 1500.0
    tokens: float = 800.0
    success_rate: float = 1.0


def _make_run(params: _RunParams) -> ModelEvalRun:
    return ModelEvalRun(
        run_id=params.run_id,
        task_id=params.task_id,
        mode=params.mode,
        started_at=datetime(2026, 3, 27, 10, 0, 0, tzinfo=UTC),
        completed_at=datetime(2026, 3, 27, 10, 5, 0, tzinfo=UTC),
        success=params.success_rate >= 0.5,
        metrics=[
            ModelEvalMetric(
                metric_type=EnumEvalMetricType.LATENCY_MS,
                value=params.latency,
                unit="ms",
            ),
            ModelEvalMetric(
                metric_type=EnumEvalMetricType.TOKEN_COUNT,
                value=params.tokens,
                unit="count",
            ),
            ModelEvalMetric(
                metric_type=EnumEvalMetricType.SUCCESS_RATE,
                value=params.success_rate,
                unit="ratio",
            ),
        ],
        git_sha="abc123",
        env_snapshot={},
    )


def _make_pair(
    task_id: str,
    verdict: EnumEvalVerdict,
    latency_delta: float = 0.0,
    token_delta: float = 0.0,
) -> ModelEvalRunPair:
    return ModelEvalRunPair(
        task_id=task_id,
        onex_on_run=_make_run(
            _RunParams(f"on-{task_id}", EnumEvalMode.ONEX_ON, task_id)
        ),
        onex_off_run=_make_run(
            _RunParams(f"off-{task_id}", EnumEvalMode.ONEX_OFF, task_id)
        ),
        delta_metrics={
            "latency_ms": latency_delta,
            "token_count": token_delta,
        },
        verdict=verdict,
    )


def _make_summary(
    total: int = 5,
    better: int = 3,
    worse: int = 1,
    neutral: int = 1,
) -> ModelEvalSummary:
    return ModelEvalSummary(
        total_tasks=total,
        onex_better_count=better,
        onex_worse_count=worse,
        neutral_count=neutral,
        avg_latency_delta_ms=-150.0,
        avg_token_delta=-50.0,
        avg_success_rate_on=0.9,
        avg_success_rate_off=0.7,
        pattern_hit_rate_on=0.6,
    )


@pytest.mark.unit
class TestModelEvalSummary:
    def test_create_valid(self) -> None:
        s = _make_summary()
        assert s.total_tasks == 5
        assert s.onex_better_count == 3
        assert s.pattern_hit_rate_on == 0.6

    def test_all_better(self) -> None:
        s = _make_summary(total=5, better=5, worse=0, neutral=0)
        assert s.onex_better_count == 5
        assert s.onex_worse_count == 0

    def test_all_worse(self) -> None:
        s = _make_summary(total=5, better=0, worse=5, neutral=0)
        assert s.onex_worse_count == 5
        assert s.onex_better_count == 0

    def test_all_neutral(self) -> None:
        s = _make_summary(total=5, better=0, worse=0, neutral=5)
        assert s.neutral_count == 5

    def test_frozen(self) -> None:
        s = _make_summary()
        with pytest.raises(ValidationError):
            s.total_tasks = 99  # type: ignore[misc]


@pytest.mark.unit
class TestModelEvalReport:
    def test_create_with_5_pairs(self) -> None:
        pairs = [
            _make_pair("eval-001", EnumEvalVerdict.ONEX_BETTER, -200.0, -100.0),
            _make_pair("eval-002", EnumEvalVerdict.ONEX_BETTER, -150.0, -50.0),
            _make_pair("eval-003", EnumEvalVerdict.ONEX_BETTER, -100.0, -30.0),
            _make_pair("eval-004", EnumEvalVerdict.ONEX_WORSE, 300.0, 200.0),
            _make_pair("eval-005", EnumEvalVerdict.NEUTRAL, 10.0, 5.0),
        ]
        report = ModelEvalReport(
            report_id="report-2026-03-27",
            suite_id="standard-v1",
            suite_version="1.0.0",
            generated_at=datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC),
            pairs=pairs,
            summary=_make_summary(total=5, better=3, worse=1, neutral=1),
        )
        assert report.report_id == "report-2026-03-27"
        assert len(report.pairs) == 5
        assert report.summary.total_tasks == 5
        assert report.summary.onex_better_count == 3

    def test_json_roundtrip(self) -> None:
        pairs = [
            _make_pair("eval-001", EnumEvalVerdict.ONEX_BETTER, -200.0, -100.0),
            _make_pair("eval-002", EnumEvalVerdict.NEUTRAL, 5.0, 2.0),
        ]
        report = ModelEvalReport(
            report_id="report-test",
            suite_id="standard-v1",
            suite_version="1.0.0",
            generated_at=datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC),
            pairs=pairs,
            summary=_make_summary(total=2, better=1, worse=0, neutral=1),
        )
        json_str = report.model_dump_json()
        restored = ModelEvalReport.model_validate_json(json_str)
        assert restored == report
        assert len(restored.pairs) == 2

    def test_edge_case_empty_pairs(self) -> None:
        report = ModelEvalReport(
            report_id="report-empty",
            suite_id="standard-v1",
            suite_version="1.0.0",
            generated_at=datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC),
            pairs=[],
            summary=_make_summary(total=0, better=0, worse=0, neutral=0),
        )
        assert len(report.pairs) == 0
        assert report.summary.total_tasks == 0

    def test_frozen(self) -> None:
        report = ModelEvalReport(
            report_id="report-frozen",
            suite_id="standard-v1",
            suite_version="1.0.0",
            generated_at=datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC),
            pairs=[],
            summary=_make_summary(total=0, better=0, worse=0, neutral=0),
        )
        with pytest.raises(ValidationError):
            report.report_id = "changed"  # type: ignore[misc]
