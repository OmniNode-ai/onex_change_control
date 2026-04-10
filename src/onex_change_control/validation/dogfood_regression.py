# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Dogfood Scorecard Regression Detector.

Compares a current ModelDogfoodScorecard against one or more prior runs
and emits a list of ModelDogfoodRegression entries.

Rules (from OMN-7699 spec):
- Healthy chain drops to 0 rows → CRITICAL
- Endpoint previously had data, now returns empty → WARN
- Delegation classifier coverage drops → WARN
- Row count drops more than 20% without a known cause → WARN

This module is pure: no I/O, no env reads, no time calls.
"""

from onex_change_control.enums.enum_dogfood_status import (
    EnumDogfoodStatus,
    EnumRegressionSeverity,
)
from onex_change_control.models.model_dogfood_scorecard import (
    ModelDogfoodRegression,
    ModelDogfoodScorecard,
)

_ROW_COUNT_DROP_THRESHOLD = 0.20  # 20% drop triggers WARN


def detect_regressions(
    current: ModelDogfoodScorecard,
    previous: ModelDogfoodScorecard,
) -> list[ModelDogfoodRegression]:
    """Compare current scorecard against a previous run and return regressions.

    Args:
        current: The newly captured scorecard.
        previous: The most recent prior scorecard to compare against.

    Returns:
        A list of ModelDogfoodRegression entries. Empty if no regressions.
    """
    regressions: list[ModelDogfoodRegression] = []

    regressions.extend(_check_golden_chains(current, previous))
    regressions.extend(_check_endpoints(current, previous))
    regressions.extend(_check_delegation(current, previous))

    return regressions


def _check_golden_chains(
    current: ModelDogfoodScorecard,
    previous: ModelDogfoodScorecard,
) -> list[ModelDogfoodRegression]:
    """Detect golden chain row count regressions."""
    regressions: list[ModelDogfoodRegression] = []

    prev_chains = {c.chain_name: c for c in previous.golden_chains}

    for chain in current.golden_chains:
        prev = prev_chains.get(chain.chain_name)
        if prev is None:
            continue

        # Healthy chain drops to 0 rows → CRITICAL
        if (
            prev.status == EnumDogfoodStatus.PASS
            and prev.row_count > 0
            and chain.row_count == 0
        ):
            regressions.append(
                ModelDogfoodRegression(
                    dimension="golden_chains",
                    field_path=f"golden_chains[{chain.chain_name}].row_count",
                    severity=EnumRegressionSeverity.CRITICAL,
                    previous_value=str(prev.row_count),
                    current_value="0",
                    description=(
                        f"Chain '{chain.chain_name}' was healthy with"
                        f" {prev.row_count} rows but now has 0 rows"
                        f" in table '{chain.table}'."
                    ),
                )
            )
            continue

        # Row count drops more than 20% → WARN
        if prev.row_count > 0 and chain.row_count < prev.row_count:
            drop_fraction = (prev.row_count - chain.row_count) / prev.row_count
            if drop_fraction > _ROW_COUNT_DROP_THRESHOLD:
                regressions.append(
                    ModelDogfoodRegression(
                        dimension="golden_chains",
                        field_path=f"golden_chains[{chain.chain_name}].row_count",
                        severity=EnumRegressionSeverity.WARN,
                        previous_value=str(prev.row_count),
                        current_value=str(chain.row_count),
                        description=(
                            f"Chain '{chain.chain_name}' row count dropped"
                            f" {drop_fraction:.1%} (from {prev.row_count}"
                            f" to {chain.row_count})"
                            f" in table '{chain.table}'."
                        ),
                    )
                )

    return regressions


def _check_endpoints(
    current: ModelDogfoodScorecard,
    previous: ModelDogfoodScorecard,
) -> list[ModelDogfoodRegression]:
    """Detect endpoint data presence regressions."""
    regressions: list[ModelDogfoodRegression] = []

    prev_endpoints = {e.path: e for e in previous.endpoints}

    for endpoint in current.endpoints:
        prev = prev_endpoints.get(endpoint.path)
        if prev is None:
            continue

        # Endpoint previously returned data, now returns empty → WARN
        if prev.has_data and not endpoint.has_data:
            regressions.append(
                ModelDogfoodRegression(
                    dimension="endpoints",
                    field_path=f"endpoints[{endpoint.path}].has_data",
                    severity=EnumRegressionSeverity.WARN,
                    previous_value="true",
                    current_value="false",
                    description=(
                        f"Endpoint '{endpoint.path}' previously returned"
                        f" data but now returns an empty response"
                        f" (HTTP {endpoint.http_code})."
                    ),
                )
            )

    return regressions


def _check_delegation(
    current: ModelDogfoodScorecard,
    previous: ModelDogfoodScorecard,
) -> list[ModelDogfoodRegression]:
    """Detect delegation classifier coverage regressions."""
    regressions: list[ModelDogfoodRegression] = []

    if current.delegation is None or previous.delegation is None:
        return regressions

    prev_pct = previous.delegation.classifier_coverage_pct
    curr_pct = current.delegation.classifier_coverage_pct

    if curr_pct < prev_pct:
        regressions.append(
            ModelDogfoodRegression(
                dimension="delegation",
                field_path="delegation.classifier_coverage_pct",
                severity=EnumRegressionSeverity.WARN,
                previous_value=f"{prev_pct:.1f}",
                current_value=f"{curr_pct:.1f}",
                description=(
                    f"Delegation classifier coverage dropped from "
                    f"{prev_pct:.1f}% to {curr_pct:.1f}%."
                ),
            )
        )

    return regressions
