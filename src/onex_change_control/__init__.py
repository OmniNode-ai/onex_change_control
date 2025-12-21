"""ONEX Change Control - Canonical governance + schema distribution.

This package provides:
- Versioned Pydantic schema models for drift-control artifacts
- Deterministic JSON Schema exports for CI consumption
- Local, no-network validation tooling
"""

from typing import Final

from onex_change_control.models import ModelDayClose, ModelTicketContract

__version__: Final[str] = "0.1.0"

__all__ = [
    "ModelDayClose",
    "ModelTicketContract",
    "__version__",
]
