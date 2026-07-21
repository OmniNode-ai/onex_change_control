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
  - ``gh pr checks 1430 ...`` (hardcoded integer PR number, mixed with ``${PR_NUMBER}``)
  - ``gh pr view {pr} ...``   (wrong-format ``{x}`` placeholder)

Correct form for checking THIS ticket's own PR:
``gh pr checks ${PR_NUMBER} --repo ${REPO}``

Correct form for a genuine, deliberate reference to a DIFFERENT (sibling/dependency)
PR: a standalone hardcoded PR number with a literal ``--repo``, e.g.
``gh pr checks 1721 --repo OmniNode-ai/omnimarket`` — with NO ``${PR_NUMBER}``
anywhere in the same value (see OMN-14431: ``run_contract_compliance_check.py``'s
``_substitute_tokens`` pre-replaces every ``${PR_NUMBER}``/``${REPO}``/``${TICKET_ID}``
occurrence in the WHOLE check_value string with the runner's OWN values before
``sh -c`` ever runs — before any ``VAR=literal`` prefix assignment in the same
string could take effect. So a value like
``PR_NUMBER=1721 REPO=org/repo gh pr checks ${PR_NUMBER} --repo ${REPO}`` looks
like it pins PR 1721, but the ``${PR_NUMBER}`` token is already gone by the time
the shell would apply the assignment: the assignment is inert, and the check
silently runs against whatever PR the runner is evaluating instead of 1721).

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
# Canonical correct form (own PR): ``gh pr checks ${PR_NUMBER} --repo ${REPO}``
# Canonical correct form (genuine cross-PR pin): a standalone hardcoded PR
# number + literal --repo, with NO ${PR_NUMBER} anywhere in the value.
_GH_PR_PREFIX = ("gh pr checks", "gh pr view", "gh pr diff")

# Hardcoded integer PR number: "gh pr checks 1430 --repo ..."
_HARDCODED_PR_NUMBER_RE = re.compile(r"gh pr (?:checks|view|diff)\s+(\d+)\b")

# Wrong-format {pr} / {repo} placeholders
_BRACE_PR_RE = re.compile(r"\{pr\}")
_BRACE_REPO_RE = re.compile(r"\{repo\}")

