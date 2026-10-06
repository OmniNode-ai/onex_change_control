# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for handler_drift_analysis — the pure logic layer.

These tests have zero OmniNode runtime dependencies and form the primary
test surface for the feature.

Ported from Mohammed Jalal's assessment submission.
"""

from __future__ import annotations

import copy
from typing import Any

from onex_change_control.enums.enum_drift_sensitivity import EnumDriftSensitivity
from onex_change_control.enums.enum_drift_severity import EnumDriftSeverity
from onex_change_control.handlers.handler_drift_analysis import (
    analyze_drift,
    analyze_drift_with_pinned_contract,
    compute_canonical_hash,
)
from onex_change_control.models.model_contract_drift_input import (
    ModelContractDriftInput,
)

# ---------------------------------------------------------------------------
# compute_canonical_hash
# ---------------------------------------------------------------------------


class TestComputeCanonicalHash:
    def test_same_dict_same_hash(self, base_compute_contract: dict[str, Any]) -> None:
        h1 = compute_canonical_hash(base_compute_contract)
        h2 = compute_canonical_hash(base_compute_contract)
        assert h1 == h2

    def test_key_order_invariant(self, base_compute_contract: dict[str, Any]) -> None:
        """Hash must not depend on key insertion order."""
        reordered = {
            k: base_compute_contract[k] for k in reversed(list(base_compute_contract))
        }
        assert compute_canonical_hash(base_compute_contract) == compute_canonical_hash(
            reordered
        )

    def test_value_change_changes_hash(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        modified = copy.deepcopy(base_compute_contract)
        modified["version"] = "2.0.0"
        assert compute_canonical_hash(base_compute_contract) != compute_canonical_hash(
            modified
        )

    def test_returns_hex_string(self, base_compute_contract: dict[str, Any]) -> None:
        h = compute_canonical_hash(base_compute_contract)
        assert len(h) == 64
        int(h, 16)  # raises ValueError if not valid hex


# ---------------------------------------------------------------------------
# analyze_drift — hash-only path
# ---------------------------------------------------------------------------


class TestAnalyzeDriftHashOnly:
    def test_no_drift_when_hashes_match(
        self, drift_input_no_change: ModelContractDriftInput
    ) -> None:
        result = analyze_drift(drift_input_no_change)
        assert result.severity == EnumDriftSeverity.NONE
        assert not result.drift_detected
        assert result.field_changes == []

    def test_breaking_when_hashes_differ(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        fake_pinned_hash = "0" * 64
        drift_input = ModelContractDriftInput(
            contract_name="node_transform_data",
            current_contract=base_compute_contract,
            pinned_hash=fake_pinned_hash,
        )
        result = analyze_drift(drift_input)
        assert result.severity == EnumDriftSeverity.BREAKING
        assert result.drift_detected
        assert len(result.breaking_changes) == 1
        assert "hash drift" in result.summary

    def test_current_hash_is_set_correctly(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        expected_hash = compute_canonical_hash(base_compute_contract)
        drift_input = ModelContractDriftInput(
            contract_name="test",
            current_contract=base_compute_contract,
            pinned_hash=expected_hash,
        )
        result = analyze_drift(drift_input)
        assert result.current_hash == expected_hash


# ---------------------------------------------------------------------------
# analyze_drift_with_pinned_contract — field-level path
# ---------------------------------------------------------------------------


class TestAnalyzeDriftDetailed:
    def _make_input(
        self,
        contract: dict[str, Any],
        pinned: dict[str, Any],
        sensitivity: EnumDriftSensitivity = EnumDriftSensitivity.STANDARD,
    ) -> tuple[ModelContractDriftInput, dict[str, Any]]:
        drift_input = ModelContractDriftInput(
            contract_name="test_contract",
            current_contract=contract,
            pinned_hash=compute_canonical_hash(pinned),
            sensitivity=sensitivity,
        )
        return drift_input, pinned

    # --- NONE ---

    def test_no_drift_identical(self, base_compute_contract: dict[str, Any]) -> None:
        inp, pinned = self._make_input(base_compute_contract, base_compute_contract)
        result = analyze_drift_with_pinned_contract(inp, pinned)
        assert result.severity == EnumDriftSeverity.NONE
        assert result.field_changes == []

    # --- BREAKING ---

    def test_breaking_field_removed(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        current = copy.deepcopy(base_compute_contract)
        del current["input_schema"]
        inp, pinned = self._make_input(current, base_compute_contract)
        result = analyze_drift_with_pinned_contract(inp, pinned)
        assert result.severity == EnumDriftSeverity.BREAKING
        removed = [c for c in result.field_changes if c.path == "input_schema"]
        assert len(removed) == 1
        assert removed[0].change_type == "removed"
        assert removed[0].is_breaking

    def test_breaking_algorithm_type_changed(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        current = copy.deepcopy(base_compute_contract)
        current["algorithm"]["algorithm_type"] = "custom_transform"
        inp, pinned = self._make_input(current, base_compute_contract)
        result = analyze_drift_with_pinned_contract(inp, pinned)
        assert result.severity == EnumDriftSeverity.BREAKING
        assert any(c.is_breaking for c in result.field_changes)

    def test_breaking_type_field_changed(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        current = copy.deepcopy(base_compute_contract)
        current["type"] = "EFFECT"
        inp, pinned = self._make_input(current, base_compute_contract)
        result = analyze_drift_with_pinned_contract(inp, pinned)
        assert result.severity == EnumDriftSeverity.BREAKING

    def test_breaking_listed_in_output(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        current = copy.deepcopy(base_compute_contract)
        del current["output_schema"]
        inp, pinned = self._make_input(current, base_compute_contract)
        result = analyze_drift_with_pinned_contract(inp, pinned)
        assert len(result.breaking_changes) >= 1

    # --- ADDITIVE ---

    def test_additive_new_optional_field(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        current = copy.deepcopy(base_compute_contract)
        current["retry_policy"] = {"max_retries": 3}
        inp, pinned = self._make_input(current, base_compute_contract)
        result = analyze_drift_with_pinned_contract(inp, pinned)
        assert result.severity == EnumDriftSeverity.ADDITIVE
        assert len(result.additive_changes) >= 1

    def test_additive_not_reported_in_lax_mode(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        current = copy.deepcopy(base_compute_contract)
        current["retry_policy"] = {"max_retries": 3}
        inp, pinned = self._make_input(
            current,
            base_compute_contract,
            sensitivity=EnumDriftSensitivity.LAX,
        )
        result = analyze_drift_with_pinned_contract(inp, pinned)
        # In LAX mode additive changes are not flagged — NONE expected.
        assert result.severity == EnumDriftSeverity.NONE

    # --- NON_BREAKING ---

    def test_non_breaking_description_change(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        current = copy.deepcopy(base_compute_contract)
        current["description"] = "Updated description."
        inp, pinned = self._make_input(current, base_compute_contract)
        result = analyze_drift_with_pinned_contract(inp, pinned)
        assert result.severity == EnumDriftSeverity.NON_BREAKING
        assert len(result.non_breaking_changes) >= 1

    def test_non_breaking_suppressed_in_lax_mode(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        current = copy.deepcopy(base_compute_contract)
        current["description"] = "Updated description."
        inp, pinned = self._make_input(
            current,
            base_compute_contract,
            sensitivity=EnumDriftSensitivity.LAX,
        )
        result = analyze_drift_with_pinned_contract(inp, pinned)
        assert result.severity == EnumDriftSeverity.NONE

    def test_non_breaking_suppressed_in_standard_if_only_additive_too(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        """BREAKING outranks ADDITIVE outranks NON_BREAKING."""
        current = copy.deepcopy(base_compute_contract)
        current["description"] = "Changed."  # non-breaking
        current["new_field"] = "value"  # additive
        current["type"] = "EFFECT"  # breaking
        inp, pinned = self._make_input(current, base_compute_contract)
        result = analyze_drift_with_pinned_contract(inp, pinned)
        assert result.severity == EnumDriftSeverity.BREAKING

    # --- Summary ---

    def test_summary_contains_contract_name(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        current = copy.deepcopy(base_compute_contract)
        current["version"] = "2.0.0"
        inp, pinned = self._make_input(current, base_compute_contract)
        result = analyze_drift_with_pinned_contract(inp, pinned)
        assert "test_contract" in result.summary

    def test_field_changes_count_matches(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        current = copy.deepcopy(base_compute_contract)
        current["version"] = "2.0.0"
        del current["description"]
        current["new_key"] = "new_value"
        inp, pinned = self._make_input(current, base_compute_contract)
        result = analyze_drift_with_pinned_contract(inp, pinned)
        # version modified, description removed, new_key added
        assert len(result.field_changes) == 3

    # --- Deep nested diff ---

    def test_nested_algorithm_field_diff(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        current = copy.deepcopy(base_compute_contract)
        current["algorithm"]["deterministic"] = False
        inp, pinned = self._make_input(current, base_compute_contract)
        result = analyze_drift_with_pinned_contract(inp, pinned)
        nested_changes = [c for c in result.field_changes if "algorithm" in c.path]
        assert len(nested_changes) >= 1
        # algorithm is a breaking path
        assert any(c.is_breaking for c in nested_changes)

    def test_nested_metadata_field_non_breaking(
        self, base_compute_contract: dict[str, Any]
    ) -> None:
        current = copy.deepcopy(base_compute_contract)
        current["metadata"]["sla_ms"] = 200
        inp, pinned = self._make_input(current, base_compute_contract)
        result = analyze_drift_with_pinned_contract(inp, pinned)
        metadata_changes = [c for c in result.field_changes if "metadata" in c.path]
        assert len(metadata_changes) >= 1
        assert not any(c.is_breaking for c in metadata_changes)
