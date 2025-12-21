"""Ticket Contract Model.

Pydantic schema model for ticket contracts.
"""

import re
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

from onex_change_control.enums.enum_evidence_kind import EnumEvidenceKind
from onex_change_control.enums.enum_interface_surface import EnumInterfaceSurface

# SemVer pattern for schema_version validation
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class ModelEvidenceRequirement(BaseModel):
    """Evidence requirement in ticket contract."""

    kind: EnumEvidenceKind = Field(..., description="Type of evidence")
    description: str = Field(..., description="What evidence must exist")
    command: str | None = Field(
        default=None,
        description="How to reproduce, if applicable",
    )


class ModelEmergencyBypass(BaseModel):
    """Emergency bypass configuration in ticket contract."""

    enabled: bool = Field(..., description="Whether bypass is enabled")
    justification: str = Field(
        default="",
        description="Justification for bypass (required if enabled)",
    )
    follow_up_ticket_id: str = Field(
        default="",
        description="Follow-up ticket ID (required if enabled)",
    )

    @model_validator(mode="after")
    def validate_bypass_fields(self) -> "ModelEmergencyBypass":
        """Validate bypass fields are complete if enabled."""
        if self.enabled:
            if not self.justification:
                msg = "justification is required when bypass is enabled"
                raise ValueError(msg)
            if not self.follow_up_ticket_id:
                msg = "follow_up_ticket_id is required when bypass is enabled"
                raise ValueError(msg)
        return self


class ModelTicketContract(BaseModel):
    """Ticket contract model.

    Represents machine-checkable acceptance criteria and enforcement hooks
    for a single ticket.
    """

    schema_version: Annotated[
        str,
        Field(
            ...,
            description="Schema version (SemVer format, e.g., '1.0.0')",
        ),
    ] = Field(..., description="Schema version (SemVer format)")
    ticket_id: str = Field(..., description="Ticket identifier (e.g., 'OMN-962')")
    summary: str = Field(..., description="One-line summary")
    is_seam_ticket: bool = Field(
        ...,
        description="Whether this ticket touches cross-repo interfaces",
    )
    interface_change: bool = Field(
        ...,
        description="Whether this ticket changes interface surfaces",
    )
    interfaces_touched: list[EnumInterfaceSurface] = Field(
        default_factory=list,
        description="Interface surfaces touched by this ticket",
    )
    evidence_requirements: list[ModelEvidenceRequirement] = Field(
        default_factory=list,
        description="Evidence requirements",
    )
    emergency_bypass: ModelEmergencyBypass = Field(
        ...,
        description="Emergency bypass configuration",
    )

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        """Validate schema_version is SemVer format."""
        if not _SEMVER_PATTERN.match(v):
            msg = f"Invalid schema_version format: {v}. Expected SemVer (e.g., '1.0.0')"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def validate_interface_constraints(self) -> "ModelTicketContract":
        """Validate interface change constraints."""
        if not self.interface_change and self.interfaces_touched:
            msg = (
                "interfaces_touched must be empty when interface_change is false. "
                "If no interfaces are touched, set interfaces_touched to []"
            )
            raise ValueError(msg)
        return self
