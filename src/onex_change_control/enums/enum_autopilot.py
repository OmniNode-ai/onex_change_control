# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Autopilot cycle and step status enums."""

from enum import Enum, unique


@unique
class EnumAutopilotStepStatus(str, Enum):
    """Status of a single autopilot pipeline step."""

    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    NOT_RUN = "not_run"

    def __str__(self) -> str:
        return self.value


@unique
class EnumAutopilotCycleStatus(str, Enum):
    """Overall status of an autopilot close-out cycle.

    Closed status set -- no free-form string states.
    """

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    HALTED = "halted"
    CIRCUIT_BREAKER = "circuit_breaker"

    def __str__(self) -> str:
        return self.value
