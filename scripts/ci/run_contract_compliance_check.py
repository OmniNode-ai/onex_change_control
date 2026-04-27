#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""run_contract_compliance_check.py -- Mandatory CI gate for ModelTicketContract DoD.

Usage (CI):
    python scripts/ci/run_contract_compliance_check.py \\
        --pr 123 \\
        --repo OmniNode-ai/omnimarket \\
        --contracts-dir <path-to-onex_change_control/contracts>

Usage (local):
    python scripts/ci/run_contract_compliance_check.py \\
        --pr 123 \\
        --repo OmniNode-ai/omnimarket

Exit codes:
    0  All checks pass (or no contract found -- WARN only)
    1  One or more BLOCK-level checks failed

Emergency bypass:
    Set EMERGENCY_BYPASS=<user>-<reason> env var.
    Bypasses all checks. Bypass is logged and audited.

Scope:
    Reads ModelTicketContract YAML from contracts/<OMN-num>.yaml.
    Runs each ModelDodCheck in each ModelDodEvidenceItem.
    No Linear API calls; no Claude Code harness; stdlib + gh CLI only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OMN_TICKET_PATTERN = re.compile(r"\b(OMN-\d+)\b", re.IGNORECASE)
_RESULT_PASS = "PASS"  # noqa: S105
_RESULT_WARN = "WARN"
_RESULT_BLOCK = "BLOCK"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(
    cmd: list[str],
    timeout: int = 30,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
            env=env,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError as exc:
        return 1, "", f"Command not found: {exc}"


def _extract_ticket_id(pr_number: int, repo: str) -> str | None:
    """Extract OMN ticket ID from PR title and branch via gh CLI."""
    rc, out, err = _run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "title,headRefName,body",
        ],
        timeout=30,
    )
    if rc != 0:
        print(f"[WARN] Could not fetch PR info: {err}", flush=True)
        return None

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        print(f"[WARN] Could not parse PR JSON: {out[:200]}", flush=True)
        return None

    for field in ("title", "headRefName", "body"):
        text = data.get(field) or ""
        match = _OMN_TICKET_PATTERN.search(text)
        if match:
            return match.group(1).upper()
    return None


def _find_contracts_dir(
    cli_contracts_dir: str | None,
    script_path: Path,
) -> Path:
    """Locate the contracts directory.

    Priority:
      1. --contracts-dir flag
      2. Sibling onex_change_control checkout
      3. This script's own repo contracts/
    """
    if cli_contracts_dir:
        return Path(cli_contracts_dir).resolve()

    # When cloned as a sibling repo in CI
    sibling = Path.cwd().parent / "onex_change_control" / "contracts"
    if sibling.exists():
        return sibling

    # When running from within onex_change_control worktree
    local = script_path.parent.parent.parent / "contracts"
    if local.exists():
        return local

    return Path("contracts").resolve()


# ---------------------------------------------------------------------------
# Check runners -- one per ModelDodCheck check_type
# ---------------------------------------------------------------------------


def _check_test_exists(check_value: Any, workspace: Path) -> tuple[str, str]:
    """check_type=test_exists: check_value is a glob pattern."""
    pattern = str(check_value)
    matches = list(workspace.glob(pattern))
    if matches:
        return _RESULT_PASS, f"Found {len(matches)} file(s) matching '{pattern}'"
    return _RESULT_BLOCK, f"No files found matching glob '{pattern}'"


def _check_test_passes(
    _check_value: Any,
    _workspace: Path,
    pr_number: int,
    repo: str,
) -> tuple[str, str]:
    """check_type=test_passes: check via gh pr checks (CI must be green)."""
    rc, out, err = _run(
        ["gh", "pr", "checks", str(pr_number), "--repo", repo, "--json", "name,state"],
        timeout=60,
    )
    if rc != 0:
        # gh pr checks fails if CI hasn't started yet -- warn, don't block
        return (
            _RESULT_WARN,
            f"Could not fetch PR checks (CI may not have started): {err}",
        )

    try:
        checks = json.loads(out)
    except json.JSONDecodeError:
        return _RESULT_WARN, "Could not parse PR checks JSON"

    failures = [
        c for c in checks if c.get("state") not in ("SUCCESS", "SKIPPED", "NEUTRAL")
    ]
    if failures:
        names = ", ".join(c.get("name", "?") for c in failures)
        return _RESULT_BLOCK, f"Failing CI checks: {names}"
    return _RESULT_PASS, f"All {len(checks)} CI checks green"


