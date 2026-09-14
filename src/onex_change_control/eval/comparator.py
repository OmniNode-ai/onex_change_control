# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Eval Comparator.

Pairs ONEX ON and ONEX OFF runs by task_id, computes delta metrics,
determines verdicts, and generates an eval report with summary statistics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from onex_change_control.enums.enum_eval_metric_type import EnumEvalMetricType
from onex_change_control.enums.enum_eval_mode import EnumEvalMode
from onex_change_control.enums.enum_eval_verdict import EnumEvalVerdict
from onex_change_control.models.model_eval_report import (
    ModelEvalReport,
    ModelEvalSummary,
)
from onex_change_control.models.model_eval_run import (
    ModelEvalRun,
    ModelEvalRunPair,
)

# Threshold constants for verdict determination
_LATENCY_THRESHOLD_PERCENT = 0.10  # >10% reduction = better
_TOKEN_THRESHOLD_PERCENT = 0.05  # >5% reduction = better


def _compute_delta_metrics(
    on_run: ModelEvalRun,
    off_run: ModelEvalRun,
) -> dict[str, float]:
    """Compute per-metric delta (on - off).

    For cost metrics: negative delta = ONEX saves.
    For benefit metrics: positive delta = ONEX helps.
    """
    deltas: dict[str, float] = {}
    for metric_type in EnumEvalMetricType:
        on_val = on_run.get_metric(metric_type)
        off_val = off_run.get_metric(metric_type)
        if on_val is not None and off_val is not None:
            deltas[str(metric_type)] = on_val - off_val
    return deltas


def _check_success_rate(
    on_run: ModelEvalRun,
    off_run: ModelEvalRun,
) -> EnumEvalVerdict | None:
    """Check success rate: any change produces a verdict."""
    on_val = on_run.get_metric(EnumEvalMetricType.SUCCESS_RATE)
    off_val = off_run.get_metric(EnumEvalMetricType.SUCCESS_RATE)
    if on_val is not None and off_val is not None:
        if on_val > off_val:
            return EnumEvalVerdict.ONEX_BETTER
        if on_val < off_val:
            return EnumEvalVerdict.ONEX_WORSE
    return None


def _check_ratio_threshold(
    deltas: dict[str, float],
    off_run: ModelEvalRun,
    metric: EnumEvalMetricType,
    threshold: float,
) -> EnumEvalVerdict | None:
    """Check if delta/baseline ratio exceeds threshold."""
    key = str(metric)
    if key not in deltas:
        return None
    baseline = off_run.get_metric(metric)
    if not baseline or baseline <= 0:
        return None
    ratio = deltas[key] / baseline
    if ratio < -threshold:
        return EnumEvalVerdict.ONEX_BETTER
    if ratio > threshold:
        return EnumEvalVerdict.ONEX_WORSE
    return None


def _determine_verdict(
    on_run: ModelEvalRun,
    off_run: ModelEvalRun,
    deltas: dict[str, float],
) -> EnumEvalVerdict:
    """Determine verdict based on threshold rules.

    Priority: success rate > latency (10%) > token count (5%).
    """
    return (
        _check_success_rate(on_run, off_run)
        or _check_ratio_threshold(
            deltas, off_run, EnumEvalMetricType.LATENCY_MS, _LATENCY_THRESHOLD_PERCENT
        )
        or _check_ratio_threshold(
            deltas, off_run, EnumEvalMetricType.TOKEN_COUNT, _TOKEN_THRESHOLD_PERCENT
        )
        or EnumEvalVerdict.NEUTRAL
    )


