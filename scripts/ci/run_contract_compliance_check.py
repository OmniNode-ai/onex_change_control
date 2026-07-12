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
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
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
_ALLOWLIST_FIELDS = 2  # each entry is 'OMN-1234 <sha256>'

# OMN-14436 -- checks that can only ever observe the OCC tree.
#
# Until this ticket, every non-OCC invocation of this runner passed --workspace
# pointing at the *onex_change_control clone* rather than the product checkout,
# so a check_value's cwd was the receipt store. The only files an author could
# reach were the receipt and the contract itself, and ~32% of the corpus greps
# exactly those. That is a wiring defect, not an authoring one.
#
# With --workspace now bound to the product, such a check can never pass: the
# path does not exist in the product tree. It is INERT -- it proves nothing
# about the code under test. An inert check is reported loudly and demoted to
# WARN so it cannot gate; it is never allowed to produce a PASS that would
# launder a red PR into green evidence (the OMN-14391 / omnibase_infra#2264
# case). See _has_effective_check for why an inert-only contract still BLOCKs
# on a non-grandfathered ticket.
_INERT_CHECK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"drift/dod_receipts/"),
    re.compile(r"(?<![\w/])contracts/OMN-"),
)


def _is_inert_check(check_value: Any) -> bool:
    """True if the check can only observe the OCC receipt/contract store.

    Such a check is structurally incapable of saying anything about the product
    repo the PR actually changes.
    """
    text = str(check_value)
    return any(p.search(text) for p in _INERT_CHECK_PATTERNS)


def _contract_digest(contract_path: Path) -> str:
    """sha256 of the contract file's exact bytes.

    The grandfather is bound to CONTENT, not to a ticket id (see
    _load_legacy_allowlist). Hashing the raw bytes means any edit at all --
    appending an entry, tweaking a check_value -- changes the digest and
    un-grandfathers the contract.
    """
    return hashlib.sha256(contract_path.read_bytes()).hexdigest()


def _load_legacy_allowlist(path: Path | None) -> dict[str, str]:
    """Load the OMN-14436 grandfather ratchet: ticket id -> contract digest.

    Contracts that predate product-workspace execution still EXECUTE and are
    REPORTED, but their failures are demoted BLOCK -> WARN so turning the runner
    on does not wedge in-flight work on pre-existing debt. New tickets are NOT
    in the list and are enforced from their first PR.

    The grandfather is bound to the contract's CONTENT DIGEST, not merely to its
    ticket id. A ticket-keyed allowlist would be a permanent laundering channel:
    anyone could append a fresh circular dod_evidence entry under an old ticket
    id and inherit its exemption forever. Binding to the digest means the moment
    a grandfathered contract is MODIFIED it stops being grandfathered, and must
    then carry at least one product-observing check or BLOCK. Frozen debt stays
    frozen; touched debt must be paid.

    This is a ratchet, not a paydown machine: the list may only shrink (pinned by
    tests/test_dod_runner_ratchet.py). It is deliberately NOT expiry-dated -- an
    expiry would manufacture paydown pressure on a corpus that RSD (OMN-14427) is
    slated to delete outright.

    Format: ``OMN-1234<whitespace><sha256>`` per line; ``#`` comments and blanks
    ignored. A line with no digest is REJECTED -- a digest-less entry would
    silently restore the ticket-keyed hole this binding exists to close.
    """
    if path is None:
        return {}
    if not path.exists():
        # Fail loudly. A silently-absent allowlist would enforce the entire
        # legacy corpus and wedge every repo -- the opposite of a safe default.
        msg = f"legacy allowlist not found: {path}"
        raise FileNotFoundError(msg)
    entries: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != _ALLOWLIST_FIELDS:
            msg = (
                f"malformed allowlist entry {line!r} in {path}: expected "
                "'OMN-1234 <sha256>'. A digest-less entry would reopen the "
                "ticket-keyed laundering hole (OMN-14436)."
            )
            raise ValueError(msg)
        entries[parts[0].upper()] = parts[1].lower()
    return entries


@dataclass(frozen=True)
class _CheckContext:
    pr_number: int
    repo: str
    ticket_id: str = ""
    contracts_dir: Path | None = None
    is_legacy: bool = False


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
    contracts_dir: Path | None = None,
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
    if contracts_dir is not None:
        overlay["CONTRACTS_DIR"] = str(contracts_dir)
        overlay["CONTRACT_REPO_DIR"] = str(contracts_dir.parent)
    if not overlay:
        return None
    return {**os.environ, **overlay}


