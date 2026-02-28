"""ONEX Change Control - Canonical governance + schema distribution.

This package provides:
- Versioned Pydantic schema models for drift-control artifacts
- Local, no-network validation tooling

Downstream packages can import and use the models directly:
    from onex_change_control import ModelDayClose, ModelTicketContract
    from onex_change_control import ModelGoldenPath, ModelGoldenPathAssertion
    from onex_change_control import ModelGoldenPathInput, ModelGoldenPathOutput
"""

from typing import Final

from onex_change_control.models import (
    ModelDayClose,
    ModelGoldenPath,
    ModelGoldenPathAssertion,
    ModelGoldenPathInput,
    ModelGoldenPathOutput,
    ModelTicketContract,
)

__version__: Final[str] = "0.1.0"

__all__ = [
    "ModelDayClose",
    "ModelGoldenPath",
    "ModelGoldenPathAssertion",
    "ModelGoldenPathInput",
    "ModelGoldenPathOutput",
    "ModelTicketContract",
    "__version__",
]
