# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Pre-commit hook: reject bare feature flag env var reads.

Usage:
    check-bare-feature-flags [files...]   (via entry point)
    python -m onex_change_control.scripts.check_bare_feature_flags [files...]

Exit code 0 = clean. Exit code 1 = violations found.

Bare ``os.getenv("ENABLE_*")``, ``os.environ.get("ENABLE_*")``,
``os.environ["ENABLE_*"]``, ``process.env.ENABLE_*`` reads (and the
``*_ENABLED`` suffix variant) must be replaced with contract-declared
feature flags resolved via the flag system.

Centralised in onex_change_control (OMN-5573) so downstream repos consume
a single canonical copy via the ``check-bare-feature-flags`` entry point.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Python getenv / environ.get / environ[] with ENABLE_ prefix or _ENABLED suffix
PYTHON_ENABLE_PATTERN = re.compile(
    r"""(?:os\.getenv|os\.environ\.get|os\.environ\[)\s*\(?\s*["'](ENABLE_\w+|[A-Z_]*_ENABLED)["']"""
)

# TypeScript / JavaScript: process.env.ENABLE_* / process.env.*_ENABLED
TS_ENABLE_PATTERN = re.compile(r"""process\.env\.(ENABLE_\w+|[A-Z_]*_ENABLED)""")

# Exemption marker with mandatory reason token.
_EXEMPT_WITH_REASON = re.compile(r"""(?:#|//)\s*ONEX_FLAG_EXEMPT:\s*(\S.*)""")
_EXEMPT_BARE = re.compile(r"""(?:#|//)\s*ONEX_FLAG_EXEMPT:""")

# ---------------------------------------------------------------------------
# Approved files / paths
# ---------------------------------------------------------------------------

APPROVED_BASENAMES: frozenset[str] = frozenset(
    {
        "feature_flag_resolver.py",
        "contract.yaml",
        "check_bare_feature_flags.py",
        "model_contract_feature_flag.py",
    }
)

APPROVED_PATH_SEGMENTS: tuple[str, ...] = (
    "/capabilities/",
    "/config_discovery/",
)

_COMMENT_PREFIXES = ("#", "//")


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------


@dataclass
class _ScanResult:
    violations: list[str] = field(default_factory=list)
    exemptions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_test_file(path: str) -> bool:
    """Return True if *path* looks like a test file or test directory."""
    basename = Path(path).name
    if basename.startswith("test_") or basename.endswith(
        ("_test.py", ".test.ts", ".test.js")
    ):
        return True
    return "/tests/" in path or path.startswith("tests/") or "/__tests__/" in path


def _is_approved_path(path: str) -> bool:
    basename = Path(path).name
    if basename in APPROVED_BASENAMES:
        return True
    if _is_test_file(path):
        return True
    return any(seg in path for seg in APPROVED_PATH_SEGMENTS)


def _is_comment_line(line: str) -> bool:
    stripped = line.lstrip()
    return any(stripped.startswith(p) for p in _COMMENT_PREFIXES)


def _extract_flag_name(line: str) -> str | None:
    """Return the flag name from a matching line, or None."""
    m = PYTHON_ENABLE_PATTERN.search(line)
    if m:
        return m.group(1)
    m = TS_ENABLE_PATTERN.search(line)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Core scan
# ---------------------------------------------------------------------------


def _scan_lines(text: str, path: str) -> _ScanResult:
    result = _ScanResult()

    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_comment_line(line):
            continue

        flag_name = _extract_flag_name(line)
        if flag_name is None:
            continue

        # Check for exemption marker.
        exempt_reason = _EXEMPT_WITH_REASON.search(line)
        exempt_bare = _EXEMPT_BARE.search(line)

        if exempt_reason:
            reason = exempt_reason.group(1).strip()
            if reason:
                result.exemptions.append(f"{path}:{lineno} [{reason}]")
                continue

        if exempt_bare:
            result.violations.append(
                f"{path}:{lineno}: ONEX_FLAG_EXEMPT without reason token"
                " -- add a reason after the colon"
            )
            continue

        result.violations.append(
            f"{path}:{lineno}: bare feature flag env var"
            f' "{flag_name}"'
            " -- declare in contract.yaml feature_flags: block"
            " and resolve via flag system"
        )

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_file(path: str) -> list[str]:
    """Return violation messages for a single file."""
    if _is_approved_path(path):
        return []

    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    return _scan_lines(text, path).violations


def check_file_full(path: str) -> _ScanResult:
    """Return full scan result (violations + exemptions) for a single file."""
    if _is_approved_path(path):
        return _ScanResult()

    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _ScanResult()

    return _scan_lines(text, path)


def main() -> int:
    """Entry point for pre-commit hook. Accepts file paths as arguments."""
    files = sys.argv[1:]
    all_violations: list[str] = []
    all_exemptions: list[str] = []

    for path in files:
        result = check_file_full(path)
        all_violations.extend(result.violations)
        all_exemptions.extend(result.exemptions)

    if all_violations:
        for v in all_violations:
            print(v)

        exemption_summary = ""
        if all_exemptions:
            reasons = ", ".join(all_exemptions)
            exemption_summary = f", {len(all_exemptions)} exemption(s) ({reasons})"

        print(
            f"\n{len(all_violations)} violation(s){exemption_summary}."
            " Declare flags in contract.yaml feature_flags: block"
            " and resolve via the flag system."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