def _check_command(
    _check_value: Any,
    workspace: Path,
    pr_number: int = 0,
    repo: str = "",
    ticket_id: str = "",
    contracts_dir: Path | None = None,
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

    cmd_env = _build_command_env(cmd_str, pr_number, repo, ticket_id, contracts_dir)

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
    context: _CheckContext,
) -> tuple[str, str, str]:
    """Run a single ModelDodCheck and return (check_type, result, detail)."""
    check_type = check.get("check_type", "")
    check_value = check.get("check_value", "")

    runner = _CHECK_RUNNERS.get(check_type)
    if runner is None:
        return check_type, _RESULT_WARN, f"Unknown check_type '{check_type}'"
    if check_type == "command":
        result, detail = runner(
            check_value,
            workspace,
            context.pr_number,
            context.repo,
            context.ticket_id,
            context.contracts_dir,
        )
    elif check_type == "test_passes":
        result, detail = runner(check_value, workspace, context.pr_number, context.repo)
    else:
        result, detail = runner(check_value, workspace)
    return check_type, result, detail


def _demote(
    check: dict[str, Any],
    result: str,
    detail: str,
    context: _CheckContext,
) -> tuple[str, str, str]:
    """Apply the OMN-14436 demotion rules to one check result.

    Returns (result, detail, label). A BLOCK becomes a WARN when the check is
    inert (it can only see the OCC store, so its failure says nothing about the
    product) or when the ticket is grandfathered. Everything else stands.
    """
    if _is_inert_check(check.get("check_value", "")):
        # Inert checks are demoted whatever they returned: an inert PASS is
        # exactly the laundering this ticket exists to stop.
        return (
            _RESULT_WARN,
            f"INERT -- reads the OCC receipt/contract store, not the product; "
            f"proves nothing about {context.repo}. Original: {detail}",
            "INERT",
        )
    if result == _RESULT_BLOCK and context.is_legacy:
        return (
            _RESULT_WARN,
            f"GRANDFATHERED (OMN-14436 ratchet) -- would BLOCK. {detail}",
            "GRANDFATHERED",
        )
    return result, detail, ""


def _run_dod_checks(
    dod_evidence: list[Any],
    workspace: Path,
    context: _CheckContext,
) -> list[tuple[str, str, str, str]]:
    """Run all DoD checks and return (dod_id, check_type, result, detail) list."""
    results: list[tuple[str, str, str, str]] = []
    superseded = _superseded_dod_ids(dod_evidence)
    for dod_item in dod_evidence:
        item_id = dod_item.get("id", "?")
        item_desc = dod_item.get("description", "")
        if item_id in superseded:
            print(f"\n[DoD {item_id}] {item_desc[:80]}", flush=True)
            detail = (
                "SUPERSEDED -- a later append-only dod_evidence item declares "
                f"evidence_artifact='supersedes_dod_evidence:{item_id}'; old "
                "evidence is preserved for audit but not re-executed against "
                "the moved PR head."
            )
            results.append((item_id, "superseded", _RESULT_WARN, detail))
            print(f"  [~] superseded: {detail}", flush=True)
            continue
        checks = dod_item.get("checks", [])
        print(f"\n[DoD {item_id}] {item_desc[:80]}", flush=True)
        for check in checks:
            check_type, result, detail = _run_single_check(check, workspace, context)
            result, detail, label = _demote(check, result, detail, context)
            results.append((item_id, check_type, result, detail))
            icon = {"PASS": "+", "WARN": "~", "BLOCK": "X"}.get(result, "?")
            tag = f"{label} " if label else ""
            print(f"  [{icon}] {tag}{check_type}: {detail}", flush=True)
    return results


def _superseded_dod_ids(dod_evidence: list[Any]) -> set[str]:
    """Return dod_evidence ids explicitly superseded by later appended items."""
    seen: set[str] = set()
    superseded: set[str] = set()
    for dod_item in dod_evidence:
        if not isinstance(dod_item, dict):
            continue
        item_id = dod_item.get("id")
        supersedes = _supersedes_marker(dod_item.get("evidence_artifact"))
        if supersedes in seen:
            superseded.add(supersedes)
        if isinstance(item_id, str):
            seen.add(item_id)
    return superseded


