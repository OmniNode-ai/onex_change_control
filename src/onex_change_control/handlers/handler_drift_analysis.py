# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Pure drift analysis handler — no OmniNode runtime dependencies.

Following the OmniNode convention that handlers own logic and nodes are thin
coordination shells, all drift analysis lives here. The handler is a plain
function, not a class, so it is trivially testable in isolation.

Ported from Mohammed Jalal's assessment submission with import path changes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from onex_change_control.enums.enum_drift_sensitivity import (
    EnumDriftSensitivity,
)
from onex_change_control.enums.enum_drift_severity import (
    EnumDriftSeverity,
)
from onex_change_control.models.model_contract_drift_input import (
    ModelContractDriftInput,  # noqa: TC001  Why: Pydantic model needs runtime type for field annotation
)
from onex_change_control.models.model_contract_drift_output import (
    ModelContractDriftOutput,
    ModelFieldChange,
)

# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------

# Changes to these root keys are always BREAKING — they define the contract's
# observable interface (algorithm, I/O schemas, type declaration).
_BREAKING_ROOT_KEYS: frozenset[str] = frozenset(
    {
        "algorithm",
        "input_schema",
        "output_schema",
        "type",
        "required",
        "parallel_processing",
        "transaction_management",
    }
)

# Changes to these root keys are always NON_BREAKING — pure documentation/meta.
_NON_BREAKING_ROOT_KEYS: frozenset[str] = frozenset(
    {
        "description",
        "docs",
        "changelog",
        "comments",
        "author",
        "license",
    }
)


def _root_key(path: str) -> str:
    return path.split(".", maxsplit=1)[0]


def _is_breaking_path(path: str) -> bool:
    return _root_key(path) in _BREAKING_ROOT_KEYS


def _is_non_breaking_path(path: str) -> bool:
    return _root_key(path) in _NON_BREAKING_ROOT_KEYS


# ---------------------------------------------------------------------------
# Recursive dict differ
# ---------------------------------------------------------------------------


def _diff_contracts(
    pinned: dict[str, Any],
    current: dict[str, Any],
    path: str = "",
) -> list[ModelFieldChange]:
    """Recursively diff two contract dicts and return a flat list of changes."""
    changes: list[ModelFieldChange] = []

    # Fields present in pinned but removed in current — always BREAKING.
    for key, pinned_val in pinned.items():
        if key not in current:
            full_path = f"{path}.{key}" if path else key
            changes.append(
                ModelFieldChange(
                    path=full_path,
                    change_type="removed",
                    old_value=pinned_val,
                    new_value=None,
                    is_breaking=True,
                )
            )

    # Fields added or modified in current.
    for key, current_val in current.items():
        full_path = f"{path}.{key}" if path else key
        if key not in pinned:
            changes.append(
                ModelFieldChange(
                    path=full_path,
                    change_type="added",
                    old_value=None,
                    new_value=current_val,
                    # Adding to a breaking-path root (e.g. algorithm) is breaking.
                    is_breaking=_is_breaking_path(full_path),
                )
            )
        elif pinned[key] != current_val:
            old_val = pinned[key]
            if isinstance(old_val, dict) and isinstance(current_val, dict):
                # Recurse into nested dicts for field-level granularity.
                changes.extend(_diff_contracts(old_val, current_val, full_path))
            else:
                changes.append(
                    ModelFieldChange(
                        path=full_path,
                        change_type="modified",
                        old_value=old_val,
                        new_value=current_val,
                        is_breaking=_is_breaking_path(full_path),
                    )
                )

    return changes


# ---------------------------------------------------------------------------
# Severity determination
# ---------------------------------------------------------------------------


def _determine_severity(
    changes: list[ModelFieldChange],
    sensitivity: EnumDriftSensitivity,
) -> EnumDriftSeverity:
    if not changes:
        return EnumDriftSeverity.NONE

    has_breaking = any(c.is_breaking for c in changes)
    has_additive = any(not c.is_breaking and c.change_type == "added" for c in changes)
    has_non_breaking = any(
        not c.is_breaking and c.change_type in ("modified", "removed") for c in changes
    )

    if has_breaking:
        return EnumDriftSeverity.BREAKING

    if sensitivity == EnumDriftSensitivity.LAX:
        # Only surface breaking changes in LAX mode; anything else is NONE.
        return EnumDriftSeverity.NONE

    if has_additive:
        return EnumDriftSeverity.ADDITIVE

    if sensitivity == EnumDriftSensitivity.STANDARD and not has_non_breaking:
        return EnumDriftSeverity.NONE

    return EnumDriftSeverity.NON_BREAKING


# ---------------------------------------------------------------------------
# Canonical hash
# ---------------------------------------------------------------------------


