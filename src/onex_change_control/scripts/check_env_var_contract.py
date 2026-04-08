# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Pre-commit hook: enforce env var contract (allowlist + blocklist).

Usage:
    check-env-var-contract [files...]   (via entry point)

Exit code 0 = clean. Exit code 1 = violations found.

Scans files for patterns that treat environment variables as required
(``os.environ["X"]``, ``os.getenv("X")`` without default, ``process.env.X``
in conditionals, etc.) and validates them against the env contract:

- **allowed_required**: sanctioned vars that may be required
- **blocked**: vars that must NEVER be required (e.g. ANTHROPIC_API_KEY)
- **unlisted**: vars not in either list trigger a warning with instructions

The contract lives in ``env_contract.yaml`` at the repo root.

Suppress a specific line with: ``# env-contract-ok: <reason>``

Centralised in onex_change_control so downstream repos consume a single
canonical copy via the ``check-env-var-contract`` entry point.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Patterns that indicate a REQUIRED env var read
# ---------------------------------------------------------------------------

# Python: os.environ["VAR"] — KeyError if missing
_PY_ENVIRON_BRACKET = re.compile(r"""os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]""")

# Python: os.getenv("VAR") or os.environ.get("VAR") WITHOUT a second arg
# Matches os.getenv("X") and os.environ.get("X") but NOT os.getenv("X", "")
_PY_GETENV_NO_DEFAULT = re.compile(
    r"""(?:os\.getenv|os\.environ\.get)\s*\(\s*["']([A-Z][A-Z0-9_]*)["']\s*\)"""
)

# Python: os.getenv("VAR", ...) or os.environ.get("VAR", ...) WITH a default
# Used to distinguish optional reads
_PY_GETENV_WITH_DEFAULT = re.compile(
    r"""(?:os\.getenv|os\.environ\.get)\s*\(\s*["']([A-Z][A-Z0-9_]*)["']\s*,"""
)

# TypeScript/JavaScript: process.env.VAR (always potentially undefined)
_TS_PROCESS_ENV = re.compile(r"""process\.env\.([A-Z][A-Z0-9_]*)""")

# Markdown: | `VAR` | ... | Required ... | (table row)
_MD_REQUIRED_TABLE = re.compile(r"""\|\s*`?([A-Z][A-Z0-9_]*)`?\s*\|.*[Rr]equired""")

# Shell: ${VAR:?error} or ${VAR?error} — fail if unset
_SH_REQUIRED_PARAM = re.compile(r"""\$\{([A-Z][A-Z0-9_]*)[?:][?]""")

# Exemption marker
_EXEMPT_PATTERN = re.compile(r"""(?:#|//|<!--)\s*env-contract-ok:\s*\S""")

# Comment prefixes
_COMMENT_PREFIXES = ("#", "//", "<!--", "*")

# Paths always exempt from scanning
_EXEMPT_PATH_PATTERNS = (
    re.compile(r"""\.env\.example$"""),
    re.compile(r"""\.env$"""),
    re.compile(r"""env-master-template"""),
    re.compile(r"""validate_env\.py$"""),
    re.compile(r"""log_sanitizer"""),
    re.compile(r"""check_env_var_contract\.py$"""),
    re.compile(r"""env_contract\.yaml$"""),
    re.compile(r"""test_check_env_var_contract"""),
)


# ---------------------------------------------------------------------------
# Contract loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvContract:
    """Parsed env var contract."""

    allowed: frozenset[str]
    blocked: dict[str, str]  # var -> reason


def load_contract(contract_path: Path | None = None) -> EnvContract:
    """Load env_contract.yaml from the repo root or given path."""
    if contract_path is None:
        # Walk up from this file to find env_contract.yaml
        candidates = [
            Path(__file__).parent.parent.parent.parent.parent / "env_contract.yaml",
            Path.cwd() / "env_contract.yaml",
        ]
        for c in candidates:
            if c.exists():
                contract_path = c
                break

    if contract_path is None or not contract_path.exists():
        # No contract found — only enforce the hardcoded blocklist
        return EnvContract(
            allowed=frozenset(),
            blocked={
                "ANTHROPIC_API_KEY": "Claude Code uses OAuth, not API keys (OMN-7467)"
            },
        )

    with contract_path.open() as f:
        data = yaml.safe_load(f)

    allowed = frozenset(entry["var"] for entry in (data.get("allowed_required") or []))
    blocked = {
        entry["var"]: entry.get("reason", "blocked by env contract")
        for entry in (data.get("blocked") or [])
    }

    return EnvContract(allowed=allowed, blocked=blocked)


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------


