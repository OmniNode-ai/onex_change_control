# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Governance event emitter for onex_change_control CLI tools.

Emits governance check results to Kafka after CLI runs complete.
Emission is best-effort: if Kafka is unavailable or kafka-python is not
installed, the CLI continues normally without raising.

Topics emitted:
  - onex.evt.onex-change-control.governance-check-completed.v1
  - onex.evt.onex-change-control.drift-detected.v1
  - onex.evt.onex-change-control.cosmetic-compliance-scored.v1

Kafka bootstrap servers are read from the KAFKA_BOOTSTRAP_SERVERS env var.
If not set, defaults to localhost:19092 (local Docker bus).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

TOPIC_GOVERNANCE_CHECK_COMPLETED = (
    "onex.evt.onex-change-control.governance-check-completed.v1"
)
TOPIC_DRIFT_DETECTED = "onex.evt.onex-change-control.drift-detected.v1"
TOPIC_COSMETIC_COMPLIANCE_SCORED = (
    "onex.evt.onex-change-control.cosmetic-compliance-scored.v1"
)

_DEFAULT_BOOTSTRAP = "localhost:19092"


def _get_bootstrap_servers() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", _DEFAULT_BOOTSTRAP)


def _build_envelope(topic: str, payload: dict[str, Any]) -> bytes:
    """Build a JSON-serialized event envelope."""
    envelope = {
        "event_type": topic,
        "event_id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "producer": "onex-change-control",
        "payload": payload,
    }
    return json.dumps(envelope).encode("utf-8")


def _try_produce(topic: str, payload: dict[str, Any]) -> None:
    """Attempt to produce a single Kafka message. Silently no-ops on failure."""
    try:
        from kafka import KafkaProducer  # type: ignore[import-not-found]
    except ImportError:
        return

    try:
        producer = KafkaProducer(
            bootstrap_servers=_get_bootstrap_servers(),
            request_timeout_ms=3000,
            max_block_ms=3000,
        )
        producer.send(topic, _build_envelope(topic, payload))
        producer.flush(timeout=3)
        producer.close()
    except Exception:  # noqa: BLE001
        # Best-effort: never fail the CLI because Kafka is unavailable
        pass


def emit_governance_check_completed(
    *,
    check_type: str,
    target: str,
    passed: bool,
    violation_count: int,
    details: dict[str, Any] | None = None,
) -> None:
    """Emit a governance-check-completed event.

    Args:
        check_type: Type of check run (e.g. 'yaml-validation', 'schema-purity',
                    'cosmetic-lint', 'db-boundary').
        target: Target file or directory that was checked.
        passed: Whether all checks passed.
        violation_count: Number of violations found.
        details: Optional additional context.

    """
    _try_produce(
        TOPIC_GOVERNANCE_CHECK_COMPLETED,
        {
            "check_type": check_type,
            "target": target,
            "passed": passed,
            "violation_count": violation_count,
            "details": details or {},
        },
    )


def emit_drift_detected(
    *,
    ticket_id: str,
    drift_kind: str,
    description: str,
    severity: str = "warning",
) -> None:
    """Emit a drift-detected event.

    Args:
        ticket_id: Ticket or artifact ID associated with the drift.
        drift_kind: Category of drift (e.g. 'schema-mismatch', 'missing-contract').
        description: Human-readable description of the drift.
        severity: Severity level ('warning' | 'error').

    """
    _try_produce(
        TOPIC_DRIFT_DETECTED,
        {
            "ticket_id": ticket_id,
            "drift_kind": drift_kind,
            "description": description,
            "severity": severity,
        },
    )


def emit_cosmetic_compliance_scored(
    *,
    target: str,
    score: float,
    total_checks: int,
    passed_checks: int,
    failed_checks: int,
    violations: list[dict[str, Any]] | None = None,
) -> None:
    """Emit a cosmetic-compliance-scored event.

    Args:
        target: Directory or repo that was linted.
        score: Compliance score 0.0–1.0 (passed / total).
        total_checks: Total number of checks run.
        passed_checks: Number of checks that passed.
        failed_checks: Number of checks with violations.
        violations: Optional list of violation details.

    """
    _try_produce(
        TOPIC_COSMETIC_COMPLIANCE_SCORED,
        {
            "target": target,
            "score": score,
            "total_checks": total_checks,
            "passed_checks": passed_checks,
            "failed_checks": failed_checks,
            "violations": violations or [],
        },
    )


__all__ = [
    "TOPIC_COSMETIC_COMPLIANCE_SCORED",
    "TOPIC_DRIFT_DETECTED",
    "TOPIC_GOVERNANCE_CHECK_COMPLETED",
    "emit_cosmetic_compliance_scored",
    "emit_drift_detected",
    "emit_governance_check_completed",
]
