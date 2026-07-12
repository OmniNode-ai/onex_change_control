# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Script exceptions-registry entry model.

A single reviewed exception in ``allowlists/scripts_exceptions.yaml`` — the
CODEOWNERS-approved registry that is the ONLY way a NEW ``scripts/**`` file may
land under the DEFAULT-DENY policy (OMN-14475). The registry is resolved from
``onex_change_control@main`` in CI (mirroring ``skip_token_approvals.yaml``), so
``approved_by`` is set by a reviewer and cannot equal the PR author — no
self-written excuse.
"""

from pydantic import BaseModel, ConfigDict, Field

from onex_change_control.enums.enum_script_exception_disposition import (
    EnumScriptExceptionDisposition,
)


class ModelScriptException(BaseModel):
    """One CODEOWNERS-approved ``scripts/**`` exception entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(
        ...,
        description="Repo-relative path of the excepted script (e.g. scripts/ci/x.py).",
    )
    repo: str = Field(
        ...,
        description="Repository the path belongs to (e.g. omnibase_infra).",
    )
    disposition: EnumScriptExceptionDisposition = Field(
        ...,
        description="Why the exception is granted (node-backed / permanent / convert).",
    )
    ticket: str = Field(
        ...,
        pattern=r"^OMN-\d+$",
        description="The tracking ticket (conversion ticket, or permanent rationale).",
    )
    reason: str = Field(
        ...,
        min_length=1,
        description="Human-readable justification (which good-reason case applies).",
    )
    approved_by: str = Field(
        default="",
        description=(
            "GitHub login of the CODEOWNERS approver. Set by review; must not "
            "equal the PR author (enforced by the registry workflow, mirroring "
            "skip_token_approvals.yaml)."
        ),
    )