def compute_canonical_hash(contract: dict[str, Any]) -> str:
    """Produce a stable SHA-256 hex digest of a contract dict.

    Keys are sorted recursively so field ordering in the source YAML does not
    affect the hash.  This mirrors the intent of ProtocolCanonicalSerializer
    without requiring the full ONEX container.

    NOTE: With access to an injected ProtocolCanonicalSerializer instance,
    prefer canonicalize_for_hash() for full volatile-field suppression.
    """
    canonical = json.dumps(contract, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def analyze_drift(drift_input: ModelContractDriftInput) -> ModelContractDriftOutput:
    """Compute a full drift report for a single contract.

    Args:
        drift_input: Current contract, baseline hash, and sensitivity config.

    Returns:
        A complete ModelContractDriftOutput including severity, field-level
        changes, and categorised change summaries.
    """
    current_hash = compute_canonical_hash(drift_input.current_contract)

    if current_hash == drift_input.pinned_hash:
        return ModelContractDriftOutput(
            contract_name=drift_input.contract_name,
            severity=EnumDriftSeverity.NONE,
            current_hash=current_hash,
            pinned_hash=drift_input.pinned_hash,
            drift_detected=False,
            field_changes=[],
            breaking_changes=[],
            additive_changes=[],
            non_breaking_changes=[],
            summary=f"{drift_input.contract_name}: no drift detected",
        )

    # Hashes differ — we need a pinned snapshot to diff against.
    return ModelContractDriftOutput(
        contract_name=drift_input.contract_name,
        severity=EnumDriftSeverity.BREAKING,
        current_hash=current_hash,
        pinned_hash=drift_input.pinned_hash,
        drift_detected=True,
        field_changes=[
            ModelFieldChange(
                path="<contract>",
                change_type="modified",
                old_value=drift_input.pinned_hash,
                new_value=current_hash,
                is_breaking=True,
            )
        ],
        breaking_changes=[
            (
                f"Contract hash changed: "
                f"{drift_input.pinned_hash[:12]}... -> "
                f"{current_hash[:12]}..."
            )
        ],
        additive_changes=[],
        non_breaking_changes=[],
        summary=(
            f"{drift_input.contract_name}: hash drift detected "
            f"(supply pinned contract for field-level analysis)"
        ),
    )


def analyze_drift_with_pinned_contract(
    drift_input: ModelContractDriftInput,
    pinned_contract: dict[str, Any],
) -> ModelContractDriftOutput:
    """Full drift analysis with field-level diff.

    When the caller has stored both the pinned hash AND the pinned contract
    dict, this variant produces a detailed field-by-field report.

    Args:
        drift_input:     Current contract + baseline hash + sensitivity.
        pinned_contract: The contract dict corresponding to pinned_hash.

    Returns:
        Complete drift report with per-field change classification.
    """
    current_hash = compute_canonical_hash(drift_input.current_contract)

    if current_hash == drift_input.pinned_hash:
        return ModelContractDriftOutput(
            contract_name=drift_input.contract_name,
            severity=EnumDriftSeverity.NONE,
            current_hash=current_hash,
            pinned_hash=drift_input.pinned_hash,
            drift_detected=False,
            field_changes=[],
            breaking_changes=[],
            additive_changes=[],
            non_breaking_changes=[],
            summary=f"{drift_input.contract_name}: no drift detected",
        )

    changes = _diff_contracts(pinned_contract, drift_input.current_contract)
    severity = _determine_severity(changes, drift_input.sensitivity)

    breaking = [
        f"{c.change_type.upper()} {c.path}: {c.old_value!r} -> {c.new_value!r}"
        for c in changes
        if c.is_breaking
    ]
    additive = [
        f"ADDED {c.path}: {c.new_value!r}"
        for c in changes
        if not c.is_breaking and c.change_type == "added"
    ]
    non_breaking = [
        f"{c.change_type.upper()} {c.path}: {c.old_value!r} -> {c.new_value!r}"
        for c in changes
        if not c.is_breaking and c.change_type != "added"
    ]

    change_count = len(changes)
    summary = (
        f"{drift_input.contract_name}: {severity.value} drift — "
        f"{change_count} change{'s' if change_count != 1 else ''} "
        f"({len(breaking)} breaking, {len(additive)} additive, "
        f"{len(non_breaking)} non-breaking)"
    )

    return ModelContractDriftOutput(
        contract_name=drift_input.contract_name,
        severity=severity,
        current_hash=current_hash,
        pinned_hash=drift_input.pinned_hash,
        drift_detected=True,
        field_changes=changes,
        breaking_changes=breaking,
        additive_changes=additive,
        non_breaking_changes=non_breaking,
        summary=summary,
    )
