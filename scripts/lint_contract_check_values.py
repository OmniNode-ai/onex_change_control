# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""lint_contract_check_values.py -- Pre-commit linter for fail-open patterns in
contract check_value fields.

Fail-open patterns cause DoD checks to pass when they should not:
  - `[ -z "$var" ] ||`  — empty-permissive short-circuit (truthy when var is absent)
  - `|| true`           — always-true tail
  - `|| exit 0`         — explicit pass on error
  - `2>/dev/null` at end of fragment (silenced errors without explicit exit check)

These patterns mask missing or failing gates and produce false positives.
The correct fail-closed form is simply `[ "$result" = "SUCCESS" ]`.

Usage:
    python3 scripts/lint_contract_check_values.py contracts/OMN-1234.yaml [...]

Exits non-zero if any fail-open pattern is found, with a human-readable report.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Anti-pattern registry
# ---------------------------------------------------------------------------

# Each entry: (human_readable_name, compiled_regex).
#
# The empty-permissive pattern matches both bare `$VAR` and brace-wrapped
# `${VAR}` forms because shell writers use them interchangeably.
#
# The 2>/dev/null pattern uses `\Z` (absolute end of string) rather than
# `$` with re.MULTILINE. The MULTILINE form produces false positives on
# multi-line fragments where `2>/dev/null` appears at a line boundary but
# is followed by a valid exit check on the next line.
ANTI_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "empty-permissive [ -z ... ] ||",
        re.compile(
            r'\[\s*-z\s+"?\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)"?\s*\]\s*\|\|'
        ),
    ),
    (
        "trailing || true",
        re.compile(r"\|\|\s*true\b"),
    ),
    (
        "trailing || exit 0",
        re.compile(r"\|\|\s*exit\s+0\b"),
    ),
    (
        "silenced errors 2>/dev/null at end of fragment",
        re.compile(r"2>/dev/null[\s;]*\Z"),
    ),
]


# ---------------------------------------------------------------------------
# Core linting logic
# ---------------------------------------------------------------------------


def lint_contract(path: Path) -> list[tuple[str, str, str]]:
    """Lint a single contract file.

    Returns a list of (path_str, pattern_label, offending_fragment) tuples.
    An empty list means the contract is clean.
    """
    findings: list[tuple[str, str, str]] = []

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return [(str(path), "read-error", str(e))]

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return [(str(path), "yaml-parse-error", str(e))]

    if not isinstance(data, dict):
        return findings

    for item in data.get("dod_evidence", []) or []:
        if not isinstance(item, dict):
            continue

        dod_id = item.get("id", "<unknown>")

        # dod_evidence items nest checks under a `checks` list
        for check in item.get("checks", []) or []:
            if not isinstance(check, dict):
                continue
            value = check.get("check_value", "")
            if not isinstance(value, str) or not value.strip():
                continue
            _scan_value(str(path), dod_id, value, findings)

        # Also handle flat check_value at the item level (legacy schema form)
        flat_value = item.get("check_value", "")
        if isinstance(flat_value, str) and flat_value.strip():
            _scan_value(str(path), dod_id, flat_value, findings)

    return findings


def _scan_value(
    path_str: str,
    dod_id: str,
    value: str,
    findings: list[tuple[str, str, str]],
) -> None:
    """Scan a single check_value string against all anti-patterns."""
    for name, pattern in ANTI_PATTERNS:
        match = pattern.search(value)
        if match:
            # Provide 20-char context window around match
            start = max(0, match.start() - 20)
            end = min(len(value), match.end() + 20)
            fragment = value[start:end].strip()
            findings.append((path_str, f"{dod_id}: {name}", fragment))


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) <= 1:
        print(
            "usage: lint_contract_check_values.py <contract.yaml> [...]",
            file=sys.stderr,
        )
        return 2

    all_findings: list[tuple[str, str, str]] = []
    for arg in argv[1:]:
        all_findings.extend(lint_contract(Path(arg)))

    if all_findings:
        print(
            "FAIL: fail-open patterns found in contract check_value fields:",
            file=sys.stderr,
        )
        for path_str, pattern_label, fragment in all_findings:
            print(f"  {path_str}: {pattern_label}", file=sys.stderr)
            print(f"    ...{fragment}...", file=sys.stderr)
        print(
            "\nFix: replace fail-open guards with fail-closed form, e.g.:\n"
            '  BAD:  [ -z "$result" ] || [ "$result" = "SUCCESS" ]\n'
            '  GOOD: [ "$result" = "SUCCESS" ]',
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
