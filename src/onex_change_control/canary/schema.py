# SPDX-License-Identifier: MIT
"""Pydantic schema for canary tier assignments."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator


class ModelCanaryTier(BaseModel):
    name: Literal["canary", "early_adopter", "ga"]
    repos: list[str]
    description: str

    @field_validator("repos")
    @classmethod
    def repos_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            msg = "repos list must not be empty"
            raise ValueError(msg)
        return v


class ModelCanaryTierAssignments(BaseModel):
    version: Literal["1.0"]
    tiers: list[ModelCanaryTier]
