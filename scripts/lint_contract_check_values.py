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
# OMN-15382 Rule A: executable-command-shape
#
# `check_type: command` values are handed verbatim to `sh -c` by
# `contract_compliance_check._check_command`. A value that OPENS with English
# prose describing what was run ("Recorded product receipt: uv run pytest ...")
# is not a command at all -- `sh` fails on the first word with "command not
# found" (exit 127), so the check always BLOCKs when actually executed. This
# was the root defect behind OMN-15382 (3/7 dod_verify failures on
# contracts/OMN-14968.yaml): four items were authored as human-readable
# descriptions of a command that had already been run by hand, never as an
# executable command a hosted runner can re-run.
#
# The rule: strip any leading simple `VAR=literal` (no internal whitespace,
# no `$()`/quoting) env-assignment tokens, then require the first remaining
# token to be either a recognized command head or a POSIX shell
# control-flow/builtin keyword that can legitimately open a compound
# fragment (`if`, `case`, `for`, `!`, `[`, `:`, ...). A value that opens with
# `IDENTIFIER=` but whose value is NOT a bare literal (a command-substitution
# or quoted assignment, e.g. ``state=$(gh pr view ...)`` or
# ``f="$(mktemp)" && ...``) is a legitimate multi-statement shell fragment
# and is exempted rather than mis-tokenized by a non-shell-aware parser.
#
# Extending the allowlist requires touching this file -- a deliberate
# ratchet (mirrors the OMN-9350 anti-pattern registry above).
# ---------------------------------------------------------------------------

_COMMAND_HEAD_ALLOWLIST: frozenset[str] = frozenset(
    {
        "gh",
        "git",
        "uv",
        "npm",
        "npx",
        "python",
        "python3",
        "bash",
        "sh",
        "pytest",
        "docker",
        "pre-commit",
        "rg",
        "grep",
        "find",
        "echo",
        "printf",
        "jq",
        "base64",
        "sha256sum",
        "cat",
        "ls",
        "cd",
        "env",
        "curl",
        "mktemp",
        "diff",
        "cmp",
        "sort",
        "cut",
        "sed",
        "awk",
        "tr",
        "wc",
        "head",
        "tail",
        "xargs",
        "mkdir",
        "rm",
        "touch",
        "true",
        "false",
        "ssh",
        "shellcheck",
    }
)

# POSIX shell control-flow / builtin tokens that can legitimately be the
# FIRST word of a `sh -c` fragment (compound commands, negation, grouping,
# the `test`/`:` builtins). These are not "commands" in the allowlist sense
# above but are unambiguously executable shell syntax, not prose.
_CONTROL_KEYWORD_HEADS: frozenset[str] = frozenset(
    {"if", "for", "while", "until", "case", "test", "!", "[", "{", "(", ":"}
)

# A leading `IDENTIFIER=` at the very start of the (stripped) value -- used
# to detect an assignment-prefixed fragment. Matched BEFORE and AFTER the
# literal-prefix strip: if it still matches after stripping, the assignment
# value was non-literal (command substitution / quoted) and the whole value
# is exempted rather than mis-tokenized.
_ENV_ASSIGN_HEAD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Repeated simple `VAR=literal` prefixes: value has no internal whitespace,
# no `$`, and no quote characters (a bare literal, e.g. `PR_NUMBER=1721`).
_LITERAL_ENV_PREFIX_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=[^\s;\"'$]*[\s;]+)+")


def _command_head_violation(value: str) -> str | None:
    """Return a label if *value* does not open with an executable command.

    Returns ``None`` when the value is clean (or not evaluable by this
    simple, non-shell-aware heuristic -- see module docstring above).
    """
    stripped = value.strip()
    if not stripped:
        return None

    m = _LITERAL_ENV_PREFIX_RE.match(stripped)
    rest = stripped[m.end() :] if m else stripped

    if _ENV_ASSIGN_HEAD_RE.match(rest):
        # Non-literal assignment prefix (command substitution / quoted) --
        # a legitimate multi-statement shell fragment. Exempt: prose never
        # opens with `IDENTIFIER=`.
        return None

    parts = rest.split()
    if not parts:
        return None
    first = parts[0]

    if first in _CONTROL_KEYWORD_HEADS or first in _COMMAND_HEAD_ALLOWLIST:
        return None

    # Subshell grouping / negation directly concatenated with the next word,
    # e.g. ``(gh pr view ...)`` with no space after the paren.
    if first[:1] in {"(", "!"} and (
        first[1:] in _COMMAND_HEAD_ALLOWLIST or len(first) == 1
    ):
        return None

    if first.endswith(":"):
        return (
            f"executable-command-shape: check_value opens with a prose label "
            f"({first!r}), not an executable command -- check_values are handed "
            "verbatim to `sh -c` and a prose opener always fails with "
            "'command not found' when actually run (OMN-15382)"
        )
    return (
        f"executable-command-shape: first token {first!r} is not a recognized "
        "command head or shell control keyword -- check_values must be "
        "literal, executable shell commands, not a description of a command "
        "already run by hand (OMN-15382)"
    )


