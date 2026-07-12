# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Script canonical-form verdict enum.

Verdict categories for the ``scripts/**`` canonical-form guard (DEFAULT-DENY
policy, OMN-14475). The gate is a deterministic inventory check: a script passes
only if its path is in the frozen baseline (pre-existing debt) or in the
CODEOWNERS-approved exceptions registry (``scripts_exceptions.yaml``, resolved
from onex_change_control@main so ``approved_by != author`` — no self-written
excuse). The AST scorer never decides pass/fail on its own: it makes exactly one
binary hard check — a ``node-backed`` registry claim with no dispatch is a false
claim (BLOCK) — and otherwise only surfaces a LOUD advisory (``logic_advisory``)
when a ``permanent`` entry's score is high, so the CODEOWNERS reviewer, not the
score, decides. CODEOWNERS approval is the authority for a ``permanent`` entry.
"""

from enum import Enum, unique


@unique
class EnumScriptCanonicalVerdict(str, Enum):
    """Verdict for a single ``scripts/**`` file under the deny-new policy.

    Passing:
    - ALLOWLISTED: path is frozen in the baseline (pre-existing debt).
    - EXCEPTION_GRANTED: path has a reviewed exceptions-registry entry, and the
      one node-backed corroboration (dispatch present) holds. A high logic score
      on a ``permanent`` entry is a loud advisory, not a block.

    Blocking:
    - NEW_UNREGISTERED: new file, not in the baseline and not in the registry —
      the default deny. Build it as a CONTRACT+NODE+HANDLER, or add a reviewed
      exceptions-registry entry.
    - FALSE_NODE_BACKED: registry disposition ``node-backed`` but no dispatch
      into the node/handler/runtime substrate is present (unsubstantiated). This
      is a binary, deterministic check — not a fuzzy score.
    """

    ALLOWLISTED = "allowlisted"
    """Path is frozen in the baseline (pre-existing debt, burn-down only)."""

    EXCEPTION_GRANTED = "exception_granted"
    """Path has a reviewed exceptions-registry entry; corroboration holds."""

    NEW_UNREGISTERED = "new_unregistered"
    """New file, not baselined and not in the registry — the default deny."""

    FALSE_NODE_BACKED = "false_node_backed"
    """node-backed disposition but no dispatch into the substrate is present."""

    @classmethod
    def blocking_verdicts(cls) -> frozenset["EnumScriptCanonicalVerdict"]:
        """Return the verdicts that fail the guard (exit non-zero)."""
        return frozenset(
            {
                cls.NEW_UNREGISTERED,
                cls.FALSE_NODE_BACKED,
            }
        )

    @property
    def is_blocking(self) -> bool:
        """Whether this verdict blocks the guard."""
        return self in self.blocking_verdicts()

    def __str__(self) -> str:
        """Return the string value for serialization."""
        return self.value
