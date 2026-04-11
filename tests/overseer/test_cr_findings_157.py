# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""TDD-first regression tests for OMN-8431 — CodeRabbit PR #157 findings.

CR#1 (Critical): model_task_delta_envelope — Mapping/EnumTaskStatus in TYPE_CHECKING
CR#2 (Critical): model_verifier_output — EnumFailureClass in TYPE_CHECKING
CR#3 (Major): model_overnight_contract — default halt threshold hardcoded to 5.0
CR#4 (Major): model_session_contract — phases defaults to empty tuple
CR#5 (Major): model_task_state_envelope — task_id auto-generated
CR#6 (Minor): overseer/__init__.py — ModelContextBundle not exported
CR#7 (Minor): model_worker_contract — load_worker_contract rejects Mapping types
CR#8 (Major): model_overnight_contract — HaltCondition missing conditional validators
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


class TestCRFinding1CriticalTaskDeltaEnvelopePydanticRuntime:
    """CR#1 (Critical): Mapping and EnumTaskStatus in TYPE_CHECKING block.

    With `from __future__ import annotations`, Pydantic calls get_type_hints()
    at runtime. TYPE_CHECKING imports are absent, so EnumTaskStatus and Mapping
    cause unresolved annotation errors.
    """

    def test_model_task_delta_envelope_schema_builds(self) -> None:
        from onex_change_control.overseer.model_task_delta_envelope import (
            ModelTaskDeltaEnvelope,
        )

        schema = ModelTaskDeltaEnvelope.model_json_schema()
        assert "task_id" in schema.get("properties", {})

    def test_model_task_delta_envelope_instantiate_with_status(self) -> None:
        from onex_change_control.overseer.model_task_delta_envelope import (
            ModelTaskDeltaEnvelope,
        )
        from onex_change_control.overseer.model_task_state_envelope import (
            EnumTaskStatus,
        )

        delta = ModelTaskDeltaEnvelope(
            task_id="abc-123",
            status=EnumTaskStatus.RUNNING,
        )
        assert delta.task_id == "abc-123"
        assert delta.status == EnumTaskStatus.RUNNING

    def test_model_task_delta_envelope_instantiate_with_mapping_payload(self) -> None:
        from onex_change_control.overseer.model_task_delta_envelope import (
            ModelTaskDeltaEnvelope,
        )

        delta = ModelTaskDeltaEnvelope(
            task_id="abc-456",
            payload={"key": "value"},
        )
        assert delta.task_id == "abc-456"
        assert delta.payload is not None


class TestCRFinding2CriticalVerifierOutputPydanticRuntime:
    """CR#2 (Critical): EnumFailureClass in TYPE_CHECKING in model_verifier_output.

    Both ModelVerifierCheckResult.failure_class and ModelVerifierOutput.failure_class
    use this type. Pydantic cannot resolve it at runtime.
    """

    def test_model_verifier_check_result_schema_builds(self) -> None:
        from onex_change_control.overseer.model_verifier_output import (
            ModelVerifierCheckResult,
        )

        schema = ModelVerifierCheckResult.model_json_schema()
        assert "failure_class" in schema.get("properties", {})

    def test_model_verifier_output_schema_builds(self) -> None:
        from onex_change_control.overseer.model_verifier_output import (
            ModelVerifierOutput,
        )

        schema = ModelVerifierOutput.model_json_schema()
        assert "failure_class" in schema.get("properties", {})

    def test_model_verifier_check_result_instantiate_with_failure_class(self) -> None:
        from onex_change_control.overseer.enum_failure_class import EnumFailureClass
        from onex_change_control.overseer.model_verifier_output import (
            ModelVerifierCheckResult,
        )

        result = ModelVerifierCheckResult(
            name="test_check",
            passed=False,
            failure_class=EnumFailureClass.PERMANENT,
        )
        assert result.failure_class == EnumFailureClass.PERMANENT

    def test_model_verifier_output_instantiate_with_failure_class(self) -> None:
        from onex_change_control.overseer.enum_failure_class import EnumFailureClass
        from onex_change_control.overseer.enum_verifier_verdict import (
            EnumVerifierVerdict,
        )
        from onex_change_control.overseer.model_verifier_output import (
            ModelVerifierOutput,
        )

        output = ModelVerifierOutput(
            verdict=EnumVerifierVerdict.FAIL,
            failure_class=EnumFailureClass.PERMANENT,
        )
        assert output.failure_class == EnumFailureClass.PERMANENT


