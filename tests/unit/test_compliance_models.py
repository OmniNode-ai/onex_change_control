# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for handler contract compliance models and enums."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from onex_change_control.enums.enum_compliance_verdict import EnumComplianceVerdict
from onex_change_control.enums.enum_compliance_violation import EnumComplianceViolation
from onex_change_control.enums.enum_migration_status import EnumMigrationStatus
from onex_change_control.models.model_compliance_sweep_report import (
    ModelComplianceSweepReport,
    ModelRepoComplianceBreakdown,
)
from onex_change_control.models.model_handler_compliance_result import (
    ModelHandlerComplianceResult,
)
from onex_change_control.models.model_migration_spec import (
    ModelMigrationSpec,
    ModelMigrationValidationResult,
)

# --- Enum Tests ---


class TestEnumComplianceVerdict:
    def test_all_values(self) -> None:
        assert set(EnumComplianceVerdict) == {
            EnumComplianceVerdict.COMPLIANT,
            EnumComplianceVerdict.IMPERATIVE,
            EnumComplianceVerdict.HYBRID,
            EnumComplianceVerdict.ALLOWLISTED,
            EnumComplianceVerdict.MISSING_CONTRACT,
        }

    def test_str_serialization(self) -> None:
        assert str(EnumComplianceVerdict.COMPLIANT) == "compliant"
        assert str(EnumComplianceVerdict.IMPERATIVE) == "imperative"

    def test_is_str_enum(self) -> None:
        assert isinstance(EnumComplianceVerdict.COMPLIANT, str)


class TestEnumComplianceViolation:
    def test_all_values(self) -> None:
        assert len(EnumComplianceViolation) == 10

    def test_str_serialization(self) -> None:
        assert str(EnumComplianceViolation.HARDCODED_TOPIC) == "hardcoded_topic"
        assert str(EnumComplianceViolation.DIRECT_DB_ACCESS) == "direct_db_access"

    def test_is_str_enum(self) -> None:
        assert isinstance(EnumComplianceViolation.HARDCODED_TOPIC, str)


class TestEnumMigrationStatus:
    def test_all_values(self) -> None:
        assert set(EnumMigrationStatus) == {
            EnumMigrationStatus.PENDING,
            EnumMigrationStatus.GENERATED,
            EnumMigrationStatus.VALIDATED,
            EnumMigrationStatus.DEPLOYED,
            EnumMigrationStatus.RETIRED,
        }

    def test_str_serialization(self) -> None:
        assert str(EnumMigrationStatus.PENDING) == "pending"


# --- Model Tests ---


@pytest.fixture
def compliant_result() -> ModelHandlerComplianceResult:
    return ModelHandlerComplianceResult(
        handler_path="src/omnibase_infra/nodes/node_foo/handlers/handler_bar.py",
        node_dir="src/omnibase_infra/nodes/node_foo",
        repo="omnibase_infra",
        contract_path="src/omnibase_infra/nodes/node_foo/contract.yaml",
        violations=[],
        violation_details=[],
        declared_topics=["onex.evt.foo.v1"],
        used_topics=["onex.evt.foo.v1"],
        undeclared_topics=[],
        declared_transports=["KAFKA"],
        used_transports=["KAFKA"],
        undeclared_transports=[],
        handler_in_routing=True,
        verdict=EnumComplianceVerdict.COMPLIANT,
        allowlisted=False,
    )


@pytest.fixture
def imperative_result() -> ModelHandlerComplianceResult:
    return ModelHandlerComplianceResult(
        handler_path="src/omnibase_infra/nodes/node_baz/handlers/handler_qux.py",
        node_dir="src/omnibase_infra/nodes/node_baz",
        repo="omnibase_infra",
        contract_path="src/omnibase_infra/nodes/node_baz/contract.yaml",
        violations=[
            EnumComplianceViolation.HARDCODED_TOPIC,
            EnumComplianceViolation.UNDECLARED_TRANSPORT,
        ],
        violation_details=[
            "hardcoded topic 'agent-actions' at line 47",
            "undeclared transport HTTP (httpx import at line 3)",
        ],
        declared_topics=["onex.evt.baz.v1"],
        used_topics=["onex.evt.baz.v1", "agent-actions"],
        undeclared_topics=["agent-actions"],
        declared_transports=["KAFKA"],
        used_transports=["KAFKA", "HTTP"],
        undeclared_transports=["HTTP"],
        handler_in_routing=True,
        verdict=EnumComplianceVerdict.IMPERATIVE,
        allowlisted=False,
    )


