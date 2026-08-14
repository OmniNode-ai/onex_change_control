# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for delegation-runtime-profile-v1.yaml contract (OMN-10921)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CONTRACT_PATH = (
    Path(__file__).parent.parent / "contracts" / "delegation-runtime-profile-v1.yaml"
)


def _delegation_model_available() -> bool:
    try:
        from omnibase_core.models.contracts import (
            model_delegation_runtime_profile,  # noqa: F401
        )
    except ImportError:
        return False
    else:
        return True


def test_contract_file_exists() -> None:
    assert CONTRACT_PATH.exists(), f"Contract file missing: {CONTRACT_PATH}"


def test_contract_parses_valid_yaml() -> None:
    data = yaml.safe_load(CONTRACT_PATH.read_text())
    assert data["name"] == "delegation-runtime-profile"
    assert data["version"] == 1
    assert data["runtime_profile"] == "main"
    assert len(data["event_bus"]["bootstrap_servers"]) > 0


def test_contract_has_required_sections() -> None:
    data = yaml.safe_load(CONTRACT_PATH.read_text())
    assert "event_bus" in data
    assert "llm_backends" in data
    assert "security" in data
    assert "pricing" in data
    assert "dashboard" in data
    assert "datastores" in data


def test_event_bus_bootstrap_servers() -> None:
    data = yaml.safe_load(CONTRACT_PATH.read_text())
    bus = data["event_bus"]
    assert bus["provider"] == "kafka"
    assert "redpanda:9092" in bus["bootstrap_servers"]
    assert bus["topic_policy_ref"] == "delegation-topic-policy-v1"
    assert isinstance(bus["consumer_groups"], list)
    assert len(bus["consumer_groups"]) > 0


def test_llm_backends_are_dict_keyed_by_name() -> None:
    data = yaml.safe_load(CONTRACT_PATH.read_text())
    backends = data["llm_backends"]
    assert isinstance(backends, dict), (
        "llm_backends must be a dict keyed by backend name"
    )
    assert "default" in backends
    default = backends["default"]
    assert default["bifrost_endpoint_ref"] == "local-bifrost"
    assert default["max_tokens_default"] == 2048
    assert default["max_tokens_hard_limit"] == 8192
    assert default["timeout_ms"] == 120000


def test_security_refs_present() -> None:
    data = yaml.safe_load(CONTRACT_PATH.read_text())
    security = data["security"]
    assert "broker_allowlist_ref" in security
    assert "endpoint_cidr_allowlist_ref" in security
    assert "shared_secret_ref" in security
    assert isinstance(security["shared_secret_ref"], dict)
    assert "ref_name" in security["shared_secret_ref"]


def test_pricing_section() -> None:
    data = yaml.safe_load(CONTRACT_PATH.read_text())
    pricing = data["pricing"]
    assert pricing["manifest_ref"] == "delegation-pricing-v1"
    assert pricing["version"] == 1


def test_dashboard_projection_api_ref() -> None:
    data = yaml.safe_load(CONTRACT_PATH.read_text())
    dashboard = data["dashboard"]
    assert "projection_api_ref" in dashboard
    proj_ref = dashboard["projection_api_ref"]
    assert isinstance(proj_ref, dict)
    assert "ref_name" in proj_ref or "base_url_ref" in proj_ref


def test_datastores_section() -> None:
    data = yaml.safe_load(CONTRACT_PATH.read_text())
    datastores = data["datastores"]
    assert isinstance(datastores, dict)
    assert len(datastores) > 0


@pytest.mark.skipif(
    not _delegation_model_available(),
    reason="omnibase_core delegation models not yet published (OMN-10919 open)",
)
def test_contract_parses_to_typed_model() -> None:
    from omnibase_core.models.contracts.model_delegation_runtime_profile import (
        ModelDelegationRuntimeProfile,
    )

    data = yaml.safe_load(CONTRACT_PATH.read_text())
    profile = ModelDelegationRuntimeProfile(**data)
    assert profile.name == "delegation-runtime-profile"
    assert profile.version == 1
    assert profile.runtime_profile == "main"