class TestCRFinding3MajorOvernightContractCostThreshold:
    """CR#3 (Major): Default cost halt condition hardcoded to 5.0.

    ModelOvernightContract(max_cost_usd=10.0) should derive halt threshold from
    max_cost_usd, not hardcode 5.0.
    """

    def test_default_halt_conditions_use_max_cost_usd(self) -> None:
        from onex_change_control.overseer.model_overnight_contract import (
            ModelOvernightContract,
            ModelOvernightPhaseSpec,
        )

        contract = ModelOvernightContract(
            session_id="test-session",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            max_cost_usd=10.0,
            phases=(ModelOvernightPhaseSpec(phase_name="phase1"),),
        )
        cost_conditions = [
            c for c in contract.halt_conditions if c.check_type == "cost_ceiling"
        ]
        assert len(cost_conditions) == 1
        threshold = cost_conditions[0].threshold
        assert threshold == 10.0, (
            f"Expected threshold=10.0 (from max_cost_usd) but got {threshold}"
        )

    def test_default_halt_conditions_still_have_phase_failure_limit(self) -> None:
        from onex_change_control.overseer.model_overnight_contract import (
            ModelOvernightContract,
            ModelOvernightPhaseSpec,
        )

        contract = ModelOvernightContract(
            session_id="test-session",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            max_cost_usd=7.5,
            phases=(ModelOvernightPhaseSpec(phase_name="phase1"),),
        )
        failure_conditions = [
            c for c in contract.halt_conditions if c.check_type == "phase_failure_count"
        ]
        assert len(failure_conditions) == 1
        assert failure_conditions[0].threshold == 3.0


