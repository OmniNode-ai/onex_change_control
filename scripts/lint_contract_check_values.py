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

Also rejects legacy ``gh pr`` invocations that omit ``${PR_NUMBER}`` or a ``--repo``
argument.  These fail in detached-HEAD CI runs with "could not determine current
branch":

  - ``gh pr checks``          (bare — no PR number)
  - ``gh pr checks --watch``  (bare — no PR number)
  - ``gh pr checks 1430 ...`` (hardcoded integer PR number)
  - ``gh pr view {pr} ...``   (wrong-format ``{x}`` placeholder)

Correct form: ``gh pr checks ${PR_NUMBER} --repo ${REPO}``

Usage:
    python3 scripts/lint_contract_check_values.py contracts/OMN-1234.yaml [...]

Exits non-zero if any fail-open or legacy-gh-pr pattern is found.
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

# Legacy ``gh pr`` patterns that must be rejected.
# These are checked separately from ANTI_PATTERNS because they require
# inspecting the command prefix before applying the regex — a plain regex
# over the full value produces too many false positives on non-gh-pr lines.
#
# Canonical correct form: ``gh pr checks ${PR_NUMBER} --repo ${REPO}``
_GH_PR_PREFIX = ("gh pr checks", "gh pr view", "gh pr diff")

# Hardcoded integer PR number: "gh pr checks 1430 --repo ..."
_HARDCODED_PR_NUMBER_RE = re.compile(r"gh pr (?:checks|view|diff)\s+\d+\s")

# Wrong-format {pr} / {repo} placeholders
_BRACE_PR_RE = re.compile(r"\{pr\}")
_BRACE_REPO_RE = re.compile(r"\{repo\}")


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


def _check_legacy_gh_pr(value: str) -> str | None:
    """Return a human-readable label if *value* is a legacy ``gh pr`` invocation.

    Returns ``None`` when the command is clean (or not a ``gh pr checks/view/diff``
    command at all).

    Legacy forms:
    * Hardcoded integer PR number (e.g. ``gh pr checks 1430 --repo ...``).
    * Wrong-format ``{pr}`` / ``{repo}`` placeholders.
    * Missing ``${PR_NUMBER}`` placeholder.
    * Missing both ``${REPO}`` and a literal ``--repo`` argument.
    """
    stripped = value.strip()
    if not stripped.startswith(_GH_PR_PREFIX):
        return None
    if _HARDCODED_PR_NUMBER_RE.search(stripped):
        return "legacy-gh-pr: hardcoded integer PR number (use ${PR_NUMBER})"
    if _BRACE_PR_RE.search(stripped):
        return "legacy-gh-pr: wrong-format {pr} placeholder (use ${PR_NUMBER})"
    if _BRACE_REPO_RE.search(stripped):
        return "legacy-gh-pr: wrong-format {repo} placeholder (use ${REPO})"
    if "${PR_NUMBER}" not in stripped:
        return "legacy-gh-pr: missing ${PR_NUMBER} placeholder"
    if "${REPO}" not in stripped and "--repo" not in stripped:
        return "legacy-gh-pr: missing --repo argument"
    return None


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

    legacy_label = _check_legacy_gh_pr(value)
    if legacy_label is not None:
        fragment = value.strip()[:80]
        findings.append((path_str, f"{dod_id}: {legacy_label}", fragment))


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
            "FAIL: invalid patterns found in contract check_value fields:",
            file=sys.stderr,
        )
        for path_str, pattern_label, fragment in all_findings:
            print(f"  {path_str}: {pattern_label}", file=sys.stderr)
            print(f"    ...{fragment}...", file=sys.stderr)
        print(
            "\nFix fail-open guards with fail-closed form, e.g.:\n"
            '  BAD:  [ -z "$result" ] || [ "$result" = "SUCCESS" ]\n'
            '  GOOD: [ "$result" = "SUCCESS" ]\n'
            "\nFix legacy gh pr commands with canonical placeholder form, e.g.:\n"
            "  BAD:  gh pr checks 1430 --repo OmniNode-ai/omnibase_infra\n"
            "  BAD:  gh pr checks {pr} --repo {repo}\n"
            "  BAD:  gh pr checks --watch\n"
            "  GOOD: gh pr checks ${PR_NUMBER} --repo ${REPO}\n"
            "\nRun: uv run python scripts/migrate_dod_contracts.py"
            " --apply --tickets <ID>",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
