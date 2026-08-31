# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Script file-kind enum for the ``scripts/**`` canonical-form guard.

Distinguishes how a governed script is analysed: Python files get full AST
scoring + dispatch detection; shell files get deny-new + text-based dispatch
detection only (no logic-score ceiling — shell is not AST-scorable in v1).
"""

from enum import Enum, unique


@unique
class EnumScriptFileKind(str, Enum):
    """How a governed ``scripts/**`` file is analysed."""

    PYTHON = "python"
    """AST-scored: dispatch detection + logic-score ceiling both apply."""

    SHELL = "shell"
    """Deny-new + text-based dispatch detection; no logic-score ceiling (v1)."""

    def __str__(self) -> str:
        """Return the string value for serialization."""
        return self.value
