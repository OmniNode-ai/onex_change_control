# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_omnidash_health comparison and formatting logic.

These are pure unit tests — no database connection required.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.scripts.check_omnidash_health import (
    Finding,
    _format_json_report,
    _format_text_report,
    _load_baseline,
    _save_baseline,
    compare,
)

# ── compare() ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCompare:
    """Tests for the compare() function."""

    def test_empty_baseline_no_findings(self) -> None:
        result = compare({}, {"events": 100, "users": 50})
        assert result == []

    def test_no_changes_no_findings(self) -> None:
        baseline = {"events": 100, "users": 50}
        current = {"events": 100, "users": 50}
        result = compare(baseline, current)
        assert result == []

    def test_regression_detected_when_drops_to_zero(self) -> None:
        baseline = {"events": 100, "users": 50}
        current = {"events": 0, "users": 50}
        result = compare(baseline, current)
        assert len(result) == 1
        assert result[0].table == "events"
        assert result[0].level == "REGRESSION"
        assert result[0].baseline == 100
        assert result[0].current == 0

    def test_table_missing_from_current_is_regression(self) -> None:
        baseline = {"events": 100}
        current: dict[str, int] = {}
        result = compare(baseline, current)
        assert len(result) == 1
        assert result[0].level == "REGRESSION"

    def test_warning_on_50_percent_drop(self) -> None:
        baseline = {"events": 100}
        current = {"events": 49}
        result = compare(baseline, current)
        assert len(result) == 1
        assert result[0].level == "WARNING"

    def test_no_warning_at_exactly_50_percent(self) -> None:
        baseline = {"events": 100}
        current = {"events": 50}
        result = compare(baseline, current)
        assert result == []

    def test_growth_no_findings(self) -> None:
        baseline = {"events": 100}
        current = {"events": 200}
        result = compare(baseline, current)
        assert result == []

    def test_baseline_zero_row_table_ignored(self) -> None:
        baseline = {"empty_table": 0}
        current = {"empty_table": 0}
        result = compare(baseline, current)
        assert result == []

    def test_mixed_findings(self) -> None:
        baseline = {"a": 100, "b": 200, "c": 10, "d": 50}
        current = {"a": 0, "b": 80, "c": 10, "d": 50}
        result = compare(baseline, current)
        assert len(result) == 2
        levels = {f.table: f.level for f in result}
        assert levels["a"] == "REGRESSION"
        assert levels["b"] == "WARNING"


# ── Baseline I/O ─────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestBaseline:
    """Tests for baseline load/save."""

    def test_load_missing_file(self, tmp_path: Path) -> None:
        result = _load_baseline(tmp_path / "nonexistent.json")
        assert result == {}

    def test_load_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        result = _load_baseline(bad)
        assert result == {}

    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        data = {"events": 100, "users": 50}
        _save_baseline(path, data)
        loaded = _load_baseline(path)
        assert loaded == data

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / "baseline.json"
        _save_baseline(path, {"x": 1})
        assert path.exists()


# ── Formatting ───────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFormatting:
    """Tests for report formatting."""

    def test_text_healthy(self) -> None:
        report = _format_text_report([], {"events": 100})
        assert "HEALTHY" in report
        assert "Tables scanned: 1" in report

    def test_text_regression(self) -> None:
        findings = [Finding("events", "REGRESSION", 100, 0)]
        report = _format_text_report(findings, {"events": 0})
        assert "[REGRESSION]" in report
        assert "FAIL" in report

    def test_text_warning(self) -> None:
        findings = [Finding("events", "WARNING", 100, 40)]
        report = _format_text_report(findings, {"events": 40})
        assert "[WARNING]" in report
        assert "PASS (with warnings)" in report

    def test_json_output_structure(self) -> None:
        findings = [
            Finding("a", "REGRESSION", 100, 0),
            Finding("b", "WARNING", 200, 80),
        ]
        current = {"a": 0, "b": 80, "c": 50}
        raw = _format_json_report(findings, current)
        data = json.loads(raw)
        assert data["tables_scanned"] == 3
        assert len(data["regressions"]) == 1
        assert len(data["warnings"]) == 1
        assert data["current_counts"]["c"] == 50


# ── Finding.to_dict ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFinding:
    """Tests for Finding dataclass."""

    def test_to_dict(self) -> None:
        f = Finding("events", "REGRESSION", 100, 0)
        d = f.to_dict()
        assert d == {
            "table": "events",
            "level": "REGRESSION",
            "baseline": 100,
            "current": 0,
        }
