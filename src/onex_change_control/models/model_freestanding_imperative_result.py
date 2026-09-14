# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Freestanding Imperative Result Model.

Audit result for a single freestanding Python module — source code under
``src/`` that is NOT a node handler (i.e. not under ``node_*/handlers/``).

The node/handler contract scanner cannot see these modules, yet they are the
place imperative IO debt actually accumulates (raw HTTP inference, direct DB
connections, hardcoded inference params, subprocess network ops). This model
captures the per-module verdict so the guard can govern all of ``src/``.
"""

from pydantic import BaseModel, ConfigDict, Field

from onex_change_control.enums.enum_compliance_verdict import EnumComplianceVerdict
from onex_change_control.enums.enum_compliance_violation import EnumComplianceViolation


class ModelFreestandingImperativeFinding(BaseModel):
    """A single imperative-IO finding inside a freestanding module."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    violation: EnumComplianceViolation = Field(
        ...,
        description="The kind of imperative-IO violation detected.",
    )
    line: int = Field(
        ...,
        ge=1,
        description="1-based source line of the offending node.",
    )
    detail: str = Field(
        ...,
        description="Human-readable description of the finding.",
    )
    suppressed: bool = Field(
        default=False,
        description=(
            "Whether an inline '# no-contract-check: <reason>' comment on the "
            "finding's line suppresses it."
        ),
    )


class ModelFreestandingImperativeResult(BaseModel):
    """Audit result for one freestanding (non-handler) source module."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    module_path: str = Field(
        ...,
        description="Repo-relative path to the scanned module.",
    )
    repo: str = Field(
        ...,
        description="Repository name.",
    )
    findings: list[ModelFreestandingImperativeFinding] = Field(
        default_factory=list,
        description="All imperative-IO findings in the module (suppressed included).",
    )
    verdict: EnumComplianceVerdict = Field(
        ...,
        description="Overall verdict for the module.",
    )
    allowlisted: bool = Field(
        default=False,
        description="Whether this module path is baselined in the allowlist.",
    )

    @property
    def active_findings(self) -> list[ModelFreestandingImperativeFinding]:
        """Findings that are not inline-suppressed."""
        return [f for f in self.findings if not f.suppressed]

    @property
    def violations(self) -> list[EnumComplianceViolation]:
        """Distinct active violation kinds, for parity with handler results."""
        seen: list[EnumComplianceViolation] = []
        for finding in self.active_findings:
            if finding.violation not in seen:
                seen.append(finding.violation)
        return seen
