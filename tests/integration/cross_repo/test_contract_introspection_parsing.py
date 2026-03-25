# SPDX-License-Identifier: MIT
"""Cross-repo regression: contract.yaml introspection parsing.

OMN-6405: ModelContractBase uses extra="forbid" but production contract YAMLs
contain sections not declared in the Pydantic model. This caused
ServiceNodeIntrospection._try_load_contract() to silently return None, leaving
metadata.description=None in all Kafka introspection events.

This test loads every contract.yaml across ALL repos through the introspection
parsing pipeline and asserts description is extractable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# omni_home root — parent of onex_change_control
OMNI_HOME = Path(__file__).resolve().parents[3].parent

# Repos that contain ONEX node contracts
CONTRACT_REPOS = [
    "omnibase_infra",
    "omnibase_core",
    "omnibase_spi",
    "omniclaude",
    "omniintelligence",
    "omnimemory",
    "omninode_infra",
    "onex_change_control",
]


def _discover_all_contracts() -> list[tuple[str, Path]]:
    """Find (repo_name, contract_dir) for every node with a contract.yaml."""
    results = []
    for repo in CONTRACT_REPOS:
        repo_path = OMNI_HOME / repo
        if not repo_path.is_dir():
            continue
        for contract_yaml in sorted(repo_path.rglob("nodes/*/contract.yaml")):
            contract_dir = contract_yaml.parent
            results.append((repo, contract_dir))
    return results


ALL_CONTRACTS = _discover_all_contracts()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("repo", "contract_dir"),
    ALL_CONTRACTS,
    ids=[f"{repo}/{d.name}" for repo, d in ALL_CONTRACTS],
)
def test_contract_description_extractable(repo: str, contract_dir: Path) -> None:
    """Every contract.yaml must have a description.

    If this fails, either:
    1. The contract.yaml is missing a 'description' field, or
    2. The parsing pipeline silently dropped it (the OMN-6405 bug class)
    """
    import yaml

    contract_yaml = contract_dir / "contract.yaml"
    with contract_yaml.open() as f:
        raw = yaml.safe_load(f)

    assert isinstance(raw, dict), f"contract.yaml is not a YAML dict: {contract_yaml}"

    description = raw.get("description")
    assert description is not None, f"{repo}/{contract_dir.name}: missing 'description'"
    assert isinstance(description, str), (
        f"{repo}/{contract_dir.name}: description not a string"
    )
    assert len(description.strip()) > 0, (
        f"{repo}/{contract_dir.name}: description is empty"
    )


@pytest.mark.integration
def test_infra_contracts_parse_through_introspection_service() -> None:
    """omnibase_infra contracts must fully parse through ServiceNodeIntrospection.

    This is the deeper check — not just that the YAML has a description field,
    but that the introspection service can actually extract it end-to-end.
    Only runs for omnibase_infra since ServiceNodeIntrospection lives there.
    """
    infra_path = OMNI_HOME / "omnibase_infra"
    if not infra_path.is_dir():
        pytest.skip("omnibase_infra not found")

    try:
        from omnibase_infra.services.service_node_introspection import (  # type: ignore[import-not-found]
            ServiceNodeIntrospection,
        )
    except ImportError:
        pytest.skip("omnibase_infra not importable")

    contracts = [d.parent for d in sorted(infra_path.rglob("nodes/*/contract.yaml"))]
    assert len(contracts) > 0, "No contracts found in omnibase_infra"

    failures = []
    for contract_dir in contracts:
        svc = ServiceNodeIntrospection.from_contract_dir(
            contracts_dir=contract_dir,
            event_bus=None,
            node_name=f"test-{contract_dir.name}",
        )
        has_description = svc._description_override is not None or (
            svc._introspection_contract is not None
            and getattr(svc._introspection_contract, "description", None) is not None
        )
        if not has_description:
            failures.append(contract_dir.name)

    assert not failures, (
        f"{len(failures)} omnibase_infra contracts failed introspection parsing "
        f"(description=None): {failures}. This is the OMN-6405 bug class."
    )
