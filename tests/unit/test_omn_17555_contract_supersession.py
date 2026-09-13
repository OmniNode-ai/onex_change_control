# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Static contract truth for OMN-17555's typed configuration migration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from onex_change_control.models.model_ticket_contract import ModelTicketContract

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_contract(ticket_id: str) -> dict[str, Any]:
    raw = yaml.safe_load(
        (_REPO_ROOT / "contracts" / f"{ticket_id}.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(raw, dict)
    ModelTicketContract.model_validate(raw)
    return raw


def _evidence_items(ticket_id: str) -> list[dict[str, Any]]:
    raw_items = _load_contract(ticket_id).get("dod_evidence")
    assert isinstance(raw_items, list)
    items: list[dict[str, Any]] = []
    for item in raw_items:
        assert isinstance(item, dict)
        items.append(item)
    return items


def _assert_pending_supersessions(
    ticket_id: str,
    expected: dict[str, tuple[str, tuple[str, ...]]],
) -> None:
    items = _evidence_items(ticket_id)
    ids = [item.get("id") for item in items]
    for old_id, (new_id, required_fragments) in expected.items():
        assert ids.count(old_id) == 1
        assert ids.count(new_id) == 1
        assert ids.index(old_id) < ids.index(new_id)
        replacement = items[ids.index(new_id)]
        assert replacement.get("source") == "manual"
        assert replacement.get("execution_scope") == "local_done_gate"
        assert replacement.get("status") == "pending"
        assert replacement.get("evidence_artifact") == (
            f"supersedes_dod_evidence:{old_id}"
        )
        description = replacement.get("description")
        assert isinstance(description, str)
        for fragment in required_fragments:
            assert fragment in description


def test_companion_declares_all_authorities_and_dependencies() -> None:
    contract = _load_contract("OMN-17555")
    assert contract["is_seam_ticket"] is True
    assert contract["interface_change"] is True
    assert contract["interfaces_touched"] == ["public_api"]
    summary = contract["summary"]
    assert isinstance(summary, str)
    for authority in (
        "ArtifactStore(root: Path)",
        "explicit env-file Path",
        "ModelLifecycleChain.heartbeat_required_seconds",
        "ModelRuntimeAlivenessProbeCommand.timeout_seconds",
        "Kubernetes service-account mount",
        "ModelNodeServiceConfig.network: ModelNetworkConfig",
        "validate_context_size(..., enforce: bool)",
    ):
        assert authority in summary
    for dependency in ("OMN-17554", "OMN-17565", "OMN-17566", "OMN-17570", "OMN-17571"):
        assert dependency in summary
    assert "no runtime, deployment, or activation claim" in summary
    for item in _evidence_items("OMN-17555"):
        assert item.get("status") == "pending"


def test_omn_9885_lifecycle_assertions_are_rebound_append_only() -> None:
    _assert_pending_supersessions(
        "OMN-9885",
        {
            "dod-002": (
                "dod-omn17555-configured-heartbeat-policy",
                (
                    "required typed wire state",
                    "ambient LIFECYCLE_HEARTBEAT_REQUIRED_SECONDS",
                ),
            ),
            "dod-003": (
                "dod-omn17555-lifecycle-suite-rebound",
                ("required typed heartbeat threshold", "without a default"),
            ),
            "dod-005": (
                "dod-omn17555-lifecycle-gates-rebound",
                ("no-new-env-vars", "Pydantic extra-forbid"),
            ),
        },
    )


def test_omn_9887_timeout_assertion_is_rebound_append_only() -> None:
    _assert_pending_supersessions(
        "OMN-9887",
        {
            "dod-002": (
                "dod-omn17555-explicit-runtime-timeout",
                (
                    "required validated wire state",
                    "ambient RUNTIME_ALIVENESS_TIMEOUT_SECONDS",
                ),
            )
        },
    )


def test_omn_13093_artifact_root_assertion_is_rebound_append_only() -> None:
    _assert_pending_supersessions(
        "OMN-13093",
        {
            "dod-omnibase-core-pr-1236": (
                "dod-omn17555-typed-artifact-root",
                ("ArtifactStore(root: Path)", "OMN-17565", "OMN-17566"),
            )
        },
    )


def test_omn_13537_infra_assertions_are_rebound_append_only() -> None:
    _assert_pending_supersessions(
        "OMN-13537",
        {
            "dod-omnibase-infra-pr-2083": (
                "dod-omn17555-explicit-infra-artifact-root",
                ("OMN-17565", "ArtifactStore(root=...)"),
            ),
            "dod-receipt-capture-tests": (
                "dod-omn17555-infra-capture-tests-rebound",
                ("OMN-17565", "ambient ONEX_ARTIFACT_STORE_ROOT"),
            ),
        },
    )


def test_omn_13095_omniclaude_assertion_is_rebound_append_only() -> None:
    _assert_pending_supersessions(
        "OMN-13095",
        {
            "dod-omniclaude-pr-1752": (
                "dod-omn17555-explicit-omniclaude-artifact-root",
                ("OMN-17566", "ArtifactStore(root=...)"),
            )
        },
    )