def _pair_runs(
    on_runs: list[ModelEvalRun],
    off_runs: list[ModelEvalRun],
) -> tuple[list[ModelEvalRunPair], list[str]]:
    """Pair runs by task_id. Returns (pairs, incomplete_task_ids)."""
    on_by_task: dict[str, ModelEvalRun] = {}
    for run in on_runs:
        if run.mode == EnumEvalMode.ONEX_ON:
            on_by_task[run.task_id] = run

    off_by_task: dict[str, ModelEvalRun] = {}
    for run in off_runs:
        if run.mode == EnumEvalMode.ONEX_OFF:
            off_by_task[run.task_id] = run

    all_task_ids = sorted(set(on_by_task) | set(off_by_task))
    pairs: list[ModelEvalRunPair] = []
    incomplete: list[str] = []

    for task_id in all_task_ids:
        on_run = on_by_task.get(task_id)
        off_run = off_by_task.get(task_id)

        if on_run is None or off_run is None:
            incomplete.append(task_id)
            continue

        deltas = _compute_delta_metrics(on_run, off_run)
        verdict = _determine_verdict(on_run, off_run, deltas)

        pairs.append(
            ModelEvalRunPair(
                task_id=task_id,
                onex_on_run=on_run,
                onex_off_run=off_run,
                delta_metrics=deltas,
                verdict=verdict,
            )
        )

    return pairs, incomplete


def _compute_summary(pairs: list[ModelEvalRunPair]) -> ModelEvalSummary:
    """Compute summary statistics from paired results."""
    if not pairs:
        return ModelEvalSummary(
            total_tasks=0,
            onex_better_count=0,
            onex_worse_count=0,
            neutral_count=0,
            avg_latency_delta_ms=0.0,
            avg_token_delta=0.0,
            avg_success_rate_on=0.0,
            avg_success_rate_off=0.0,
            pattern_hit_rate_on=0.0,
        )

    better = sum(1 for p in pairs if p.verdict == EnumEvalVerdict.ONEX_BETTER)
    worse = sum(1 for p in pairs if p.verdict == EnumEvalVerdict.ONEX_WORSE)
    neutral = sum(
        1
        for p in pairs
        if p.verdict not in (EnumEvalVerdict.ONEX_BETTER, EnumEvalVerdict.ONEX_WORSE)
    )

    latency_key = str(EnumEvalMetricType.LATENCY_MS)
    token_key = str(EnumEvalMetricType.TOKEN_COUNT)

    latency_deltas = [
        p.delta_metrics[latency_key] for p in pairs if latency_key in p.delta_metrics
    ]
    token_deltas = [
        p.delta_metrics[token_key] for p in pairs if token_key in p.delta_metrics
    ]

    success_on = [
        v
        for p in pairs
        if (v := p.onex_on_run.get_metric(EnumEvalMetricType.SUCCESS_RATE)) is not None
    ]
    success_off = [
        v
        for p in pairs
        if (v := p.onex_off_run.get_metric(EnumEvalMetricType.SUCCESS_RATE)) is not None
    ]
    pattern_on = [
        v
        for p in pairs
        if (v := p.onex_on_run.get_metric(EnumEvalMetricType.PATTERN_HIT_RATE))
        is not None
    ]

    n = len(pairs)
    return ModelEvalSummary(
        total_tasks=n,
        onex_better_count=better,
        onex_worse_count=worse,
        neutral_count=neutral,
        avg_latency_delta_ms=(
            sum(latency_deltas) / len(latency_deltas) if latency_deltas else 0.0
        ),
        avg_token_delta=(
            sum(token_deltas) / len(token_deltas) if token_deltas else 0.0
        ),
        avg_success_rate_on=(sum(success_on) / len(success_on) if success_on else 0.0),
        avg_success_rate_off=(
            sum(success_off) / len(success_off) if success_off else 0.0
        ),
        pattern_hit_rate_on=(sum(pattern_on) / len(pattern_on) if pattern_on else 0.0),
    )


def compute_eval_report(
    on_runs: list[ModelEvalRun],
    off_runs: list[ModelEvalRun],
    suite_id: str,
    suite_version: str = "",
) -> ModelEvalReport:
    """Build a complete eval report from ON and OFF run lists.

    Pairs runs by task_id, computes deltas, determines verdicts,
    and generates summary statistics.
    """
    pairs, _incomplete = _pair_runs(on_runs, off_runs)
    summary = _compute_summary(pairs)

    return ModelEvalReport(
        report_id=f"eval-report-{uuid4().hex[:12]}",
        suite_id=suite_id,
        suite_version=suite_version,
        generated_at=datetime.now(UTC),
        pairs=pairs,
        summary=summary,
    )
