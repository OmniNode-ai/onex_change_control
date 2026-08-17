# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Migration Spec Model.

Migration plan for a single handler's imperative-to-declarative transition.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from onex_change_control.enums.enum_compliance_violation import (  # noqa: TC001  Why: Pydantic model needs runtime type for field annotation
    EnumComplianceViolation,
)
from onex_change_control.enums.enum_migration_status import EnumMigrationStatus


class ModelMigrationSpec(BaseModel):
    """Migration plan for one handler.

    Captures the violations to fix, specific contract.yaml changes needed,
    handler code changes, and current migration status.

    Lifecycle: PENDING -> GENERATED -> VALIDATED -> DEPLOYED -> RETIRED
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    handler_path: str = Field(
        ...,
        description="Path to imperative handler",
    )
    node_dir: str = Field(
        ...,
        description="Parent node directory",
    )
    contract_path: str = Field(
        ...,
        description="Path to contract.yaml (existing or to-be-created)",
    )
    violations: list[EnumComplianceViolation] = Field(
        default_factory=list,
        description="Violations to fix",
    )
    contract_changes: list[str] = Field(
        default_factory=list,
        description="Specific contract.yaml changes needed",
    )
    handler_changes: list[str] = Field(
        default_factory=list,
        description="Specific handler changes needed",
    )
    estimated_complexity: int = Field(
        ...,
        description="1-5 scale based on violation count and type",
        ge=1,
        le=5,
    )
    status: EnumMigrationStatus = Field(
        default=EnumMigrationStatus.PENDING,
        description="Current migration status",
    )
    ticket_id: str | None = Field(
        default=None,
        description="Linear ticket ID tracking this migration",
    )


class ModelMigrationValidationResult(BaseModel):
    """Before/after validation result for a handler migration.

    Verifies that the migrated handler can be loaded via contract
    dispatch and produces equivalent outputs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    handler_path: str = Field(
        ...,
        description="Path to migrated handler",
    )
    contract_dispatch_loads: bool = Field(
        ...,
        description="Whether handler loads successfully via contract routing",
    )
    test_inputs_count: int = Field(
        ...,
        description="Number of test inputs run",
        ge=0,
    )
    tests_passed: int = Field(
        ...,
        description="Number of tests that produced equivalent output",
        ge=0,
    )
    tests_failed: int = Field(
        ...,
        description="Number of tests that produced different output",
        ge=0,
    )
    failure_details: list[str] = Field(
        default_factory=list,
        description="Details of any failed equivalence tests",
    )
    passed: bool = Field(
        ...,
        description="Overall pass/fail: all tests passed and dispatch loads",
    )

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        """Enforce test count and passed-flag invariants."""
        if self.tests_passed + self.tests_failed != self.test_inputs_count:
            msg = (
                f"tests_passed ({self.tests_passed}) "
                f"+ tests_failed ({self.tests_failed}) "
                f"!= test_inputs_count ({self.test_inputs_count})"
            )
            raise ValueError(msg)
        if self.passed and (
            not self.contract_dispatch_loads
            or self.tests_failed != 0
            or self.tests_passed != self.test_inputs_count
        ):
            msg = (
                "passed=True requires contract_dispatch_loads=True, "
                "tests_failed=0, and all tests passed"
            )
            raise ValueError(msg)
        return self
