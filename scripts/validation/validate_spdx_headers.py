# SPDX-License-Identifier: MIT
# Copyright (c) 2025 OmniNode Team
#
# Licensed under the MIT License. See LICENSE in the project root for details.
# ----------------------------------------------------------------------------
"""Validate that Python source files contain the required SPDX MIT license header."""

import sys


REQUIRED_SPDX = "SPDX-License-Identifier: MIT"


def check_file(filepath: str) -> bool:
    """Return True if the file has the required SPDX header."""
    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read(512)
        return REQUIRED_SPDX in content
    except (OSError, UnicodeDecodeError):
        return True  # Skip unreadable files


def main() -> int:
    files = sys.argv[1:]
    missing = [f for f in files if not check_file(f)]
    for filepath in missing:
        print(f"Missing SPDX header: {filepath}")
    if missing:
        print(
            f"\n{len(missing)} file(s) missing SPDX header."
            " Add: # SPDX-License-Identifier: MIT"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
