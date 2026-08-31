# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Regression coverage for the OMN-11717 nightly promotion contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from onex_change_control.kafka.topics import GovernanceTopic
from onex_change_control.models.model_wire_schema_contract import (
    load_wire_schema_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMOTION_TOPIC = "onex.evt.occ.nightly-promotion.v1"
REQUIRED_INPUT_FIELDS = {
    "promotion_batch_id",
    "intended_manifest",
    "per_repo_results",
    "runtime_topology_proof",
    "cross_repo_integration_result",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    return data


def test_central_ticket_contract_declares_promotion_authority() -> None:
    data = _load_yaml(REPO_ROOT / "contracts" / "OMN-11717.yaml")

    assert data["ticket_id"] == "OMN-11717"
    assert data["is_seam_ticket"] is True
    assert {"topics", "events", "envelopes"} <= set(data["interfaces_touched"])
    assert "onex.evt.occ.nightly-promotion.v1" in data["summary"]
    assert "occ_nightly_promotion_v1.yaml" in data["summary"]


def test_wire_schema_declares_required_evidence_fields() -> None:
    data = _load_yaml(
        REPO_ROOT
        / "src"
        / "onex_change_control"
        / "wire_schemas"
        / "occ_nightly_promotion_v1.yaml"
    )

    contract = load_wire_schema_contract(data)

    assert contract.topic == PROMOTION_TOPIC
    assert contract.topic_authority == "onex_change_control"
    assert contract.freshness_sla == "24h"
    assert contract.required_field_names == REQUIRED_INPUT_FIELDS
    assert "verifier_identity" in data["output"]["promotion_receipt"]["required_fields"]
    assert GovernanceTopic.NIGHTLY_PROMOTION.value == contract.topic
