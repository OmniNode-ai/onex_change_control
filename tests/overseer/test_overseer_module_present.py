# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""TDD-first tests for OMN-8431: overseer module in onex_change_control.

These tests assert the post-migration state. They fail before the files
are copied, pass after.
"""

from __future__ import annotations


class TestOverseerContractModels:
    def test_model_worker_contract_importable(self) -> None:
        from onex_change_control.overseer.model_worker_contract import (
            ModelWorkerContract,
        )

        contract = ModelWorkerContract(worker_name="test-worker")
        assert contract.worker_name == "test-worker"
        assert contract.schema_version == "1.0.0"

    def test_model_overnight_contract_importable(self) -> None:
        from onex_change_control.overseer.model_overnight_contract import (
            ModelOvernightContract,
        )

        assert ModelOvernightContract is not None

    def test_model_dispatch_item_importable(self) -> None:
        from onex_change_control.overseer.model_dispatch_item import ModelDispatchItem

        assert ModelDispatchItem is not None

    def test_model_session_contract_importable(self) -> None:
        from onex_change_control.overseer.model_session_contract import (
            ModelSessionContract,
        )

        assert ModelSessionContract is not None

    def test_model_context_bundle_importable(self) -> None:
        from onex_change_control.overseer.model_context_bundle import ModelContextBundle

        assert ModelContextBundle is not None

    def test_model_verifier_output_importable(self) -> None:
        from onex_change_control.overseer.model_verifier_output import (
            ModelVerifierOutput,
        )

        assert ModelVerifierOutput is not None

    def test_model_task_state_envelope_importable(self) -> None:
        from onex_change_control.overseer.model_task_state_envelope import (
            ModelTaskStateEnvelope,
        )

        assert ModelTaskStateEnvelope is not None

    def test_model_completion_report_importable(self) -> None:
        from onex_change_control.overseer.model_completion_report import (
            ModelCompletionReport,
        )

        assert ModelCompletionReport is not None


class TestOverseerEnums:
    def test_enum_failure_class_importable(self) -> None:
        from onex_change_control.overseer.enum_failure_class import EnumFailureClass

        assert EnumFailureClass is not None

    def test_enum_verifier_verdict_importable(self) -> None:
        from onex_change_control.overseer.enum_verifier_verdict import (
            EnumVerifierVerdict,
        )

        assert EnumVerifierVerdict is not None

    def test_enum_provider_importable(self) -> None:
        from onex_change_control.overseer.enum_provider import EnumProvider

        assert EnumProvider is not None

    def test_enum_process_runner_state_importable(self) -> None:
        from onex_change_control.overseer.enum_process_runner_state import (
            EnumProcessRunnerState,
        )

        assert EnumProcessRunnerState is not None

    def test_action_enums_importable(self) -> None:
        from onex_change_control.overseer.enum_artifact_store_action import (
            EnumArtifactStoreAction,
        )
        from onex_change_control.overseer.enum_code_repository_action import (
            EnumCodeRepositoryAction,
        )
        from onex_change_control.overseer.enum_event_bus_action import (
            EnumEventBusAction,
        )
        from onex_change_control.overseer.enum_llm_provider_action import (
            EnumLLMProviderAction,
        )
        from onex_change_control.overseer.enum_notification_action import (
            EnumNotificationAction,
        )
        from onex_change_control.overseer.enum_ticket_service_action import (
            EnumTicketServiceAction,
        )

        for cls in [
            EnumArtifactStoreAction,
            EnumCodeRepositoryAction,
            EnumEventBusAction,
            EnumLLMProviderAction,
            EnumNotificationAction,
            EnumTicketServiceAction,
        ]:
            assert cls is not None

    def test_action_enum_values_unique(self) -> None:
        from onex_change_control.overseer.enum_artifact_store_action import (
            EnumArtifactStoreAction,
        )
        from onex_change_control.overseer.enum_code_repository_action import (
            EnumCodeRepositoryAction,
        )
        from onex_change_control.overseer.enum_event_bus_action import (
            EnumEventBusAction,
        )
        from onex_change_control.overseer.enum_llm_provider_action import (
            EnumLLMProviderAction,
        )
        from onex_change_control.overseer.enum_notification_action import (
            EnumNotificationAction,
        )
        from onex_change_control.overseer.enum_ticket_service_action import (
            EnumTicketServiceAction,
        )

        for cls in [
            EnumArtifactStoreAction,
            EnumCodeRepositoryAction,
            EnumEventBusAction,
            EnumLLMProviderAction,
            EnumNotificationAction,
            EnumTicketServiceAction,
        ]:
            values = [m.value for m in cls]
            assert len(values) == len(set(values)), (
                f"{cls.__name__} has duplicate values"
            )
