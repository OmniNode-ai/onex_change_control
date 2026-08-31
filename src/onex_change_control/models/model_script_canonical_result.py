# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Script canonical-form result model.

Audit result for a single ``scripts/**`` file under the DEFAULT-DENY policy
(OMN-14475). The imperative-contract scanner only sees ``src/``; logic-bearing
scripts accumulate outside it. This model captures the per-script verdict so
the guard can block new non-canonical scripts while ratcheting existing ones.
"""

from pydantic import BaseModel, ConfigDict, Field

from onex_change_control.enums.enum_script_canonical_verdict import (
    EnumScriptCanonicalVerdict,
)
from onex_change_control.enums.enum_script_exception_disposition import (
    EnumScriptExceptionDisposition,
)
from onex_change_control.enums.enum_script_file_kind import EnumScriptFileKind


class ModelScriptCanonicalResult(BaseModel):
    """Audit result for one governed ``scripts/**`` file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    script_path: str = Field(
        ...,
        description="Repo-relative path to the scanned script.",
    )
    repo: str = Field(
        ...,
        description="Repository name.",
    )
    file_kind: EnumScriptFileKind = Field(
        ...,
        description="How the script was analysed (python AST vs shell text).",
    )
    verdict: EnumScriptCanonicalVerdict = Field(
        ...,
        description="Overall canonical-form verdict for the script.",
    )
    logic_score: int = Field(
        default=0,
        ge=0,
        description=(
            "AST logic score (0 for shell). Advisory for burn-down ranking and "
            "the permanent-shim loud advisory; never decides pass/fail on its own."
        ),
    )
    has_dispatch: bool = Field(
        default=False,
        description=(
            "Whether a dispatch into the ONEX node/handler/runtime substrate "
            "was detected (corroborates a node-backed exception)."
        ),
    )
    is_new: bool = Field(
        default=False,
        description="Whether the path is NOT in the frozen baseline allowlist.",
    )
    disposition: EnumScriptExceptionDisposition | None = Field(
        default=None,
        description="The exceptions-registry disposition, if the path has an entry.",
    )
    logic_advisory: bool = Field(
        default=False,
        description=(
            "LOUD advisory (not a block): a permanent-disposition script whose "
            "logic score is at/above the ceiling — the reviewer should confirm "
            "it is genuinely glue, not logic that belongs in a node."
        ),
    )
    detail: str = Field(
        default="",
        description="Human-readable explanation of the verdict.",
    )

    @property
    def blocking(self) -> bool:
        """Whether this result blocks the guard (exit non-zero)."""
        return self.verdict.is_blocking
