# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Pre-commit hook: block ANTHROPIC_API_KEY required checks.

Usage:
    check-anthropic-key-guard [files...]   (via entry point)

Exit code 0 = clean. Exit code 1 = violations found.

Claude Code authenticates via OAuth, not API keys. ANTHROPIC_API_KEY must
never be treated as required. This hook blocks new code that adds required
checks, preflight validations, or required env var tables for this key.

This regression has occurred 6+ times (OMN-7467). This hook is the
permanent guardrail.

Acceptable uses (exempted):
- os.environ.get("ANTHROPIC_API_KEY", "") — optional with empty default
- Commented-out entries in .env.example files
- Log sanitizer regexes that redact the key
- KNOWN_VARS inventories (catalogs, not requirements)
- Demo/example code for direct Anthropic API usage
- Lines with # anthropic-key-ok: <reason> exemption marker

Centralised in onex_change_control so downstream repos consume a single
canonical copy via the ``check-anthropic-key-guard`` entry point.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Patterns that indicate ANTHROPIC_API_KEY is being treated as REQUIRED
# ---------------------------------------------------------------------------

# Required env var checks: raises, exits, or blocks if missing
REQUIRED_CHECK_PATTERNS = [
    # Python: if not os.getenv("ANTHROPIC_API_KEY") / raise / exit
    re.compile(
        r"""(?:if\s+not\s+(?:os\.getenv|os\.environ\.get)\s*\(\s*["']ANTHROPIC_API_KEY["'])"""
    ),
    # Python: os.environ["ANTHROPIC_API_KEY"] (KeyError if missing)
    re.compile(r"""os\.environ\[["']ANTHROPIC_API_KEY["']\]"""),
    # Markdown table row with "required" on same line
    re.compile(r"""\|\s*.*ANTHROPIC_API_KEY.*\|\s*.*[Rr]equired"""),
    # Shell: -z check (empty string test)
    re.compile(r"""\[\s*-z\s+.*ANTHROPIC_API_KEY"""),
    # Shell: required in a missing list
    re.compile(r"""missing.*ANTHROPIC_API_KEY|ANTHROPIC_API_KEY.*missing"""),
    # Generic "Requires: ... ANTHROPIC_API_KEY" in comments/docs
    re.compile(r"""[Rr]equires?:.*ANTHROPIC_API_KEY"""),
]

# Exemption marker: # anthropic-key-ok: <reason>
_EXEMPT_PATTERN = re.compile(r"""(?:#|//|<!--)\s*anthropic-key-ok:\s*\S""")

# Files that are always exempt (sanitizers, inventories, demo code)
EXEMPT_PATH_PATTERNS = [
    re.compile(r"""log_sanitizer"""),
    re.compile(r"""\.env\.example$"""),
    re.compile(r"""env-master-template"""),
    re.compile(r"""validate_env\.py$"""),  # KNOWN_VARS inventory
    re.compile(r"""examples/demo/"""),
    re.compile(r"""check_anthropic_key_guard\.py$"""),  # this script
]


def _is_exempt_path(path: Path) -> bool:
    """Check if the file path is always exempt."""
    path_str = str(path)
    return any(p.search(path_str) for p in EXEMPT_PATH_PATTERNS)


def _check_file(path: Path) -> list[str]:
    """Check a single file for violations. Returns list of violation messages."""
    if _is_exempt_path(path):
        return []

    violations: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    for i, line in enumerate(lines, start=1):
        # Skip exempt lines
        if _EXEMPT_PATTERN.search(line):
            continue
        # Skip commented lines unless they say "required"
        stripped = line.lstrip()
        if (
            stripped.startswith("#")
            and "ANTHROPIC_API_KEY" in line
            and not any(p.search(line) for p in REQUIRED_CHECK_PATTERNS)
        ):
            continue

        for pattern in REQUIRED_CHECK_PATTERNS:
            if pattern.search(line):
                violations.append(
                    f"  {path}:{i}: ANTHROPIC_API_KEY treated as required\n"
                    f"    {line.rstrip()}\n"
                    f"    Claude Code uses OAuth. Remove this requirement.\n"
                    f"    Suppress with: # anthropic-key-ok: <reason>"
                )
                break  # One violation per line is enough

    return violations


def main() -> int:
    """Entry point for pre-commit hook."""
    files = [Path(f) for f in sys.argv[1:] if Path(f).is_file()]

    all_violations: list[str] = []
    for f in files:
        all_violations.extend(_check_file(f))

    if all_violations:
        print(
            "ERROR: ANTHROPIC_API_KEY must NOT be required (OMN-7467)\n"
            "Claude Code authenticates via OAuth, not API keys.\n"
            "This regression has happened 6+ times. See ~/.claude/CLAUDE.md.\n"
        )
        for v in all_violations:
            print(v)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