@dataclass
class Violation:
    """A single env var contract violation."""

    path: str
    lineno: int
    var_name: str
    line_text: str
    kind: str  # "blocked", "unlisted"
    reason: str


@dataclass
class ScanResult:
    """Full scan result for a file."""

    violations: list[Violation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core scanning
# ---------------------------------------------------------------------------


def _is_exempt_path(path: str) -> bool:
    return any(p.search(path) for p in _EXEMPT_PATH_PATTERNS)


def _is_comment_line(line: str) -> bool:
    stripped = line.lstrip()
    return any(stripped.startswith(p) for p in _COMMENT_PREFIXES)


def _extract_required_vars(line: str) -> list[str]:
    """Extract var names from required-env-var patterns in a line."""
    vars_found: list[str] = []

    # Check bracket access first (always required)
    for m in _PY_ENVIRON_BRACKET.finditer(line):
        vars_found.append(m.group(1))

    # Check getenv without default (required pattern)
    for m in _PY_GETENV_NO_DEFAULT.finditer(line):
        var = m.group(1)
        # Skip if this same var also appears with a default on this line
        if _PY_GETENV_WITH_DEFAULT.search(line) and var in line:
            # More precise: check if the match position overlaps with a default
            has_default = any(
                dm.group(1) == var for dm in _PY_GETENV_WITH_DEFAULT.finditer(line)
            )
            if has_default:
                continue
        vars_found.append(var)

    # Check TS process.env in conditional patterns
    for m in _TS_PROCESS_ENV.finditer(line):
        vars_found.append(m.group(1))

    # Check markdown required tables
    md_match: re.Match[str] | None = _MD_REQUIRED_TABLE.search(line)
    if md_match:
        vars_found.append(md_match.group(1))

    # Check shell required params
    for m in _SH_REQUIRED_PARAM.finditer(line):
        vars_found.append(m.group(1))

    return vars_found


def scan_file(path: str, contract: EnvContract) -> ScanResult:
    """Scan a single file for env var contract violations."""
    result = ScanResult()

    if _is_exempt_path(path):
        return result

    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return result

    for lineno, line in enumerate(lines, start=1):
        # Skip exempt lines
        if _EXEMPT_PATTERN.search(line):
            continue

        # Skip pure comment lines (unless they contain required-table patterns)
        if _is_comment_line(line) and not _MD_REQUIRED_TABLE.search(line):
            continue

        required_vars = _extract_required_vars(line)

        for var in required_vars:
            if var in contract.blocked:
                result.violations.append(
                    Violation(
                        path=path,
                        lineno=lineno,
                        var_name=var,
                        line_text=line.rstrip(),
                        kind="blocked",
                        reason=contract.blocked[var],
                    )
                )
            elif var not in contract.allowed:
                result.violations.append(
                    Violation(
                        path=path,
                        lineno=lineno,
                        var_name=var,
                        line_text=line.rstrip(),
                        kind="unlisted",
                        reason=(
                            f"{var} is not in env_contract.yaml allowed_required. "
                            f"Add it there with a reason, or use an optional read "
                            f"with a default value."
                        ),
                    )
                )
            # If var is in allowed: no violation

    return result


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _format_violation(v: Violation) -> str:
    if v.kind == "blocked":
        return (
            f"  BLOCKED: {v.path}:{v.lineno}: {v.var_name}\n"
            f"    {v.line_text}\n"
            f"    Reason: {v.reason}\n"
            f"    Fix: Remove the required check. Use optional read with default.\n"
            f"    Suppress: # env-contract-ok: <reason>"
        )
    return (
        f"  UNLISTED: {v.path}:{v.lineno}: {v.var_name}\n"
        f"    {v.line_text}\n"
        f"    {v.reason}\n"
        f"    Suppress: # env-contract-ok: <reason>"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Entry point for pre-commit hook."""
    files = sys.argv[1:]
    contract = load_contract()

    blocked_violations: list[Violation] = []
    unlisted_violations: list[Violation] = []

    for path in files:
        result = scan_file(path, contract)
        for v in result.violations:
            if v.kind == "blocked":
                blocked_violations.append(v)
            else:
                unlisted_violations.append(v)

    exit_code = 0

    if blocked_violations:
        print(
            f"ERROR: {len(blocked_violations)} BLOCKED env var(s) treated as required\n"
            "These env vars must NEVER be required:\n"
        )
        for v in blocked_violations:
            print(_format_violation(v))
        exit_code = 1

    if unlisted_violations:
        print(
            f"\nWARNING: {len(unlisted_violations)} UNLISTED required env var(s)\n"
            "These vars are not in env_contract.yaml. Add them or use optional reads:\n"
        )
        for v in unlisted_violations:
            print(_format_violation(v))
        exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