# ---------------------------------------------------------------------------
# OMN-15382 Rule B: per-item PR binding
#
# A dod_evidence `id` that embeds a PR number (``pr-(\d+)``) declares intent
# to pin evidence to THAT specific PR. If every `gh pr view/checks/diff` call
# in its check_values uses only the bare ``${PR_NUMBER}`` runner placeholder
# -- never the literal number from the id -- the check silently re-targets
# whatever PR the compliance runner happens to be evaluating (the OMN-15382
# class-3 PR-number mis-binding defect: contracts/OMN-14968.yaml's
# `dod-OmniNode-ai-omnibase_infra-pr-2536` item used exactly this bare-token
# form and never actually pinned PR #2536).
# ---------------------------------------------------------------------------

_ITEM_ID_PR_RE = re.compile(r"pr-(\d+)")
_GH_PR_CALL_RE = re.compile(r"gh pr (?:view|checks|diff)\b")


def _is_generated_product_ci_item(dod_id: str) -> bool:
    """Return true for the generated product diff-scope companion item.

    The OCC companion producers intentionally keep the product ``-ci`` item in
    runner-placeholder form so public and private product repos share one hosted
    contract vocabulary. Rule B still applies to self-bind and non-CI
    PR-numbered evidence items, where a placeholder would silently target the
    OCC PR rather than the cited product PR.
    """
    return dod_id.startswith("dod-") and "-pr-" in dod_id and dod_id.endswith("-ci")


def _pr_binding_violation(dod_id: str, values: list[str]) -> str | None:
    """Return a label if a PR-numbered item never literally pins that PR."""
    match = _ITEM_ID_PR_RE.search(dod_id)
    if not match:
        return None
    if _is_generated_product_ci_item(dod_id):
        return None
    pr_number = match.group(1)

    gh_pr_values = [v for v in values if _GH_PR_CALL_RE.search(v)]
    if not gh_pr_values:
        return None

    for value in gh_pr_values:
        if pr_number in value:
            return None

    return (
        f"pr-binding: id {dod_id!r} embeds PR #{pr_number} but no "
        "`gh pr view/checks/diff` check_value in this item references that "
        "literal number -- a bare ${PR_NUMBER} placeholder resolves to "
        "whatever PR the compliance runner is evaluating, not the pinned PR "
        "(OMN-15382 class-3 mis-binding)"
    )


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

    dod_evidence = data.get("dod_evidence", []) or []
    superseded = _superseded_dod_ids(dod_evidence)

    for item in dod_evidence:
        if not isinstance(item, dict):
            continue

        raw_dod_id = item.get("id", "<unknown>")
        dod_id = raw_dod_id if isinstance(raw_dod_id, str) else "<unknown>"
        if dod_id in superseded:
            continue
        _scan_dod_item(str(path), item, dod_id, findings)

    return findings


def _scan_dod_item(
    path_label: str,
    item: dict[object, object],
    dod_id: str,
    findings: list[tuple[str, str, str]],
) -> None:
    # dod_evidence items nest checks under a `checks` list.
    checks = item.get("checks", [])
    if not isinstance(checks, list):
        checks = []

    command_values: list[str] = []
    all_values: list[str] = []

    for check in checks:
        if not isinstance(check, dict):
            continue
        value = check.get("check_value", "")
        if not isinstance(value, str) or not value.strip():
            continue
        _scan_value(path_label, dod_id, value, findings)
        all_values.append(value)
        if check.get("check_type", "command") == "command":
            command_values.append(value)

    # Also handle flat check_value at the item level (legacy schema form).
    # The legacy flat form predates check_type and was always a shell command.
    flat_value = item.get("check_value", "")
    if isinstance(flat_value, str) and flat_value.strip():
        _scan_value(path_label, dod_id, flat_value, findings)
        all_values.append(flat_value)
        command_values.append(flat_value)

    # OMN-15382 Rule A: every command-shaped check_value must open with an
    # executable command, not prose describing one.
    for value in command_values:
        label = _command_head_violation(value)
        if label is not None:
            fragment = value.strip()[:80]
            findings.append((path_label, f"{dod_id}: {label}", fragment))

    # OMN-15382 Rule B: a dod_evidence id embedding a PR number must pin that
    # literal number in every gh pr view/checks/diff call it makes.
    binding_label = _pr_binding_violation(dod_id, all_values)
    if binding_label is not None:
        findings.append((path_label, binding_label, dod_id))


def _superseded_dod_ids(dod_evidence: list[object]) -> set[str]:
    """Return ids superseded by later append-only replacement items."""
    seen: set[str] = set()
    superseded: set[str] = set()
    for item in dod_evidence:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        artifact = item.get("evidence_artifact")
        if isinstance(artifact, str):
            prefix = "supersedes_dod_evidence:"
            if artifact.startswith(prefix):
                target = artifact[len(prefix) :].strip()
                if target in seen:
                    superseded.add(target)
        if isinstance(item_id, str):
            seen.add(item_id)
    return superseded


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
