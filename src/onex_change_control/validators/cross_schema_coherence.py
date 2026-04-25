# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Cross-schema coherence validator (v1 minimal).

Checks that any omnibase_core ModelTicketContract with interfaces_provided
has a corresponding onex_change_control contract with is_seam_ticket: true.

Filename convention: contracts/{TICKET_ID}.yaml is a deliberate invariant.
This validator uses filename-based lookup, not parsed ticket_id search. If
the path convention changes, update this validator.

v1 checks:
- seam contract file exists (contracts/{TICKET_ID}.yaml)
- is_seam_ticket: true in the seam contract

v2 will add:
- ticket_id field match
- interfaces_touched surface overlap
- orphan seam contract detection
- cross-seam surface overlap verification
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path  # noqa: TC003  Why: used at runtime for file reading
from typing import Any

import yaml


class EnumCoherenceLevel(str, Enum):
    """Coherence level for a cross-schema coherence check result."""

    NOT_REQUIRED = "not_required"
    V1_SEAM_PRESENT = "v1_seam_present"
    INCOHERENT = "incoherent"


@dataclass
class CoherenceResult:
    """Result of a cross-schema coherence check."""

    passed: bool
    level: EnumCoherenceLevel
    message: str


class CrossSchemaCoherenceValidator:
    """Validates cross-schema coherence for omnibase_core ticket contracts.

    Checks that onex_change_control seam contracts exist and are marked
    ``is_seam_ticket: true`` for any ticket that declares
    ``interfaces_provided``.

    Uses filename-based lookup: the canonical seam contract path is
    ``contracts/{TICKET_ID}.yaml``. This is a deliberate invariant — if the
    path convention changes, this validator must be updated accordingly.
    """

    def __init__(self, contracts_dir: Path) -> None:
        """Initialise with the directory containing seam contracts."""
        self.contracts_dir = contracts_dir

    def check(
        self, ticket_id: str, omnibase_contract: dict[str, Any]
    ) -> CoherenceResult:
        """Check v1 coherence for a ticket.

        Args:
            ticket_id: The ticket identifier (e.g. "OMN-1234").
            omnibase_contract: The deserialized omnibase_core contract dict.

        Returns:
            CoherenceResult with passed=True when coherent or not required.

        """
        if not omnibase_contract.get("interfaces_provided"):
            return CoherenceResult(
                passed=True,
                level=EnumCoherenceLevel.NOT_REQUIRED,
                message="No interfaces_provided — seam contract not required.",
            )

        seam_path = self.contracts_dir / f"{ticket_id}.yaml"
        if not seam_path.exists():
            return CoherenceResult(
                passed=False,
                level=EnumCoherenceLevel.INCOHERENT,
                message=(
                    f"{ticket_id}: interfaces_provided declared but no seam "
                    f"contract at {seam_path}. Run generate-ticket-contract "
                    "with is_seam_ticket: true."
                ),
            )

        seam_data: dict[str, Any] = yaml.safe_load(seam_path.read_text()) or {}
        if not seam_data.get("is_seam_ticket", False):
            return CoherenceResult(
                passed=False,
                level=EnumCoherenceLevel.INCOHERENT,
                message=(
                    f"{ticket_id}: seam contract exists but is_seam_ticket: "
                    "false. Set is_seam_ticket: true."
                ),
            )

        return CoherenceResult(
            passed=True,
            level=EnumCoherenceLevel.V1_SEAM_PRESENT,
            message=(
                f"{ticket_id}: v1 coherent (seam contract present, "
                "is_seam_ticket: true)."
            ),
        )


__all__ = [
    "CoherenceResult",
    "CrossSchemaCoherenceValidator",
    "EnumCoherenceLevel",
]
