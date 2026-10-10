# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18945: enforcement claims and governed-branch readback results."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelEnforcementSurface(BaseModel):
    """An active surface and the required context that holds its producer."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    workflow_path: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    command: str = Field(min_length=1)
    required_context: str = Field(min_length=1)
    rollup_job_id: str | None = Field(default=None, min_length=1)

    @field_validator("source_path", "workflow_path")
    @classmethod
    def repository_relative_path(cls, value: str) -> str:
        """A path cannot override the API's branch query or escape its tree."""
        if (
            PurePosixPath(value).is_absolute()
            or ".." in value.split("/")
            or any(char in value for char in "?#%\\")
        ):
            message = (
                "enforcement paths must be repository-relative, without query syntax"
            )
            raise ValueError(message)
        return value


class ModelEnforcementPlacementRequest(BaseModel):
    """Resolve every supplied claim against one repository and governed branch."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    contract_name: Literal["enforcement-placement"] = "enforcement-placement"
    repository: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    governed_branch: str = Field(min_length=1)
    surfaces: tuple[ModelEnforcementSurface, ...] = Field(min_length=1)


class ModelEnforcementPlacementResult(BaseModel):
    """A non-vacuous placement verdict; transport failures never produce PASS."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_name: Literal["enforcement-placement"] = "enforcement-placement"
    repository: str
    governed_branch: str
    checked_surface_count: int = Field(ge=1)
    failures: tuple[str, ...]

    passed: bool

    @model_validator(mode="after")
    def consistent_verdict(self) -> Self:
        """A serialized verdict must agree with its findings."""
        if self.passed != (not self.failures):
            message = "placement verdict must agree with failures"
            raise ValueError(message)
        return self
