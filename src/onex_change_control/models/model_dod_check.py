# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""OCC-local ticket contract supporting models. OMN-10066

ModelEmergencyBypass and ModelEvidenceRequirement are re-exported from
omnibase_core (OMN-10064) so all consumers share a single class identity.

ModelDodCheck is OCC-local (richer than core's ModelDodEvidenceCheck:
Literal check_type, str | dict check_value, optional cwd) and is
intentionally distinct. ModelDodEvidenceItem references ModelDodCheck.
"""

from __future__ import annotations

from typing import Literal

from omnibase_core.models.ticket.model_emergency_bypass import (
    ModelEmergencyBypass as ModelEmergencyBypass,  # re-export
)
from omnibase_core.models.ticket.model_evidence_requirement import (
    ModelEvidenceRequirement as ModelEvidenceRequirement,  # re-export
)
from pydantic import BaseModel, ConfigDict, Field

# Security constraints to prevent DoS attacks
_MAX_STRING_LENGTH = 10000
_MAX_LIST_ITEMS = 1000


class ModelDodCheck(BaseModel):
    """A single executable check for a DoD evidence item.

    Each check has a type that determines how check_value is interpreted:
    - test_exists: check_value is a glob pattern for test files
    - test_passes: check_value is a pytest marker or path to run
    - file_exists: check_value is a glob pattern for expected files
    - grep: check_value is a dict with 'pattern' and 'path' keys
    - command: check_value is a shell command (exit 0 = pass)
    - endpoint: check_value is a URL or path to check

    The optional ``cwd`` field declares the working directory the check should
    execute under. The runner expands ``${OMNI_HOME}``, ``${PR_NUMBER}``,
    ``${REPO}``, and ``${TICKET_ID}`` template tokens before invocation, and
    is responsible for path-traversal containment checks. When ``cwd`` is
    omitted the runner inherits its caller's working directory (legacy
    behavior).

    OMN-10078: replaces the brittle ``cd ${OMNI_HOME}/<repo> && `` shell
    prefix introduced as a temporary fix in OMN-10049 / PR #448.
    """

    model_config = ConfigDict(frozen=True)

    check_type: Literal[
        "test_exists",
        "test_passes",
        "file_exists",
        "grep",
        "command",
        "endpoint",
    ] = Field(..., description="Type of executable check")
    check_value: str | dict[str, str] = Field(
        ...,
        description="Check-type-specific value (glob, command, URL, or pattern dict)",
    )
    cwd: str | None = Field(
        default=None,
        description=(
            "Optional working directory for the check command. Supports "
            "${OMNI_HOME}, ${PR_NUMBER}, ${REPO}, ${TICKET_ID} template "
            "tokens that the runner substitutes at execution time. When "
            "omitted the runner inherits its caller's cwd."
        ),
        max_length=_MAX_STRING_LENGTH,
    )


class ModelDodEvidenceItem(BaseModel):
    """A single DoD evidence item mapping a requirement to executable checks."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(
        ...,
        description="Unique identifier within the contract (e.g., 'dod-001')",
        max_length=50,
    )
    description: str = Field(
        ...,
        description="Human-readable description of the DoD requirement",
        max_length=_MAX_STRING_LENGTH,
    )
    source: Literal["linear", "manual", "generated"] = Field(
        default="generated",
        description="Where this DoD item originated",
    )
    linear_dod_text: str | None = Field(
        default=None,
        description="Original DoD text from Linear, if sourced from Linear",
        max_length=_MAX_STRING_LENGTH,
    )
    checks: list[ModelDodCheck] = Field(
        ...,
        description="Executable checks that verify this DoD item",
        max_length=_MAX_LIST_ITEMS,
    )
    status: Literal["pending", "verified", "failed", "skipped"] = Field(
        default="pending",
        description="Current verification status of this DoD item",
    )
    evidence_artifact: str | None = Field(
        default=None,
        description="Path to evidence artifact (e.g., test output, screenshot)",
        max_length=_MAX_STRING_LENGTH,
    )


__all__ = [
    "ModelDodCheck",
    "ModelDodEvidenceItem",
    "ModelEmergencyBypass",
    "ModelEvidenceRequirement",
]
