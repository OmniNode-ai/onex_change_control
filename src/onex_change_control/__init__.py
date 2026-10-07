# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""ONEX Change Control - Canonical governance + schema distribution.

This package provides:
- Versioned Pydantic schema models for drift-control artifacts
- Local, no-network validation tooling

Downstream packages can import and use the models directly:
    from onex_change_control import ModelDayClose, ModelTicketContract
    from onex_change_control import ModelGoldenPath, ModelGoldenPathAssertion
    from onex_change_control import ModelGoldenPathInput, ModelGoldenPathOutput
"""

# Do not hardcode versions here; version is sourced from distribution metadata.
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("onex-change-control")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

from onex_change_control.models import (
    ModelDayClose,
    ModelDayOpen,
    ModelGoldenPath,
    ModelGoldenPathAssertion,
    ModelGoldenPathInput,
    ModelGoldenPathOutput,
    ModelTicketContract,
)

__all__ = [
    "ModelDayClose",
    "ModelDayOpen",
    "ModelGoldenPath",
    "ModelGoldenPathAssertion",
    "ModelGoldenPathInput",
    "ModelGoldenPathOutput",
    "ModelTicketContract",
    "__version__",
]
