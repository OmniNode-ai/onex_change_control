# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Script exceptions-registry entry model.

A single reviewed exception in ``allowlists/scripts_exceptions.yaml`` — the
CODEOWNERS-approved registry that is the ONLY way a NEW ``scripts/**`` file may
land under the DEFAULT-DENY policy (OMN-14475). The registry is resolved from
``onex_change_control@main`` in CI (mirroring ``skip_token_approvals.yaml``), so
a downstream PR cannot self-add an entry — an entry lands only via a separate,
CODEOWNERS-reviewed PR against this file.

``approved_by`` merely RECORDS the reviewer's GitHub login on approval; it is
advisory and NOT code-enforced here. There is no ``approved_by != author`` check
for scripts exceptions (the field defaults to blank and no validator reads it).
The only ``approved_by != author`` code check in this repo is in
``validate_prod_promotion_grants.py`` and applies solely to prod-promotion
grants.
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
            "GitHub login of the CODEOWNERS approver, recorded on approval. "
            "Advisory and NOT code-enforced for scripts exceptions: nothing "
            "compares it to the PR author, and it defaults to blank. The gate "
            "is CODEOWNERS review on the separate onex_change_control@main PR."
        ),
    )
