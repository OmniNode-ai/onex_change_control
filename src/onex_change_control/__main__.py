# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Allow ``python -m onex_change_control`` for quick import verification.

When invoked as a module, prints the installed version and available models.
This is useful for verifying the package is importable in a given environment::

    uv run python -m onex_change_control
    # => onex-change-control 0.1.0
    # => Models: ModelDayClose, ModelTicketContract, ...
"""

from __future__ import annotations

import sys


def main() -> None:
    """Print package version and available exports."""
    from onex_change_control import __all__, __version__

    print(f"onex-change-control {__version__}")
    print(f"Models: {', '.join(sorted(__all__))}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
