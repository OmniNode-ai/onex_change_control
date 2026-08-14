# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Script exceptions-registry disposition enum.

The disposition of a reviewed ``scripts/**`` exception entry
(``scripts_exceptions.yaml``) declares WHY a script is allowed to exist outside
the canonical CONTRACT+NODE+HANDLER form, and how it is expected to evolve.
"""

from enum import Enum, unique


@unique
class EnumScriptExceptionDisposition(str, Enum):
    """Why a governed ``scripts/**`` script is granted an exception."""

    NODE_BACKED = "node_backed"
    """Thin shim whose substantive work dispatches to an ONEX node/handler.

    Corroborated by the guard: a NODE_BACKED entry with no dispatch call is a
    false claim and is BLOCKED.
    """

    PERMANENT = "permanent"
    """Provably-cannot-be-a-node glue (CI/deploy/bootstrap/git-hook layer).

    Runs where no ONEX runtime exists yet (a GitHub Actions step, a .201 host
    bring-up before the runtime is up, a first-run provisioner, a git hook). A
    high logic score is surfaced as a loud advisory to the reviewer, not blocked.
    """

    CONVERT = "convert"
    """Logic-bearing script pending conversion to a node (tracked debt).

    Carries a conversion ticket; logic is expected, so the score ceiling does
    not apply. Removed from the registry once the conversion lands.
    """

    def __str__(self) -> str:
        """Return the string value for serialization."""
        return self.value