def _supersedes_marker(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    prefix = "supersedes_dod_evidence:"
    if not value.startswith(prefix):
        return None
    superseded = value[len(prefix) :].strip()
    return superseded or None


def _has_effective_check(dod_evidence: list[Any]) -> bool:
    """True if any check can actually observe the product.

    A contract whose every check is inert carries zero proof about the code it
    claims to certify. Before OMN-14436 that was the norm, because the runner
    only ever showed authors the receipt store -- so the legacy corpus is
    grandfathered. A NEW ticket gets no such pass.
    """
    for dod_item in dod_evidence:
        if not isinstance(dod_item, dict):
            continue
        for check in dod_item.get("checks", []) or []:
            if isinstance(check, dict) and not _is_inert_check(
                check.get("check_value", "")
            ):
                return True
    return False


def run_compliance_check(
    pr_number: int,
    repo: str,
    contracts_dir: Path,
    workspace: Path,
    legacy_tickets: dict[str, str] | None = None,
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

    allow = legacy_tickets or {}
    recorded = allow.get(ticket_id.upper())
    actual = _contract_digest(contract_path)
    is_legacy = recorded is not None and recorded == actual
    if recorded is not None and not is_legacy:
        print(
            f"[INFO] {ticket_id} is in the grandfather allowlist but its contract "
            "has been MODIFIED since the cutoff -- exemption REVOKED. A touched "
            "contract must carry at least one product-observing check.",
            flush=True,
        )
    print(
        f"[INFO] Workspace (product under test): {workspace}\n"
        f"[INFO] Grandfathered (OMN-14436 ratchet): {is_legacy}",
        flush=True,
    )

    results = _run_dod_checks(
        dod_evidence,
        workspace,
        _CheckContext(pr_number, repo, ticket_id, contracts_dir, is_legacy),
    )

    total = len(results)
    passes = sum(1 for _, _, r, _ in results if r == _RESULT_PASS)
    warns = sum(1 for _, _, r, _ in results if r == _RESULT_WARN)
    blocks = sum(1 for _, _, r, _ in results if r == _RESULT_BLOCK)

    print(
        f"\n[SUMMARY] {ticket_id}: {passes}/{total} PASS, {warns} WARN, {blocks} BLOCK",
        flush=True,
    )

    # A contract with no check that can observe the product proves nothing about
    # it. The legacy corpus is grandfathered (it was authored against a runner
    # that only ever showed it the receipt store); a new ticket is not.
    if not _has_effective_check(dod_evidence):
        if is_legacy:
            print(
                "[WARN] Every check is INERT (OCC-store-only). Grandfathered "
                "under the OMN-14436 ratchet -- reported, not enforced.",
                flush=True,
            )
        else:
            print(
                f"[BLOCK] {ticket_id}: every check is INERT -- each one reads the "
                f"OCC receipt/contract store rather than {repo}. This contract "
                "cannot certify the code it claims to. Add at least one check "
                "that observes the product, e.g.\n"
                "  check_value: 'test -f src/path/touched_by_this_pr.py'\n"
                "  check_value: 'gh api repos/OWNER/REPO/pulls/<src_pr> --jq .merged'",
                flush=True,
            )
            return 1

    if blocks > 0:
        print(
            f"[BLOCK] {blocks} check(s) failed. PR cannot merge until resolved.",
            flush=True,
        )
        return 1

    if warns and not passes:
        # Do not call this "all checks satisfied" -- nothing was proven. Saying
        # so is the same declaration-in-place-of-verification this ticket exists
        # to remove.
        print(
            f"[PASS] No enforceable DoD check failed, but {warns}/{total} were "
            "WARN and 0 proved anything about the product.",
            flush=True,
        )
        return 0

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
        "--workspace",
        default=None,
        help=(
            "Product checkout the DoD checks run against (default: CWD). "
            "This MUST be the repo the PR changes -- pointing it at the "
            "onex_change_control clone is the OMN-14436 defect."
        ),
    )
    parser.add_argument(
        "--legacy-allowlist",
        default=None,
        help=(
            "Path to the OMN-14436 grandfather ratchet (one OMN ticket id per "
            "line). Listed tickets still execute and report, but their failures "
            "are demoted BLOCK -> WARN. Omit to enforce every ticket."
        ),
    )
    args = parser.parse_args()

    # Emergency-bypass toggle resolves from the integration contract + overlay
    # (descriptor.emergency_bypass bound to ${env.EMERGENCY_BYPASS}, OMN-13563);
    # empty string == disabled. Lazy import keeps this standalone CI script's
    # module load free of the package import.
    from onex_change_control.integrations import contract_descriptor

    bypass_env = contract_descriptor.emergency_bypass()
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
    legacy_path = Path(args.legacy_allowlist) if args.legacy_allowlist else None
    legacy_tickets = _load_legacy_allowlist(legacy_path)

    return run_compliance_check(
        pr_number=args.pr,
        repo=args.repo,
        contracts_dir=contracts_dir,
        workspace=workspace,
        legacy_tickets=legacy_tickets,
    )


if __name__ == "__main__":
    sys.exit(main())
