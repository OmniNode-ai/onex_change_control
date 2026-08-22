# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression tests for OMN-16191: overseer wire types live in omnibase_core.

Originally written for OMN-8431 (copy the overseer module into
onex_change_control) — these tests asserted the post-copy state. OMN-16191
reverses that copy: OCC's local overseer/model_*.py and enum_*.py duplicates
are deleted, and the canonical definitions live exclusively in
omnibase_core.models.overseer / omnibase_core.enums.overseer (per OMN-11225).
These tests now assert that canonical-location state instead.
"""

from __future__ import annotations


class TestOverseerContractModels:
    def test_model_worker_contract_importable(self) -> None:
        from omnibase_core.models.overseer.model_worker_contract import (
            ModelWorkerContract,
        )

        contract = ModelWorkerContract(worker_name="test-worker")
        assert contract.worker_name == "test-worker"
        assert contract.schema_version == "1.0.0"

    def test_model_session_contract_importable(self) -> None:
        from omnibase_core.models.overseer.model_session_contract import (
            ModelSessionContract,
        )

        assert ModelSessionContract is not None

    def test_model_dispatch_item_importable(self) -> None:
        from omnibase_core.models.overseer.model_dispatch_item import ModelDispatchItem

        assert ModelDispatchItem is not None

    def test_model_context_bundle_importable(self) -> None:
        from omnibase_core.models.overseer.model_context_bundle import (
            ModelContextBundle,
        )

        assert ModelContextBundle is not None

    def test_model_verifier_output_importable(self) -> None:
        from omnibase_core.models.overseer.model_verifier_output import (
            ModelVerifierOutput,
        )

        assert ModelVerifierOutput is not None

    def test_model_task_state_envelope_importable(self) -> None:
        from omnibase_core.models.overseer.model_task_state_envelope import (
            ModelTaskStateEnvelope,
        )

        assert ModelTaskStateEnvelope is not None

    def test_model_completion_report_importable(self) -> None:
        from omnibase_core.models.overseer.model_completion_report import (
            ModelCompletionReport,
        )

        assert ModelCompletionReport is not None


class TestOverseerEnums:
    def test_enum_failure_class_importable(self) -> None:
        from omnibase_core.enums.overseer.enum_failure_class import EnumFailureClass

        assert EnumFailureClass is not None

    def test_enum_verifier_verdict_importable(self) -> None:
        from omnibase_core.enums.overseer.enum_verifier_verdict import (
            EnumVerifierVerdict,
        )

        assert EnumVerifierVerdict is not None

    def test_enum_provider_importable(self) -> None:
        from omnibase_core.enums.overseer.enum_provider import EnumProvider

        assert EnumProvider is not None

    def test_enum_process_runner_state_importable(self) -> None:
        from omnibase_core.enums.overseer.enum_process_runner_state import (
            EnumProcessRunnerState,
        )

        assert EnumProcessRunnerState is not None

    def test_action_enums_importable(self) -> None:
        from omnibase_core.enums.overseer.enum_artifact_store_action import (
            EnumArtifactStoreAction,
        )
        from omnibase_core.enums.overseer.enum_code_repository_action import (
            EnumCodeRepositoryAction,
        )
        from omnibase_core.enums.overseer.enum_event_bus_action import (
            EnumEventBusAction,
        )
        from omnibase_core.enums.overseer.enum_llm_provider_action import (
            EnumLLMProviderAction,
        )
        from omnibase_core.enums.overseer.enum_notification_action import (
            EnumNotificationAction,
        )
        from omnibase_core.enums.overseer.enum_ticket_service_action import (
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
        from omnibase_core.enums.overseer.enum_artifact_store_action import (
            EnumArtifactStoreAction,
        )
        from omnibase_core.enums.overseer.enum_code_repository_action import (
            EnumCodeRepositoryAction,
        )
        from omnibase_core.enums.overseer.enum_event_bus_action import (
            EnumEventBusAction,
        )
        from omnibase_core.enums.overseer.enum_llm_provider_action import (
            EnumLLMProviderAction,
        )
        from omnibase_core.enums.overseer.enum_notification_action import (
            EnumNotificationAction,
        )
        from omnibase_core.enums.overseer.enum_ticket_service_action import (
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
