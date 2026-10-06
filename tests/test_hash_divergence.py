# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests documenting hash divergence between local and omnibase_core canonicalization.

These tests exist to quantify and track the divergence between Mohammed's
compute_canonical_hash (json.dumps, sort_keys=True) and omnibase_core's
canonicalize_for_hash (YAML-based, volatile field suppression). The tests
are informational — they document the gap, not gate on it.
"""

from __future__ import annotations

from typing import Any

import pytest

from onex_change_control.handlers.handler_drift_analysis import (
    compute_canonical_hash,
)


def test_hash_divergence_on_contract_with_volatile_fields() -> None:
    """Volatile fields cause divergence — omnibase_core suppresses them, we don't."""
    contract_with_volatile: dict[str, Any] = {
        "name": "test",
        "type": "COMPUTE",
        "hash": "abc123",  # volatile field
        "last_modified_at": "2026-01-01T00:00:00Z",  # volatile field
        "algorithm": {"type": "default"},
    }
    hash_with_volatile = compute_canonical_hash(contract_with_volatile)

    contract_without_volatile: dict[str, Any] = {
        "name": "test",
        "type": "COMPUTE",
        "algorithm": {"type": "default"},
    }
    hash_without_volatile = compute_canonical_hash(contract_without_volatile)

    # Our hash WILL differ because we don't suppress volatile fields
    assert hash_with_volatile != hash_without_volatile, (
        "compute_canonical_hash does not suppress volatile fields — "
        "this is the known divergence from ProtocolCanonicalSerializer"
    )


def test_hash_divergence_on_clean_contract(
    base_compute_contract: dict[str, Any],
) -> None:
    """Document divergence between local hash and omnibase_core canonicalization."""
    local_hash = compute_canonical_hash(base_compute_contract)
    try:
        from omnibase_core.contracts.contract_hash_registry import (
            ContractHashRegistry,
        )

        registry = ContractHashRegistry()
        core_fingerprint = registry.compute_contract_fingerprint(base_compute_contract)  # type: ignore[attr-defined]
        # Extract hash portion from fingerprint
        # Document whether they match — this test is informational, not a gate
        assert local_hash != str(core_fingerprint), (
            "If these match, the divergence warning can be removed"
        )
    except (ImportError, TypeError, AttributeError):
        pytest.skip("omnibase_core fingerprint not available or API changed")