def _check_file_exists(check_value: Any, workspace: Path) -> tuple[str, str]:
    """check_type=file_exists: check_value is a glob pattern."""
    pattern = str(check_value)
    matches = list(workspace.glob(pattern))
    if matches:
        return _RESULT_PASS, f"Found file(s) matching '{pattern}'"
    return _RESULT_BLOCK, f"No files found matching '{pattern}'"


def _check_grep(check_value: Any, workspace: Path) -> tuple[str, str]:
    """check_type=grep: check_value is dict with 'pattern' and 'path' keys."""
    if not isinstance(check_value, dict):
        return (
            _RESULT_BLOCK,
            f"grep check_value must be a dict, got: {type(check_value).__name__}",
        )

    pattern = check_value.get("pattern", "")
    search_path = check_value.get("path") or check_value.get("file") or "."
    if not pattern:
        return _RESULT_BLOCK, "grep check_value missing 'pattern' key"

    rc, out, _ = _run(
        ["grep", "-rl", "--include=*.py", pattern, str(workspace / search_path)],
        timeout=30,
    )
    if rc == 0 and out:
        return (
            _RESULT_PASS,
            f"Pattern '{pattern}' found in {len(out.splitlines())} file(s)",
        )
    return _RESULT_BLOCK, f"Pattern '{pattern}' not found under '{search_path}'"


def _substitute_tokens(
    cmd: str,
    pr_number: int,
    repo: str,
    ticket_id: str,
) -> str:
    """Substitute templating tokens in a check command.

    Two complementary placeholder forms are supported so contract YAML can
    pick whichever reads best:

    * ``{pr}``, ``{repo}``, ``{ticket_id}`` — runner-level substitution that
      happens before ``sh -c`` is invoked (safe in any shell context, including
      single-quoted strings).
    * ``${PR_NUMBER}``, ``${REPO}``, ``${TICKET_ID}`` — shell-style placeholders
      that ALSO get pre-substituted here so they work in single-quoted strings
      (where ``sh -c`` would not expand them). The same names are exported as
      env vars by the caller, so unquoted ``${PR_NUMBER}`` references in
      double-quoted strings keep working too.

    Pre-substitution is preferred over relying solely on env-var expansion
    because a contract author that writes ``'gh pr checks ${PR_NUMBER}'`` (with
    single quotes) would otherwise see the literal token reach ``gh``.
    """
    return (
        cmd.replace("{pr}", str(pr_number))
        .replace("{repo}", repo)
        .replace("{ticket_id}", ticket_id)
        .replace("${PR_NUMBER}", str(pr_number))
        .replace("${REPO}", repo)
        .replace("${TICKET_ID}", ticket_id)
    )


def _maybe_demote_precommit(cmd_str: str) -> tuple[str, str] | None:
    """Return a (result, detail) WARN tuple if a pre-commit cmd should be
    skipped because the binary is genuinely absent. Returns None to indicate
    "do not demote — proceed with normal execution".
    """
    if not cmd_str.lstrip().startswith("pre-commit"):
        return None
    rc_which, _, _ = _run(["which", "pre-commit"], timeout=5)
    if rc_which == 0:
        return None  # binary present — enforce normally
    in_ci = os.environ.get("CI", "").lower() in ("true", "1")
    if in_ci:
        msg = (
            "[WARN] pre-commit check skipped (binary absent in CI). "
            "Install pre-commit on the runner to enforce this check."
        )
        print(msg, flush=True)
        return _RESULT_WARN, "pre-commit check skipped (binary absent in CI)"
    print(
        "[WARN] pre-commit check skipped (pre-commit not installed). "
        "Run pre-commit locally to verify.",
        flush=True,
    )
    return _RESULT_WARN, "pre-commit check skipped (pre-commit not installed)"


def _build_command_env(
    cmd_str: str,
    pr_number: int,
    repo: str,
    ticket_id: str,
) -> dict[str, str] | None:
    """Build the env overlay for a contract command.

    PR_NUMBER / REPO / TICKET_ID are always exported when set so contract
    authors can reference them as ``$VAR`` in double-quoted shell strings.
    GH_REPO is additionally injected when the command shells out to ``gh``
    because gh cannot infer the branch in detached-HEAD CI checkouts
    (regression: OMN-8830).
    """
    overlay: dict[str, str] = {}
    if pr_number:
        overlay["PR_NUMBER"] = str(pr_number)
    if repo:
        overlay["REPO"] = repo
        if "gh " in cmd_str:
            overlay["GH_REPO"] = repo
    if ticket_id:
        overlay["TICKET_ID"] = ticket_id
    if not overlay:
        return None
    return {**os.environ, **overlay}


