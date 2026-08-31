# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Shared pytest configuration for ``tests/unit/scripts/``.

Adds the repository-level ``scripts/`` directory to ``sys.path`` once so that
every test module under this package can import scripts under test without
duplicating the path-bootstrap boilerplate.

Path is computed relative to this file so it works on any machine and in any
worktree location.
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/unit/scripts/ -> tests/unit/ -> tests/ -> repo root -> scripts/
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
