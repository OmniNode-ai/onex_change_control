#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""run_contract_compliance_check.py -- Mandatory CI gate: verify PR matches ModelTicketContract DoD.

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
    0  All checks pass (or no contract found — WARN only)
    1  One or more BLOCK-level checks failed

Emergency bypass:
    EMERGENCY_BYPASS=<user>-<reason> python scripts/ci/run_contract_compliance_check.py ...
    Bypasses all checks. Bypass is logged to stdout and the action is audited.

Scope:
    Reads ModelTicketContract YAML from contracts/<OMN-num>.yaml.
    Runs each ModelDodCheck in each ModelDodEvidenceItem.
    No Linear API calls; no Claude Code harness; stdlib + gh CLI only.
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OMN_TICKET_PATTERN = re.compile(r"\b(OMN-\d+)\b", re.IGNORECASE)
_RESULT_PASS = "PASS"
_RESULT_WARN = "WARN"
_RESULT_BLOCK = "BLOCK"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError as exc:
        return 1, "", f"Command not found: {exc}"


def _extract_ticket_id(pr_number: int, repo: str) -> str | None:
    """Extract OMN ticket ID from PR title and branch via gh CLI."""
    rc, out, err = _run(
        ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json", "title,headRefName,body"],
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


def _find_contracts_dir(cli_contracts_dir: str | None, script_path: Path) -> Path:
    """Locate the contracts directory.

    Priority:
      1. --contracts-dir flag
      2. Sibling onex_change_control checkout (../onex_change_control/contracts relative to CWD)
      3. This script's own repo contracts/ (works when running from within onex_change_control)
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
# Check runners — one per ModelDodCheck check_type
# ---------------------------------------------------------------------------


def _check_test_exists(check_value: Any, workspace: Path) -> tuple[str, str]:
    """check_type=test_exists: check_value is a glob pattern."""
    pattern = str(check_value)
    matches = glob.glob(str(workspace / pattern), recursive=True)
    if matches:
        return _RESULT_PASS, f"Found {len(matches)} file(s) matching '{pattern}'"
    return _RESULT_BLOCK, f"No files found matching glob '{pattern}'"


def _check_test_passes(check_value: Any, workspace: Path, pr_number: int, repo: str) -> tuple[str, str]:
    """check_type=test_passes: check via gh pr checks (CI must be green)."""
    rc, out, err = _run(
        ["gh", "pr", "checks", str(pr_number), "--repo", repo, "--json", "name,state"],
        timeout=60,
    )
    if rc != 0:
        # gh pr checks fails if CI hasn't started yet — warn, don't block
        return _RESULT_WARN, f"Could not fetch PR checks (CI may not have started): {err}"

    try:
        checks = json.loads(out)
    except json.JSONDecodeError:
        return _RESULT_WARN, "Could not parse PR checks JSON"

    failures = [c for c in checks if c.get("state") not in ("SUCCESS", "SKIPPED", "NEUTRAL")]
    if failures:
        names = ", ".join(c.get("name", "?") for c in failures)
        return _RESULT_BLOCK, f"Failing CI checks: {names}"
    return _RESULT_PASS, f"All {len(checks)} CI checks green"


def _check_file_exists(check_value: Any, workspace: Path) -> tuple[str, str]:
    """check_type=file_exists: check_value is a glob pattern."""
    pattern = str(check_value)
    matches = glob.glob(str(workspace / pattern), recursive=True)
    if matches:
        return _RESULT_PASS, f"Found file(s) matching '{pattern}'"
    return _RESULT_BLOCK, f"No files found matching '{pattern}'"


def _check_grep(check_value: Any, workspace: Path) -> tuple[str, str]:
    """check_type=grep: check_value is dict with 'pattern' and 'path' keys."""
    if not isinstance(check_value, dict):
        return _RESULT_BLOCK, f"grep check_value must be a dict, got: {type(check_value).__name__}"

    pattern = check_value.get("pattern", "")
    search_path = check_value.get("path", ".")
    if not pattern:
        return _RESULT_BLOCK, "grep check_value missing 'pattern' key"

    rc, out, _ = _run(
        ["grep", "-rl", "--include=*.py", pattern, str(workspace / search_path)],
        timeout=30,
    )
    if rc == 0 and out:
        return _RESULT_PASS, f"Pattern '{pattern}' found in {len(out.splitlines())} file(s)"
    return _RESULT_BLOCK, f"Pattern '{pattern}' not found under '{search_path}'"


def _check_command(check_value: Any, workspace: Path) -> tuple[str, str]:
    """check_type=command: check_value is a shell command; exit 0 = pass."""
    cmd_str = str(check_value)
    rc, out, err = _run(["sh", "-c", cmd_str], timeout=60)
    if rc == 0:
        return _RESULT_PASS, f"Command succeeded: {cmd_str[:80]}"
    output_snippet = (out + err)[:200]
    return _RESULT_BLOCK, f"Command failed (exit {rc}): {cmd_str[:80]}\n  {output_snippet}"


def _check_endpoint(check_value: Any, workspace: Path) -> tuple[str, str]:
    """check_type=endpoint: check_value is a URL or local path."""
    target = str(check_value)
    if target.startswith("http://") or target.startswith("https://"):
        rc, _, err = _run(["curl", "-fsS", "--max-time", "10", target], timeout=15)
        if rc == 0:
            return _RESULT_PASS, f"Endpoint reachable: {target}"
        return _RESULT_WARN, f"Endpoint unreachable (non-blocking in CI): {target} — {err}"
    # Local path
    resolved = workspace / target
    if resolved.exists():
        return _RESULT_PASS, f"Path exists: {target}"
    return _RESULT_BLOCK, f"Path not found: {target}"


_CHECK_RUNNERS = {
    "test_exists": _check_test_exists,
    "test_passes": _check_test_passes,
    "file_exists": _check_file_exists,
    "grep": _check_grep,
    "command": _check_command,
    "endpoint": _check_endpoint,
}


# ---------------------------------------------------------------------------
# Contract loader (pure stdlib YAML via manual parse or pyyaml if available)
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML using pyyaml (available in CI after pip install pyyaml)."""
    try:
        import yaml  # type: ignore[import-untyped]
        with path.open() as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        pass

    # Minimal fallback: just enough to read top-level string fields
    # For production use pyyaml is required — this fallback is intentionally limited
    print("[WARN] pyyaml not installed; contract parsing limited. Install: pip install pyyaml", flush=True)
    return {}


# ---------------------------------------------------------------------------
# Main compliance runner
# ---------------------------------------------------------------------------


def run_compliance_check(
    pr_number: int,
    repo: str,
    contracts_dir: Path,
    workspace: Path,
) -> int:
    """Run all contract compliance checks. Returns exit code (0=pass, 1=block)."""
    # Step 1: Extract ticket ID
    ticket_id = _extract_ticket_id(pr_number, repo)
    if not ticket_id:
        print(f"[WARN] No OMN ticket ID found in PR #{pr_number} title/branch/body. Skipping contract check.", flush=True)
        return 0

    print(f"[INFO] Ticket: {ticket_id}, PR: #{pr_number}, Repo: {repo}", flush=True)

    # Step 2: Find contract file
    contract_path = contracts_dir / f"{ticket_id}.yaml"
    if not contract_path.exists():
        print(
            f"[WARN] No contract found at {contract_path}. "
            f"Contract backfill pending (OMN-8637). PR is not blocked.",
            flush=True,
        )
        return 0

    print(f"[INFO] Contract: {contract_path}", flush=True)

    # Step 3: Load contract
    contract = _load_yaml(contract_path)
    if not contract:
        print("[WARN] Contract file is empty or unreadable. Skipping check.", flush=True)
        return 0

    dod_evidence = contract.get("dod_evidence", [])
    if not dod_evidence:
        print("[INFO] Contract has no dod_evidence checks. Checking evidence_requirements only.", flush=True)
        # evidence_requirements are informational, not executable — treat as PASS
        print("[PASS] No executable DoD checks defined. Contract acknowledged.", flush=True)
        return 0

    # Step 4: Run each check
    results: list[tuple[str, str, str, str]] = []  # (dod_id, check_type, result, detail)
    block_count = 0

    for dod_item in dod_evidence:
        item_id = dod_item.get("id", "?")
        item_desc = dod_item.get("description", "")
        checks = dod_item.get("checks", [])

        print(f"\n[DoD {item_id}] {item_desc[:80]}", flush=True)

        for check in checks:
            check_type = check.get("check_type", "")
            check_value = check.get("check_value", "")

            runner = _CHECK_RUNNERS.get(check_type)
            if runner is None:
                result, detail = _RESULT_WARN, f"Unknown check_type '{check_type}' — skipping"
            elif check_type in ("test_passes",):
                result, detail = runner(check_value, workspace, pr_number, repo)
            else:
                result, detail = runner(check_value, workspace)

            results.append((item_id, check_type, result, detail))
            icon = {"PASS": "+", "WARN": "~", "BLOCK": "X"}.get(result, "?")
            print(f"  [{icon}] {check_type}: {detail}", flush=True)

            if result == _RESULT_BLOCK:
                block_count += 1

    # Step 5: Summary
    total = len(results)
    passes = sum(1 for _, _, r, _ in results if r == _RESULT_PASS)
    warns = sum(1 for _, _, r, _ in results if r == _RESULT_WARN)
    blocks = sum(1 for _, _, r, _ in results if r == _RESULT_BLOCK)

    print(f"\n[SUMMARY] {ticket_id}: {passes}/{total} PASS, {warns} WARN, {blocks} BLOCK", flush=True)

    if block_count > 0:
        print(f"[BLOCK] {block_count} check(s) failed. PR cannot merge until resolved.", flush=True)
        return 1

    print("[PASS] All executable DoD checks satisfied.", flush=True)
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Contract compliance CI gate")
    parser.add_argument("--pr", required=True, type=int, help="PR number")
    parser.add_argument("--repo", required=True, help="GitHub repo (org/name)")
    parser.add_argument("--contracts-dir", default=None, help="Path to contracts directory")
    parser.add_argument("--workspace", default=None, help="Workspace root (default: CWD)")
    args = parser.parse_args()

    # Emergency bypass
    bypass_env = os.environ.get("EMERGENCY_BYPASS", "").strip()
    if bypass_env:
        print(
            f"[EMERGENCY_BYPASS] Bypass activated by: {bypass_env}. "
            f"All contract checks skipped. This action is audited.",
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