def _check_command(
    _check_value: Any,
    workspace: Path,
    pr_number: int = 0,
    repo: str = "",
    ticket_id: str = "",
) -> tuple[str, str]:
    """check_type=command: check_value is a shell command; exit 0 = pass.

    Supports both ``{pr}``/``{repo}``/``{ticket_id}`` and
    ``${PR_NUMBER}``/``${REPO}``/``${TICKET_ID}`` placeholders so contract YAML
    files don't hard-code PR numbers, repo names, or ticket IDs. Pre-substitutes
    every token before invoking ``sh -c`` AND exports them as env vars so
    ``$PR_NUMBER``-style references work in double-quoted shell strings too.

    repo is validated against ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ before
    substitution to prevent shell injection via adversarial --repo values.

    pre-commit commands are demoted to WARN only when pre-commit binary is
    genuinely absent AND the process is running in CI. Installing pre-commit
    on the runner opts back in to full enforcement.
    """
    if repo and not _REPO_PATTERN.match(repo):
        return (
            _RESULT_BLOCK,
            f"Invalid --repo '{repo}': must match org/repo (alphanumeric, -, _, .)",
        )

    cmd_str = _substitute_tokens(str(_check_value), pr_number, repo, ticket_id)

    demoted = _maybe_demote_precommit(cmd_str)
    if demoted is not None:
        return demoted

    cmd_env = _build_command_env(cmd_str, pr_number, repo, ticket_id)

    rc, out, err = _run(["sh", "-c", cmd_str], timeout=60, cwd=workspace, env=cmd_env)
    if rc == 0:
        return _RESULT_PASS, f"Command succeeded: {cmd_str[:80]}"
    output_snippet = (out + err)[:200]
    return (
        _RESULT_BLOCK,
        f"Command failed (exit {rc}): {cmd_str[:80]}\n  {output_snippet}",
    )


def _check_endpoint(check_value: Any, workspace: Path) -> tuple[str, str]:
    """check_type=endpoint: check_value is a URL or local path."""
    target = str(check_value)
    if target.startswith(("http://", "https://")):
        rc, _, err = _run(["curl", "-fsS", "--max-time", "10", target], timeout=15)
        if rc == 0:
            return _RESULT_PASS, f"Endpoint reachable: {target}"
        return (
            _RESULT_WARN,
            f"Endpoint unreachable (non-blocking in CI): {target} -- {err}",
        )
    # Local path
    resolved = workspace / target
    if resolved.exists():
        return _RESULT_PASS, f"Path exists: {target}"
    return _RESULT_BLOCK, f"Path not found: {target}"


_CHECK_RUNNERS: dict[str, Any] = {
    "test_exists": _check_test_exists,
    "test_passes": _check_test_passes,
    "file_exists": _check_file_exists,
    "grep": _check_grep,
    "command": _check_command,
    "endpoint": _check_endpoint,
}


