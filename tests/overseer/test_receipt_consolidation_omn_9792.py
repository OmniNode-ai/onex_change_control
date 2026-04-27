# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""TDD-first tests for OMN-9792: ModelVerifierCheckResult removed; checks re-typed."""

from __future__ import annotations

from datetime import UTC, datetime


class TestVerifierOutputConsolidationOMN9792:
    """OMN-9792: ModelVerifierCheckResult removed.

    checks field re-typed to ModelDodReceipt.
    """

    def test_model_verifier_check_result_does_not_exist(self) -> None:
        import onex_change_control.overseer as pkg

        assert not hasattr(pkg, "ModelVerifierCheckResult"), (
            "ModelVerifierCheckResult must be deleted (OMN-9792)"
        )

    def test_model_verifier_output_checks_accepts_model_dod_receipt(self) -> None:
        from omnibase_core.enums.ticket.enum_receipt_status import EnumReceiptStatus
        from omnibase_core.models.contracts.ticket.model_dod_receipt import (
            ModelDodReceipt,
        )

        from onex_change_control.overseer.enum_verifier_verdict import (
            EnumVerifierVerdict,
        )
        from onex_change_control.overseer.model_verifier_output import (
            ModelVerifierOutput,
        )

        receipt = ModelDodReceipt(
            ticket_id="OMN-9792",
            evidence_item_id="dod-001",
            check_type="command",
            check_value="uv run pytest tests/ -v",
            status=EnumReceiptStatus.PASS,
            run_timestamp=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC),
            commit_sha="a1b2c3d4e5f6",  # pragma: allowlist secret
            runner="ci-worker",
        )
        output = ModelVerifierOutput(
            verdict=EnumVerifierVerdict.PASS,
            checks=(receipt,),
        )
        assert len(output.checks) == 1
        assert isinstance(output.checks[0], ModelDodReceipt)

    def test_model_verifier_output_checks_type_annotation_is_dod_receipt(self) -> None:
        from onex_change_control.overseer.model_verifier_output import (
            ModelVerifierOutput,
        )

        schema = ModelVerifierOutput.model_json_schema()
        checks_prop = schema.get("properties", {}).get("checks", {})
        # The items should reference ModelDodReceipt, not ModelVerifierCheckResult
        items = checks_prop.get("items", checks_prop.get("prefixItems", {}))
        ref = items.get("$ref", "")
        assert "ModelVerifierCheckResult" not in ref, (
            "checks items must not reference ModelVerifierCheckResult"
        )

    def test_model_verifier_check_result_not_importable_from_module(self) -> None:
        import onex_change_control.overseer.model_verifier_output as mod

        assert not hasattr(mod, "ModelVerifierCheckResult"), (
            "ModelVerifierCheckResult must not exist in model_verifier_output module"
        )
