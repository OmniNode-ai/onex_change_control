"""Ticket Contract Model.

Pydantic schema model for ticket contracts.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from onex_change_control.enums.enum_evidence_kind import EnumEvidenceKind
from onex_change_control.enums.enum_interface_surface import EnumInterfaceSurface
from onex_change_control.validation.patterns import SEMVER_PATTERN

# Security constraints to prevent DoS attacks
_MAX_STRING_LENGTH = 10000  # Max length for string fields
_MAX_LIST_ITEMS = 1000  # Max items in lists


class ModelEvidenceRequirement(BaseModel):
    """Evidence requirement in ticket contract."""

    model_config = ConfigDict(frozen=True)

    kind: EnumEvidenceKind = Field(..., description="Type of evidence")
    description: str = Field(
        ..., description="What evidence must exist", max_length=_MAX_STRING_LENGTH
    )
    command: str | None = Field(
        default=None,
        description="How to reproduce, if applicable",
        max_length=_MAX_STRING_LENGTH,
    )


class ModelEmergencyBypass(BaseModel):
    """Emergency bypass configuration in ticket contract."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(..., description="Whether bypass is enabled")
    justification: str = Field(
        default="",
        description="Justification for bypass (required if enabled)",
        max_length=_MAX_STRING_LENGTH,
    )
    follow_up_ticket_id: str = Field(
        default="",
        description="Follow-up ticket ID (required if enabled)",
        max_length=50,  # Ticket IDs are typically short (e.g., "OMN-962")
    )

    @model_validator(mode="after")
    def validate_bypass_fields(self) -> "ModelEmergencyBypass":
        """Validate bypass fields are complete if enabled.

        Rejects whitespace-only strings for justification and follow_up_ticket_id
        when bypass is enabled, as these are not meaningful values.
        """
        if self.enabled:
            if not self.justification.strip():
                msg = "justification is required when bypass is enabled"
                raise ValueError(msg)
            if not self.follow_up_ticket_id.strip():
                msg = "follow_up_ticket_id is required when bypass is enabled"
                raise ValueError(msg)
        return self


class ModelTicketContract(BaseModel):
    """Ticket contract model.

    Represents machine-checkable acceptance criteria and enforcement hooks
    for a single ticket.

    Schema Version:
        The schema_version field uses basic SemVer format (major.minor.patch) only.
        Pre-release versions (e.g., "1.0.0-alpha") and build metadata
        (e.g., "1.0.0+build") are not supported. Leading zeros are rejected
        per SemVer specification.

    Immutability:
        This model is frozen (immutable) after creation to ensure:
        1. Ticket contracts cannot be modified after creation
        2. Thread-safe access from multiple readers
        3. Safe use as dictionary keys or cache entries

    Interface Change Validation:
        - If interface_change=False, interfaces_touched must be empty
        - If interface_change=True, interfaces_touched may be empty temporarily
          (e.g., during initial contract creation before categorization is complete)
          but should be populated before the ticket is considered complete.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(
        ..., description="Schema version (SemVer format, e.g., '1.0.0')", max_length=20
    )
    ticket_id: str = Field(
        ..., description="Ticket identifier (e.g., 'OMN-962')", max_length=50
    )
    summary: str = Field(
        ..., description="One-line summary", max_length=_MAX_STRING_LENGTH
    )
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
        max_length=_MAX_LIST_ITEMS,
    )
    evidence_requirements: list[ModelEvidenceRequirement] = Field(
        default_factory=list,
        description="Evidence requirements",
        max_length=_MAX_LIST_ITEMS,
    )
    emergency_bypass: ModelEmergencyBypass = Field(
        ...,
        description="Emergency bypass configuration",
    )

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        """Validate schema_version is SemVer format.

        Note: Only basic SemVer (major.minor.patch) is supported.
        Pre-release versions and build metadata are not supported.
        Leading zeros are rejected per SemVer specification.
        """
        if not SEMVER_PATTERN.match(v):
            msg = f"Invalid schema_version format: {v}. Expected SemVer (e.g., '1.0.0')"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def validate_interface_constraints(self) -> "ModelTicketContract":
        """Validate interface change constraints.

        Enforces:
        - If interface_change is False, interfaces_touched must be empty
        - If interface_change is True, interfaces_touched may be empty temporarily
          (e.g., during initial contract creation before categorization is complete)
          but should be populated before the ticket is considered complete.

        Steady State vs Temporary:
        - Steady state: interface_change=True should have non-empty interfaces_touched
        - Temporary: Empty interfaces_touched is allowed during contract creation
          but should be treated as incomplete until populated
        """
        if not self.interface_change and self.interfaces_touched:
            msg = (
                "interfaces_touched must be empty when interface_change is false. "
                "If no interfaces are touched, set interfaces_touched to []"
            )
            raise ValueError(msg)
        # Note: We allow interface_change=True with empty interfaces_touched
        # to support cases where interfaces are changed but categorization is pending.
        # This is a temporary state and should be resolved before ticket completion.
        return self

    @property
    def is_complete(self) -> bool:
        """Check if the ticket contract is in a complete state.

        A contract is considered complete when:
        - If interface_change=True, interfaces_touched must be non-empty
        - Emergency bypass is either disabled or properly configured (if enabled)

        Returns:
            True if the contract is in a steady, complete state; False otherwise.

        """
        # Check interface change completeness
        # Contract is complete if:
        # - interface_change is False (no interfaces to categorize), OR
        # - interface_change is True AND interfaces_touched is non-empty
        # Emergency bypass completeness is already validated by ModelEmergencyBypass.
        # If enabled, it must have justification and follow_up_ticket_id (enforced
        # by validator). So if we get here, bypass is either disabled or properly
        # configured.
        return not self.interface_change or bool(self.interfaces_touched)
