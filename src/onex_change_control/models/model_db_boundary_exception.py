# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""DB Boundary Exception Model.

Pydantic models for DB boundary exceptions and the exception registry.
"""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from onex_change_control.enums.enum_db_boundary import (
    EnumDbBoundaryExceptionStatus,
    EnumDbBoundaryReasonCategory,
)

# YYYY-MM pattern for review_by field
_REVIEW_BY_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class ModelDbBoundaryException(BaseModel):
    """A registered DB boundary exception.

    Documents an approved deviation from the one-service-one-database principle.
    Each exception must have a review date and explicit justification.

    Immutability:
        This model is frozen (immutable) after creation to ensure:
        1. Exception records cannot be modified after approval
        2. Thread-safe access from multiple readers
        3. Safe use as dictionary keys or cache entries
    """

    model_config = ConfigDict(frozen=True)

    repo: str = Field(
        ...,
        description="Repository containing the exception",
    )
    file: str = Field(
        ...,
        description="Exact file path within the repository",
    )
    usage: str = Field(
        ...,
        description="Brief description of the cross-boundary access",
    )
    reason_category: EnumDbBoundaryReasonCategory = Field(
        ...,
        description="Category of the exception reason",
    )
    justification: str = Field(
        ...,
        description="Explicit rationale for the exception",
    )
    owner: str = Field(
        ...,
        description="Person or team responsible for the exception",
    )
    approved_by: str = Field(
        ...,
        description="Person who approved the exception",
    )
    review_by: str = Field(
        ...,
        description="YYYY-MM date when the exception must be re-evaluated",
    )
    status: EnumDbBoundaryExceptionStatus = Field(
        default=EnumDbBoundaryExceptionStatus.APPROVED,
        description="Current status of the exception",
    )

    @field_validator("review_by")
    @classmethod
    def validate_review_by(cls, v: str) -> str:
        """Validate review_by is YYYY-MM format with valid month (01-12)."""
        if not _REVIEW_BY_PATTERN.match(v):
            msg = (
                f"Invalid review_by format: {v}. "
                "Expected YYYY-MM with valid month (01-12)"
            )
            raise ValueError(msg)
        return v


class ModelDbBoundaryExceptionsRegistry(BaseModel):
    """Registry of all DB boundary exceptions.

    Loaded from registry/db-boundary-exceptions.yaml and validated
    by the check-db-boundary CLI tool.
    """

    model_config = ConfigDict(frozen=True)

    exceptions: list[ModelDbBoundaryException] = Field(
        default_factory=list,
        description="List of registered DB boundary exceptions",
    )