class TestCRFinding4MajorSessionContractPhasesRequired:
    """CR#4 (Major): phases field defaults to empty tuple in ModelSessionContract.

    The comment says "No default — phases must be supplied explicitly" but
    the implementation uses default_factory=tuple, allowing empty contracts.
    """

    def test_session_contract_requires_phases(self) -> None:
        from pydantic import ValidationError

        from onex_change_control.overseer.model_session_contract import (
            ModelSessionContract,
        )

        with pytest.raises(ValidationError):
            ModelSessionContract(
                session_id="test",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                phases=(),
            )

    def test_session_contract_rejects_missing_phases(self) -> None:
        from pydantic import ValidationError

        from onex_change_control.overseer.model_session_contract import (
            ModelSessionContract,
        )

        with pytest.raises((ValidationError, TypeError)):
            ModelSessionContract(  # type: ignore[call-arg]
                session_id="test",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

    def test_session_contract_accepts_non_empty_phases(self) -> None:
        from onex_change_control.overseer.model_session_contract import (
            ModelSessionContract,
            ModelSessionPhaseSpec,
        )

        contract = ModelSessionContract(
            session_id="test",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            phases=(ModelSessionPhaseSpec(phase_name="init"),),
        )
        assert len(contract.phases) == 1


class TestCRFinding5MajorTaskStateEnvelopeTaskIdRequired:
    """CR#5 (Major): task_id auto-generated with uuid4 default_factory.

    Creates orphan state envelopes when callers omit task identity.
    task_id should be required (no default).
    """

    def test_task_state_envelope_requires_task_id(self) -> None:
        from pydantic import ValidationError

        from onex_change_control.overseer.model_task_state_envelope import (
            EnumTaskStatus,
            ModelTaskStateEnvelope,
        )

        with pytest.raises((ValidationError, TypeError)):
            ModelTaskStateEnvelope(  # type: ignore[call-arg]
                status=EnumTaskStatus.PENDING,
                domain="test",
                node_id="node-1",
            )

    def test_task_state_envelope_accepts_explicit_task_id(self) -> None:
        from onex_change_control.overseer.model_task_state_envelope import (
            EnumTaskStatus,
            ModelTaskStateEnvelope,
        )

        envelope = ModelTaskStateEnvelope(
            task_id="explicit-id-123",
            status=EnumTaskStatus.PENDING,
            domain="test",
            node_id="node-1",
        )
        assert envelope.task_id == "explicit-id-123"


class TestCRFinding6MinorModelContextBundleExported:
    """CR#6 (Minor): ModelContextBundle not exported from overseer/__init__.py."""

    def test_model_context_bundle_importable_from_overseer_package(self) -> None:
        import onex_change_control.overseer as pkg

        assert pkg.ModelContextBundle is not None

    def test_model_context_bundle_in_all(self) -> None:
        import onex_change_control.overseer as pkg

        assert "ModelContextBundle" in pkg.__all__


class TestCRFinding7MinorWorkerContractLoadAcceptsMapping:
    """CR#7 (Minor): load_worker_contract rejects valid Mapping types.

    The function docstring says it accepts a mapping but isinstance(data, dict)
    rejects read-only mappings and other Mapping subclasses.
    """

    def test_load_worker_contract_accepts_mapping_proxy(self) -> None:
        from types import MappingProxyType

        from onex_change_control.overseer.model_worker_contract import (
            load_worker_contract,
        )

        data = MappingProxyType({"worker_name": "test-worker"})
        contract = load_worker_contract(data)
        assert contract.worker_name == "test-worker"

    def test_load_worker_contract_still_rejects_non_mapping(self) -> None:
        from onex_change_control.overseer.model_worker_contract import (
            load_worker_contract,
        )

        with pytest.raises(TypeError):
            load_worker_contract("not-a-mapping")  # type: ignore[arg-type]


class TestCRFinding8MajorOvernightHaltConditionConditionalFields:
    """CR#8 (Major): ModelOvernightHaltCondition missing conditional field validators.

    Comments document: skill required when on_halt='dispatch_skill',
    pr+threshold_minutes required when check_type='pr_blocked_too_long',
    outcome required when check_type='required_outcome_missing'. No validators
    enforce these, so invalid structs pass schema validation silently.
    """

    def test_dispatch_skill_requires_skill_field(self) -> None:
        from pydantic import ValidationError

        from onex_change_control.overseer.model_overnight_contract import (
            ModelOvernightHaltCondition,
        )

        with pytest.raises(ValidationError, match="skill is required"):
            ModelOvernightHaltCondition(
                condition_id="test",
                description="test",
                check_type="cost_ceiling",
                on_halt="dispatch_skill",
                skill=None,
            )

    def test_dispatch_skill_accepts_skill_field(self) -> None:
        from onex_change_control.overseer.model_overnight_contract import (
            ModelOvernightHaltCondition,
        )

        cond = ModelOvernightHaltCondition(
            condition_id="test",
            description="test",
            check_type="cost_ceiling",
            on_halt="dispatch_skill",
            skill="onex:pr_polish",
        )
        assert cond.skill == "onex:pr_polish"

    def test_pr_blocked_requires_pr_and_threshold_minutes(self) -> None:
        from pydantic import ValidationError

        from onex_change_control.overseer.model_overnight_contract import (
            ModelOvernightHaltCondition,
        )

        with pytest.raises(ValidationError, match="pr is required"):
            ModelOvernightHaltCondition(
                condition_id="test",
                description="test",
                check_type="pr_blocked_too_long",
                pr=None,
                threshold_minutes=60.0,
            )

        with pytest.raises(ValidationError, match="threshold_minutes is required"):
            ModelOvernightHaltCondition(
                condition_id="test",
                description="test",
                check_type="pr_blocked_too_long",
                pr=157,
                threshold_minutes=None,
            )

    def test_pr_blocked_accepts_valid_fields(self) -> None:
        from onex_change_control.overseer.model_overnight_contract import (
            ModelOvernightHaltCondition,
        )

        cond = ModelOvernightHaltCondition(
            condition_id="test",
            description="test",
            check_type="pr_blocked_too_long",
            pr=157,
            threshold_minutes=60.0,
        )
        assert cond.pr == 157
        assert cond.threshold_minutes == 60.0

    def test_required_outcome_missing_requires_outcome(self) -> None:
        from pydantic import ValidationError

        from onex_change_control.overseer.model_overnight_contract import (
            ModelOvernightHaltCondition,
        )

        with pytest.raises(ValidationError, match="outcome is required"):
            ModelOvernightHaltCondition(
                condition_id="test",
                description="test",
                check_type="required_outcome_missing",
                outcome=None,
            )

    def test_required_outcome_missing_accepts_outcome(self) -> None:
        from onex_change_control.overseer.model_overnight_contract import (
            ModelOvernightHaltCondition,
        )

        cond = ModelOvernightHaltCondition(
            condition_id="test",
            description="test",
            check_type="required_outcome_missing",
            outcome="merge_sweep_completed",
        )
        assert cond.outcome == "merge_sweep_completed"