# OMN-14431: runner-injected tokens that `_substitute_tokens()` pre-replaces
# in the WHOLE check_value string before `sh -c` ever runs. A `VAR=literal`
# prefix assignment sharing the same name as one of these tokens is
# unconditionally inert — the token is gone before the assignment could take
# effect — regardless of whether the command is a `gh pr` invocation.
_RUNNER_INJECTED_VARS = ("PR_NUMBER", "REPO", "TICKET_ID")


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
    * Wrong-format ``{pr}`` / ``{repo}`` placeholders.
    * A hardcoded integer PR number mixed with ``${PR_NUMBER}`` in the same
      value (OMN-14431: ambiguous — the token wins at pre-substitution time,
      silently discarding the literal).
    * Missing both ``${PR_NUMBER}`` and a genuine standalone hardcoded PR
      number.
    * A genuine standalone hardcoded PR number (own-PR checks aside) that
      omits a literal ``--repo`` argument — ``${REPO}`` is NOT accepted here
      because it resolves to the RUNNER's own repo, which is not necessarily
      the repo the pinned PR lives in.
    * Missing both ``${REPO}`` and a literal ``--repo`` argument (own-PR form).

    A standalone hardcoded PR number with NO ``${PR_NUMBER}`` anywhere in the
    value and a literal ``--repo`` argument is the sanctioned, genuinely
    cross-PR form (OMN-14431) — it is executable exactly as written, with no
    runner-side substitution required, so it is accepted.
    """
    stripped = value.strip()
    if not stripped.startswith(_GH_PR_PREFIX):
        return None

    if _BRACE_PR_RE.search(stripped):
        return "legacy-gh-pr: wrong-format {pr} placeholder (use ${PR_NUMBER})"
    if _BRACE_REPO_RE.search(stripped):
        return "legacy-gh-pr: wrong-format {repo} placeholder (use ${REPO})"

    has_hardcoded_pr = bool(_HARDCODED_PR_NUMBER_RE.search(stripped))
    has_pr_token = "${PR_NUMBER}" in stripped

    if has_hardcoded_pr and has_pr_token:
        return (
            "legacy-gh-pr: hardcoded PR number mixed with ${PR_NUMBER} in the "
            "same command is ambiguous — ${PR_NUMBER} is pre-substituted with "
            "the runner's own PR before the literal could ever apply; use "
            "EITHER a standalone hardcoded cross-PR reference (no ${PR_NUMBER} "
            "anywhere in the value) OR ${PR_NUMBER} alone, never both"
        )

    if has_hardcoded_pr:
        # Genuine, standalone cross-PR reference — must be executable exactly
        # as written, so --repo must be a literal (not ${REPO}, which would
        # resolve to the runner's own repo, not necessarily the pinned PR's).
        if "--repo" not in stripped or "${REPO}" in stripped:
            return (
                "legacy-gh-pr: hardcoded cross-PR reference requires a "
                "literal --repo argument (${REPO} resolves to the runner's "
                "own repo, not necessarily the pinned PR's repo)"
            )
        return None

    if not has_pr_token:
        return (
            "legacy-gh-pr: missing ${PR_NUMBER} placeholder or a genuine "
            "standalone hardcoded PR number"
        )
    if "${REPO}" not in stripped and "--repo" not in stripped:
        return "legacy-gh-pr: missing --repo argument"
    return None


def _check_inert_token_prefix(value: str) -> str | None:
    """Return a label if *value* contains an inert ``VAR=literal`` prefix.

    OMN-14431: ``_substitute_tokens()`` in ``run_contract_compliance_check.py``
    replaces every ``${PR_NUMBER}`` / ``${REPO}`` / ``${TICKET_ID}`` occurrence
    in the WHOLE check_value string with the runner's own values BEFORE
    ``sh -c`` is ever invoked — i.e. before any ``VAR=literal`` prefix
    assignment in the same string could take effect. A fragment like
    ``PR_NUMBER=1721 ... ${PR_NUMBER}`` therefore looks like it pins PR 1721,
    but the ``${PR_NUMBER}`` token is already gone by the time the shell would
    apply the assignment: the assignment is dead decoration and the check
    silently runs against whatever PR the runner is evaluating instead of the
    literal 1721. This is NOT limited to ``gh pr`` commands — it applies to
    any command referencing these three runner-injected token names.
    """
    for var in _RUNNER_INJECTED_VARS:
        if re.search(rf"\b{var}=\S", value) and f"${{{var}}}" in value:
            return (
                f"inert-token-prefix: {var}=<literal> prefix is silently "
                f"discarded because ${{{var}}} is pre-substituted with the "
                "runner's own value before the shell ever sees the "
                "assignment take effect — the check runs against the "
                "runner's value, not the literal"
            )
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

    inert_label = _check_inert_token_prefix(value)
    if inert_label is not None:
        fragment = value.strip()[:80]
        findings.append((path_str, f"{dod_id}: {inert_label}", fragment))


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
            "  BAD:  gh pr checks {pr} --repo {repo}\n"
            "  BAD:  gh pr checks --watch\n"
            "  GOOD (own PR):       gh pr checks ${PR_NUMBER} --repo ${REPO}\n"
            "  GOOD (cross-PR pin): gh pr checks 1721 --repo OmniNode-ai/omnimarket"
            "  (standalone hardcoded PR + literal --repo, NO ${PR_NUMBER}"
            " anywhere in the value)\n"
            "  BAD (OMN-14431):     PR_NUMBER=1721 REPO=org/repo gh pr checks"
            " ${PR_NUMBER} --repo ${REPO}  (the ${PR_NUMBER} token is"
            " pre-substituted with the runner's OWN PR before the assignment"
            " could ever apply -- the 1721 literal is silently discarded)\n"
            "\nRun: uv run python scripts/migrate_dod_contracts.py"
            " --apply --tickets <ID>",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
