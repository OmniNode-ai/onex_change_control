# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for ModelEvalTask and ModelEvalSuite."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from onex_change_control.models.model_eval_task import ModelEvalSuite, ModelEvalTask


def _make_task(task_id: str = "eval-001-fix-import", **kwargs: object) -> ModelEvalTask:
    defaults: dict[str, object] = {
        "task_id": task_id,
        "name": "Fix import error",
        "category": "bug-fix",
        "prompt": "Fix the circular import in module foo",
        "repo": "omnibase_infra",
        "setup_commands": [
            "git checkout -- src/foo.py",
            "echo 'import bar' >> src/foo.py",
        ],
        "expected_files_changed": ["src/foo.py"],
        "success_criteria": ["uv run pytest tests/unit/test_foo.py"],
        "max_duration_seconds": 300,
        "tags": ["import", "bug-fix"],
    }
    defaults.update(kwargs)
    return ModelEvalTask(**defaults)


SAMPLE_TASKS = [
    _make_task("eval-001-fix-import", category="bug-fix"),
    _make_task("eval-002-fix-type-error", name="Fix type error", category="bug-fix"),
    _make_task("eval-003-extract-method", name="Extract method", category="refactor"),
]


@pytest.mark.unit
class TestModelEvalTask:
    def test_create_valid(self) -> None:
        task = _make_task()
        assert task.task_id == "eval-001-fix-import"
        assert task.category == "bug-fix"
        assert task.repeat_count == 1

    def test_frozen(self) -> None:
        task = _make_task()
        with pytest.raises(ValidationError):
            task.name = "changed"  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            _make_task(unknown_field="bad")

    def test_max_duration_bounds(self) -> None:
        task = _make_task(max_duration_seconds=7200)
        assert task.max_duration_seconds == 7200
        with pytest.raises(ValidationError):
            _make_task(max_duration_seconds=0)

    def test_repeat_count_default(self) -> None:
        task = _make_task()
        assert task.repeat_count == 1

    def test_repeat_count_custom(self) -> None:
        task = _make_task(repeat_count=5)
        assert task.repeat_count == 5

    def test_serialization_roundtrip(self) -> None:
        task = _make_task()
        data = task.model_dump()
        restored = ModelEvalTask(**data)
        assert restored == task

    def test_json_roundtrip(self) -> None:
        task = _make_task()
        json_str = task.model_dump_json()
        restored = ModelEvalTask.model_validate_json(json_str)
        assert restored == task


@pytest.mark.unit
class TestModelEvalSuite:
    def test_create_valid(self) -> None:
        suite = ModelEvalSuite(
            suite_id="standard-v1",
            name="Standard Eval Suite v1",
            description="10 tasks for A/B comparison",
            tasks=SAMPLE_TASKS,
            created_at=datetime(2026, 3, 27, tzinfo=UTC),
            version="1.0.0",
        )
        assert suite.suite_id == "standard-v1"
        assert len(suite.tasks) == 3
        assert suite.version == "1.0.0"

    def test_frozen(self) -> None:
        suite = ModelEvalSuite(
            suite_id="standard-v1",
            name="Standard Eval Suite v1",
            tasks=SAMPLE_TASKS,
            created_at=datetime(2026, 3, 27, tzinfo=UTC),
            version="1.0.0",
        )
        with pytest.raises(ValidationError):
            suite.name = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        suite = ModelEvalSuite(
            suite_id="standard-v1",
            name="Standard Eval Suite v1",
            tasks=SAMPLE_TASKS,
            created_at=datetime(2026, 3, 27, tzinfo=UTC),
            version="1.0.0",
        )
        data = suite.model_dump()
        restored = ModelEvalSuite(**data)
        assert restored == suite
        assert len(restored.tasks) == 3