class TestModelHandlerComplianceResult:
    def test_frozen(self, compliant_result: ModelHandlerComplianceResult) -> None:
        with pytest.raises(ValidationError):
            compliant_result.repo = "changed"  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            ModelHandlerComplianceResult(
                handler_path="x",
                node_dir="x",
                repo="x",
                verdict=EnumComplianceVerdict.COMPLIANT,
                extra_field="bad",  # type: ignore[call-arg]
            )

    def test_roundtrip_json(
        self, imperative_result: ModelHandlerComplianceResult
    ) -> None:
        json_str = imperative_result.model_dump_json()
        parsed = json.loads(json_str)
        restored = ModelHandlerComplianceResult.model_validate(parsed)
        assert restored == imperative_result

    def test_compliant_has_no_violations(
        self, compliant_result: ModelHandlerComplianceResult
    ) -> None:
        assert compliant_result.violations == []
        assert compliant_result.verdict == EnumComplianceVerdict.COMPLIANT

    def test_imperative_has_violations(
        self, imperative_result: ModelHandlerComplianceResult
    ) -> None:
        assert len(imperative_result.violations) == 2
        assert imperative_result.verdict == EnumComplianceVerdict.IMPERATIVE

    def test_missing_contract(self) -> None:
        result = ModelHandlerComplianceResult(
            handler_path="x",
            node_dir="x",
            repo="x",
            contract_path=None,
            verdict=EnumComplianceVerdict.MISSING_CONTRACT,
        )
        assert result.contract_path is None


class TestModelComplianceSweepReport:
    def test_empty_report(self) -> None:
        report = ModelComplianceSweepReport(
            timestamp=datetime.now(tz=UTC),
            total_handlers=0,
            compliant_count=0,
            imperative_count=0,
            hybrid_count=0,
            allowlisted_count=0,
            missing_contract_count=0,
            compliant_pct=0.0,
        )
        assert report.total_handlers == 0
        assert report.compliant_pct == 0.0

    def test_percentage_accepts_boundary_values(self) -> None:
        report = ModelComplianceSweepReport(
            timestamp=datetime.now(tz=UTC),
            total_handlers=10,
            compliant_count=10,
            imperative_count=0,
            hybrid_count=0,
            allowlisted_count=0,
            missing_contract_count=0,
            compliant_pct=100.0,
        )
        assert report.compliant_pct == 100.0

        zero_report = ModelComplianceSweepReport(
            timestamp=datetime.now(tz=UTC),
            total_handlers=0,
            compliant_count=0,
            imperative_count=0,
            hybrid_count=0,
            allowlisted_count=0,
            missing_contract_count=0,
            compliant_pct=0.0,
        )
        assert zero_report.compliant_pct == 0.0

    def test_frozen(self) -> None:
        report = ModelComplianceSweepReport(
            timestamp=datetime.now(tz=UTC),
            total_handlers=0,
            compliant_count=0,
            imperative_count=0,
            hybrid_count=0,
            allowlisted_count=0,
            missing_contract_count=0,
            compliant_pct=0.0,
        )
        with pytest.raises(ValidationError):
            report.total_handlers = 5  # type: ignore[misc]

    def test_roundtrip_json(
        self, compliant_result: ModelHandlerComplianceResult
    ) -> None:
        breakdown = ModelRepoComplianceBreakdown(
            repo="omnibase_infra",
            total_handlers=1,
            compliant=1,
            imperative=0,
            hybrid=0,
            top_violations=[],
        )
        report = ModelComplianceSweepReport(
            timestamp=datetime(2026, 3, 27, tzinfo=UTC),
            total_handlers=1,
            compliant_count=1,
            imperative_count=0,
            hybrid_count=0,
            allowlisted_count=0,
            missing_contract_count=0,
            compliant_pct=100.0,
            violation_histogram={},
            per_repo={"omnibase_infra": breakdown},
            results=[compliant_result],
        )
        json_str = report.model_dump_json()
        parsed = json.loads(json_str)
        restored = ModelComplianceSweepReport.model_validate(parsed)
        assert restored.total_handlers == 1
        assert len(restored.results) == 1

    def test_violation_histogram(self) -> None:
        report = ModelComplianceSweepReport(
            timestamp=datetime.now(tz=UTC),
            total_handlers=5,
            compliant_count=2,
            imperative_count=3,
            hybrid_count=0,
            allowlisted_count=0,
            missing_contract_count=0,
            compliant_pct=40.0,
            violation_histogram={
                "hardcoded_topic": 3,
                "undeclared_transport": 1,
            },
        )
        assert report.violation_histogram["hardcoded_topic"] == 3


