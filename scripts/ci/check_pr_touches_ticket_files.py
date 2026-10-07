# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Thin wrapper for the OCC false-Done checker."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


def main() -> int:
    """Import the packaged CLI lazily from the local src tree."""
    from onex_change_control.scripts.check_pr_touches_ticket_files import (
        main as packaged_main,
    )

    return packaged_main()


if __name__ == "__main__":
    raise SystemExit(main())
