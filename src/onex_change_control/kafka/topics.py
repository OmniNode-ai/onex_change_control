# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Canonical Kafka topic registry for onex_change_control governance events.

All governance output topics follow ONEX canonical format:
``onex.{kind}.{producer}.{event-name}.v{n}``

This module is the single source of truth for onex_change_control topic names.
No hardcoded topic strings should appear in producer code; use these enum values.

Reference: OMN-8635
"""

from __future__ import annotations

from enum import Enum, unique


@unique
class GovernanceTopic(str, Enum):
    """Canonical Kafka topic names for onex_change_control governance events."""

    GOVERNANCE_CHECK_COMPLETED = (
        "onex.evt.onex-change-control.governance-check-completed.v1"
    )
    """Governance check completed (yaml-validation, schema-purity, etc.)."""

    CONTRACT_DRIFT_DETECTED = "onex.evt.onex-change-control.contract-drift-detected.v1"
    """Contract drift detected between ticket contract and actual artifact."""

    COSMETIC_COMPLIANCE_SCORED = (
        "onex.evt.onex-change-control.cosmetic-compliance-scored.v1"
    )
    """Cosmetic compliance score computed for a directory or repo."""

    def __str__(self) -> str:
        return self.value


__all__ = ["GovernanceTopic"]