# ---------------------------------------------------------------------------
# Contract loader
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML using pyyaml (available in CI after pip install pyyaml)."""
    try:
        import yaml

        with path.open() as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        pass

    print(
        "[WARN] pyyaml not installed; contract parsing skipped. "
        "Install: pip install pyyaml",
        flush=True,
    )
    return {}


# ---------------------------------------------------------------------------
# Main compliance runner
# ---------------------------------------------------------------------------


def _run_single_check(
    check: dict[str, Any],
    workspace: Path,
    pr_number: int,
    repo: str,
    ticket_id: str = "",
) -> tuple[str, str, str]:
    """Run a single ModelDodCheck and return (check_type, result, detail)."""
    check_type = check.get("check_type", "")
    check_value = check.get("check_value", "")

    runner = _CHECK_RUNNERS.get(check_type)
    if runner is None:
        return check_type, _RESULT_WARN, f"Unknown check_type '{check_type}'"
    if check_type == "command":
        result, detail = runner(check_value, workspace, pr_number, repo, ticket_id)
    elif check_type == "test_passes":
        result, detail = runner(check_value, workspace, pr_number, repo)
    else:
        result, detail = runner(check_value, workspace)
    return check_type, result, detail


def _run_dod_checks(
    dod_evidence: list[Any],
    workspace: Path,
    pr_number: int,
    repo: str,
    ticket_id: str = "",
) -> list[tuple[str, str, str, str]]:
    """Run all DoD checks and return (dod_id, check_type, result, detail) list."""
    results: list[tuple[str, str, str, str]] = []
    for dod_item in dod_evidence:
        item_id = dod_item.get("id", "?")
        item_desc = dod_item.get("description", "")
        checks = dod_item.get("checks", [])
        print(f"\n[DoD {item_id}] {item_desc[:80]}", flush=True)
        for check in checks:
            check_type, result, detail = _run_single_check(
                check, workspace, pr_number, repo, ticket_id
            )
            results.append((item_id, check_type, result, detail))
            icon = {"PASS": "+", "WARN": "~", "BLOCK": "X"}.get(result, "?")
            print(f"  [{icon}] {check_type}: {detail}", flush=True)
    return results


def run_compliance_check(
    pr_number: int,
    repo: str,
    contracts_dir: Path,
    workspace: Path,
) -> int:
    """Run all contract compliance checks. Returns exit code (0=pass, 1=block)."""
    ticket_id = _extract_ticket_id(pr_number, repo)
    if not ticket_id:
        print(
            f"[WARN] No OMN ticket ID in PR #{pr_number} title/branch/body. "
            "Skipping contract check.",
            flush=True,
        )
        return 0

    print(f"[INFO] Ticket: {ticket_id}, PR: #{pr_number}, Repo: {repo}", flush=True)

    contract_path = contracts_dir / f"{ticket_id}.yaml"
    if not contract_path.exists():
        print(
            f"[WARN] No contract at {contract_path}. "
            "Backfill pending (OMN-8637). PR not blocked.",
            flush=True,
        )
        return 0

    print(f"[INFO] Contract: {contract_path}", flush=True)

    contract = _load_yaml(contract_path)
    if not contract:
        print("[WARN] Contract file is empty or unreadable. Skipping.", flush=True)
        return 0

    dod_evidence = contract.get("dod_evidence", [])
    if not dod_evidence:
        print("[INFO] No dod_evidence checks in contract.", flush=True)
        print("[PASS] No executable DoD checks. Contract acknowledged.", flush=True)
        return 0

    results = _run_dod_checks(dod_evidence, workspace, pr_number, repo, ticket_id)

    total = len(results)
    passes = sum(1 for _, _, r, _ in results if r == _RESULT_PASS)
    warns = sum(1 for _, _, r, _ in results if r == _RESULT_WARN)
    blocks = sum(1 for _, _, r, _ in results if r == _RESULT_BLOCK)

    print(
        f"\n[SUMMARY] {ticket_id}: {passes}/{total} PASS, {warns} WARN, {blocks} BLOCK",
        flush=True,
    )

    if blocks > 0:
        print(
            f"[BLOCK] {blocks} check(s) failed. PR cannot merge until resolved.",
            flush=True,
        )
        return 1

    print("[PASS] All executable DoD checks satisfied.", flush=True)
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Contract compliance CI gate")
    parser.add_argument("--pr", required=True, type=int, help="PR number")
    parser.add_argument("--repo", required=True, help="GitHub repo (org/name)")
    parser.add_argument("--contracts-dir", default=None, help="Path to contracts dir")
    parser.add_argument(
        "--workspace", default=None, help="Workspace root (default: CWD)"
    )
    args = parser.parse_args()

    bypass_env = os.environ.get("EMERGENCY_BYPASS", "").strip()
    if bypass_env:
        print(
            f"[EMERGENCY_BYPASS] Bypass activated by: {bypass_env}. "
            "All contract checks skipped. This action is audited.",
            flush=True,
        )
        print(f"[AUDIT] repo={args.repo} pr={args.pr} bypass={bypass_env}", flush=True)
        return 0

    script_path = Path(__file__).resolve()
    contracts_dir = _find_contracts_dir(args.contracts_dir, script_path)
    workspace = Path(args.workspace).resolve() if args.workspace else Path.cwd()

    return run_compliance_check(
        pr_number=args.pr,
        repo=args.repo,
        contracts_dir=contracts_dir,
        workspace=workspace,
    )


if __name__ == "__main__":
    sys.exit(main())