class TestModelMigrationSpec:
    def test_frozen(self) -> None:
        spec = ModelMigrationSpec(
            handler_path="x",
            node_dir="x",
            contract_path="x/contract.yaml",
            estimated_complexity=1,
        )
        with pytest.raises(ValidationError):
            spec.status = EnumMigrationStatus.GENERATED  # type: ignore[misc]

    def test_roundtrip_json(self) -> None:
        spec = ModelMigrationSpec(
            handler_path="src/omnibase_infra/nodes/node_foo/handlers/handler_bar.py",
            node_dir="src/omnibase_infra/nodes/node_foo",
            contract_path="src/omnibase_infra/nodes/node_foo/contract.yaml",
            violations=[EnumComplianceViolation.HARDCODED_TOPIC],
            contract_changes=["add 'agent-actions' to event_bus.publish_topics"],
            handler_changes=["replace hardcoded topic with contract lookup"],
            estimated_complexity=2,
            status=EnumMigrationStatus.GENERATED,
            ticket_id="OMN-1234",
        )
        json_str = spec.model_dump_json()
        parsed = json.loads(json_str)
        restored = ModelMigrationSpec.model_validate(parsed)
        assert restored == spec

    def test_complexity_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ModelMigrationSpec(
                handler_path="x",
                node_dir="x",
                contract_path="x",
                estimated_complexity=0,
            )
        with pytest.raises(ValidationError):
            ModelMigrationSpec(
                handler_path="x",
                node_dir="x",
                contract_path="x",
                estimated_complexity=6,
            )

    def test_default_status(self) -> None:
        spec = ModelMigrationSpec(
            handler_path="x",
            node_dir="x",
            contract_path="x",
            estimated_complexity=1,
        )
        assert spec.status == EnumMigrationStatus.PENDING
        assert spec.ticket_id is None


class TestModelMigrationValidationResult:
    def test_frozen(self) -> None:
        result = ModelMigrationValidationResult(
            handler_path="x",
            contract_dispatch_loads=True,
            test_inputs_count=5,
            tests_passed=5,
            tests_failed=0,
            passed=True,
        )
        with pytest.raises(ValidationError):
            result.passed = False  # type: ignore[misc]

    def test_roundtrip_json(self) -> None:
        result = ModelMigrationValidationResult(
            handler_path="src/omnibase_infra/nodes/node_foo/handlers/handler_bar.py",
            contract_dispatch_loads=True,
            test_inputs_count=3,
            tests_passed=2,
            tests_failed=1,
            failure_details=["Input 3: expected output A, got output B"],
            passed=False,
        )
        json_str = result.model_dump_json()
        parsed = json.loads(json_str)
        restored = ModelMigrationValidationResult.model_validate(parsed)
        assert restored == result

    def test_count_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError, match="test_inputs_count"):
            ModelMigrationValidationResult(
                handler_path="x",
                contract_dispatch_loads=True,
                test_inputs_count=5,
                tests_passed=3,
                tests_failed=1,
                passed=False,
            )

    def test_passed_requires_all_green(self) -> None:
        with pytest.raises(ValidationError, match="passed=True requires"):
            ModelMigrationValidationResult(
                handler_path="x",
                contract_dispatch_loads=False,
                test_inputs_count=5,
                tests_passed=5,
                tests_failed=0,
                passed=True,
            )
