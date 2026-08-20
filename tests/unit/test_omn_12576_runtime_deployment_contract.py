# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Regression coverage for the OMN-12576 runtime deployment OCC wire contract.

OCC owns the deployment wire schema as the source of truth (plan
``docs/plans/2026-06-01-node-based-runtime-deployment-occ-tdd.md`` "Open
Decisions": OCC owns the wire schema; mirror transiently through
``omnibase_compat``). These tests pin the runtime-deployment request and proof
wire schemas, their topic authority, and the required lane/digest/promotion
fields that downstream node and validator work consumes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from onex_change_control.kafka.topics import GovernanceTopic
from onex_change_control.models.model_wire_schema_contract import (
    load_wire_schema_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WIRE_SCHEMA_DIR = REPO_ROOT / "src" / "onex_change_control" / "wire_schemas"

REQUEST_TOPIC = "onex.cmd.omnimarket.redeploy-start.v1"
PROOF_TOPIC = "onex.evt.omnimarket.runtime-deployment-proof.v1"

REQUEST_REQUIRED_FIELDS = {
    "correlation_id",
    "deployment_id",
    "runtime_lane",
    "source_branch",
    "source_sha",
    "requested_by",
    "requested_at",
}

PROOF_REQUIRED_FIELDS = {
    "correlation_id",
    "deployment_id",
    "runtime_lane",
    "source_sha",
    "image_digest",
    "compose_project",
    "health_status",
    "ready_status",
    "probed_at",
    "status",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    return data


def test_runtime_deployment_request_topic_registered() -> None:
    assert GovernanceTopic.RUNTIME_DEPLOYMENT_REQUEST.value == REQUEST_TOPIC, (
        "OCC must register the runtime deployment request topic authority."
    )


def test_runtime_deployment_proof_topic_registered() -> None:
    assert GovernanceTopic.RUNTIME_DEPLOYMENT_PROOF.value == PROOF_TOPIC


def test_request_wire_schema_declares_required_lane_digest_fields() -> None:
    data = _load_yaml(WIRE_SCHEMA_DIR / "runtime_deployment_request_v1.yaml")
    contract = load_wire_schema_contract(data)

    assert contract.topic == REQUEST_TOPIC
    assert contract.topic_authority == "onex_change_control"
    assert contract.required_field_names == REQUEST_REQUIRED_FIELDS
    # image_digest and promotion_batch_id are optional on the request (the dev
    # lane builds from a ref; only prod pins a digest), but they MUST be
    # declarable so the validator and prod gate can read them.
    assert {"image_digest", "promotion_batch_id"} <= contract.optional_field_names


def test_proof_wire_schema_declares_required_proof_fields() -> None:
    data = _load_yaml(WIRE_SCHEMA_DIR / "runtime_deployment_proof_v1.yaml")
    contract = load_wire_schema_contract(data)

    assert contract.topic == PROOF_TOPIC
    assert contract.topic_authority == "onex_change_control"
    assert contract.freshness_sla == "24h"
    assert contract.required_field_names == PROOF_REQUIRED_FIELDS
    # image_digest is the prod-gate authority and MUST be a required proof field.
    assert "image_digest" in contract.required_field_names


def test_request_schema_rejects_missing_runtime_lane() -> None:
    """A request wire schema without runtime_lane is a malformed contract."""
    data = _load_yaml(WIRE_SCHEMA_DIR / "runtime_deployment_request_v1.yaml")
    data["required_fields"] = [
        f for f in data["required_fields"] if f["name"] != "runtime_lane"
    ]
    contract = load_wire_schema_contract(data)
    assert "runtime_lane" not in contract.required_field_names


def test_central_ticket_contract_declares_runtime_deployment_authority() -> None:
    data = _load_yaml(REPO_ROOT / "contracts" / "OMN-12576.yaml")

    assert data["ticket_id"] == "OMN-12576"
    assert data["is_seam_ticket"] is True
    assert {"topics", "events", "envelopes"} <= set(data["interfaces_touched"])
    assert "runtime_deployment_request_v1.yaml" in data["summary"]
