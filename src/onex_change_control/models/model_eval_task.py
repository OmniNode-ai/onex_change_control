# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Eval Task and Suite Models.

Defines the structure of eval tasks and suites for the A/B eval framework.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

_MAX_STRING_LENGTH = 10000
_MAX_LIST_ITEMS = 500


class ModelEvalTask(BaseModel):
    """A single eval task definition.

    Each task describes a repeatable unit of work that can be executed
    by an agent with ONEX features on or off.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(
        ...,
        description="Unique identifier (e.g., eval-001-fix-import-error)",
        max_length=200,
    )
    name: str = Field(
        ...,
        description="Human-readable task name",
        max_length=500,
    )
    category: str = Field(
        ...,
        description="Classification (e.g., bug-fix, refactor, feature, review)",
        max_length=100,
    )
    prompt: str = Field(
        ...,
        description="The task prompt given to the agent",
        max_length=_MAX_STRING_LENGTH,
    )
    repo: str = Field(
        ...,
        description="Target repository for the task",
        max_length=200,
    )
    setup_commands: list[str] = Field(
        default_factory=list,
        description="Commands to prepare the environment before each run",
        max_length=_MAX_LIST_ITEMS,
    )
    expected_files_changed: list[str] = Field(
        default_factory=list,
        description="Files expected to be modified (for success scoring)",
        max_length=_MAX_LIST_ITEMS,
    )
    success_criteria: list[str] = Field(
        default_factory=list,
        description="Machine-checkable criteria (e.g., 'tests pass', 'no lint errors')",
        max_length=_MAX_LIST_ITEMS,
    )
    max_duration_seconds: int = Field(
        default=600,
        description="Timeout for a single run",
        ge=1,
        le=7200,
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags for filtering/grouping",
        max_length=_MAX_LIST_ITEMS,
    )
    repeat_count: int = Field(
        default=1,
        description="Number of times to run the task (median metrics reported when >1)",
        ge=1,
        le=100,
    )


class ModelEvalSuite(BaseModel):
    """A collection of eval tasks with metadata.

    Suites group related tasks and are versioned for reproducibility.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_id: str = Field(
        ...,
        description="Unique suite identifier",
        max_length=200,
    )
    name: str = Field(
        ...,
        description="Human-readable suite name",
        max_length=500,
    )
    description: str = Field(
        default="",
        description="Suite description",
        max_length=_MAX_STRING_LENGTH,
    )
    tasks: list[ModelEvalTask] = Field(
        ...,
        description="Tasks in this suite",
        max_length=_MAX_LIST_ITEMS,
    )
    created_at: datetime = Field(
        ...,
        description="When the suite was created",
    )
    version: str = Field(
        ...,
        description="Suite version (semver)",
        max_length=50,
    )
