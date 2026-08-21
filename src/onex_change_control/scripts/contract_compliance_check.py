#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Contract compliance engine -- mandatory CI gate for ModelTicketContract DoD.

OMN-14458: this is the CANONICAL, importable home of the check-execution
engine. It used to live only at ``scripts/ci/run_contract_compliance_check.py``
(a repo-root script, excluded from the built wheel), which meant no downstream
consumer could import it -- every local preflight tool maintained its own
forked copy of the check runners, and those copies silently diverged from
this one (missing the OMN-14436 workspace binding, missing inert-check
detection). ``scripts/ci/run_contract_compliance_check.py`` is now a thin
wrapper that re-exports everything from this module so CI's existing
invocation keeps working unchanged; omniclaude's ``dod_evidence_runner.py``
imports the check runners and inert-check/demotion logic directly from here.

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
import shlex
import subprocess
import sys
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from onex_change_control.models.model_dod_check import ModelDodEvidenceItem
from onex_change_control.validation.evidence_admissibility import (
    EXECUTED_HERMETIC_COMMANDS,
    LIVE_PROBE_COMMANDS,
    AdmissibilityVerdict,
    admissible_evidence_guidance,
    classify_evidence_item,
)

_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OMN_TICKET_PATTERN = re.compile(r"\b(OMN-\d+)\b", re.IGNORECASE)
_RESULT_PASS = "PASS"  # noqa: S105
_RESULT_WARN = "WARN"
_RESULT_BLOCK = "BLOCK"
_RESULT_NOT_EVALUATED = "NOT_EVALUATED"
_SELF_STATUS_CHECK_NAMES = frozenset({"CI Summary", "Contract Compliance Check"})
_EXECUTION_SCOPE_HOSTED_AND_LOCAL = "hosted_and_local"
_EXECUTION_SCOPE_LOCAL_DONE_GATE = "local_done_gate"
_EXECUTION_SCOPES = frozenset(
    {_EXECUTION_SCOPE_HOSTED_AND_LOCAL, _EXECUTION_SCOPE_LOCAL_DONE_GATE}
)
_ALLOWLIST_FIELDS = 2  # each entry is 'OMN-1234 <sha256>'

# OMN-15309 -- admissibility is decided by ONE predicate, shared with deploy-gate.
#
# Operator ruling 2026-07-29: the OMN-14505 deploy-gate falsifiability predicate
# (EXECUTED, FALSIFIABLE, OUTSIDE ITS OWN DIFF) is THE admissibility rule for
# evidence everywhere. It lives in
# ``onex_change_control.validation.evidence_admissibility`` and is adopted from
# ``omniclaude/.github/actions/deploy-gate/validate_pr_deploy_required.py``
# (``classify_check_value``), which remains the cited source; the two are held
# in parity by execution against a shared corpus (tests/data/
# evidence_admissibility_cases.yaml, run against BOTH implementations by the
# ``predicate-parity`` step of the contract-compliance CI job).
#
# History this replaces, and why the replacement is strictly larger:
#   OMN-14436 introduced two regexes (``drift/dod_receipts/`` and
#   ``contracts/OMN-``) to catch checks that could only observe the OCC store,
#   after finding the runner's ``--workspace`` had been mis-pointed at the OCC
#   clone so the receipt was the only file a check could reach (~32% of the
#   corpus grepped exactly those paths). Those two regexes are the DENY half of
#   the predicate and are preserved verbatim inside it. What they never caught,
#   and the predicate does, is the rest of the same disease: a probe name
#   appearing as *quoted text* beside a circular grep, an ``echo``/``true``
#   whose exit status the author fixed by typing it, a ``test -f`` on a path the
#   PR itself adds, and a grep whose every path operand is a file this same
#   change writes. All of those "observe the product" under the old regexes and
#   still prove nothing.
#
# The word INERT is retained for the verdict label and the demotion machinery
# (see ``_demote`` / ``_has_effective_check``) so the ratchet's blast radius is
# unchanged in SHAPE: an inadmissible check is reported loudly and demoted to
# WARN so it can never gate, and it is never allowed to produce a PASS that
# would launder a red PR into green evidence (the OMN-14391 /
# omnibase_infra#2264 case). Legacy content-pinned contracts stay grandfathered;
# a NEW or touched contract is held to the real bar from its first PR.
#
# This runner EXECUTES check_value, so its ALLOW vocabulary is the live-probe
# set PLUS commands that genuinely run against the product checkout in CI. A
# text-only consumer (deploy-gate, which never executes) passes the live set
# alone. That single parameter is the ONLY difference between consumers -- the
# DENY half and the command-position analysis are identical.
_RUNNER_ADMISSIBLE_PROBES: frozenset[str] = (
    LIVE_PROBE_COMMANDS | EXECUTED_HERMETIC_COMMANDS
)


def _classify_check(
    check_value: Any,
    changed_paths: frozenset[str] | None = None,
    check_type: str = "command",
) -> AdmissibilityVerdict:
    """Apply the single admissibility predicate to one dod_evidence check."""
    return classify_evidence_item(
        check_type,
        check_value,
        admissible_probes=_RUNNER_ADMISSIBLE_PROBES,
        changed_paths=changed_paths,
    )


def _is_inert_check(
    check_value: Any,
    changed_paths: frozenset[str] | None = None,
    check_type: str = "command",
) -> bool:
    """True if the check is INADMISSIBLE under the OMN-15309 predicate.

    Inadmissible means at least one of the three required properties is missing:
    it does not execute, it cannot go RED, or everything it reads is authored by
    this same change. Such a check is structurally incapable of saying anything
    about the product repo the PR actually changes.
    """
    return not _classify_check(check_value, changed_paths, check_type).admissible


# OMN-14051 -- non-hermetic check_value guard (reject at validation time).
#
# A dod_evidence check of check_type "command" has its check_value executed
# verbatim by this runner inside the CI environment. CI runners have no ssh, no
# route to the .201 Tailscale/LAN hosts, and no live docker/k8s daemon, so a
# command that shells out to ssh/scp, a live container daemon, or network egress
# to a non-loopback host can NEVER pass in CI -- it BLOCKs the PR with a cryptic
# runtime error ("sh: 1: ssh: not found", exit 127) even when the underlying
# work is real. Observed on OCC PR #3642 (OMN-14001): a `dod-deploy-scope` item
# inlined `ssh jonah@100.109.203.94 "docker ps ..."` and produced
# "3/4 PASS, 1 BLOCK".
#
# OMN-15309 CORRECTION -- this block used to prescribe exactly the shape the
# same file unconditionally refuses to credit:
#
#   check_value: >-
#     grep -q '^status: PASS$'
#     "$CONTRACT_REPO_DIR/drift/dod_receipts/<TICKET>/<evidence-id>/command.yaml"
#
# Following that instruction produced a permanent WARN and could never produce a
# PASS: the OMN-14051 guard rejected the inline live probe, and the OMN-14436
# INERT rule demoted the receipt grep the guard told the author to write
# instead. Three surfaces rejected the same shape on 2026-07-28 (deploy-gate's
# annotation on omnimarket#1927, this evaluator's demotion, and this comment
# prescribing it), which is why the contradiction was filed rather than patched.
#
# The admissible hermetic form for a fact this runner cannot probe live is a
# CONTENT READ AT A PINNED REF against the product repo -- executed on any CI
# runner, falsifiable, and outside the diff the evidence author writes:
#
#   check_value: >-
#     gh api repos/OWNER/REPO/contents/<changed_path>?ref=<merge_sha>
#     --jq .content | base64 -d | grep -q '<symbol the fix introduces>'
#
# `gh` is deliberately absent from the deny lists below for exactly this reason.
# See ``admissible_evidence_guidance()`` in
# ``onex_change_control.validation.evidence_admissibility`` for the full set of
# admissible shapes per evidence class -- that function is the single author-
# facing text, and it is what this runner prints on refusal.
#
# This guard rejects the non-hermetic form up front with that actionable
# message, so the failure surfaces at authoring/validation time instead of as a
# late, cryptic CI error. It is folded into the *existing* validator (no new
# gate/workflow -- net-negative-surface) and is subject to the same OMN-14436
# demotion rules as any other BLOCK: a grandfathered (content-pinned legacy)
# contract is demoted to WARN -- so the pre-existing corpus of `docker exec`
# runtime-proof checks is reported but not wedged -- while a NEW or touched
# contract is enforced from its first PR.
#
# Detection is deliberately conservative (per the ticket: "start conservative,
# expand as needed"). Known gap: a binary hidden inside a command substitution
# (`$(ssh ...)`) is not yet detected; only command-position invocations are.
_MAX_SNIPPET_LEN = 160

# Shell tokens that begin a new command word (so the next token is in command
# position). shlex emits these as standalone tokens when whitespace-separated.
_SHELL_OPERATORS: frozenset[str] = frozenset(
    {"|", "||", "&&", ";", ";;", "&", "|&", "(", ")", "{", "}"}
)
# Command wrappers whose *argument* is the real command; stay in command
# position past them (and past `env FOO=bar` assignment tokens).
_WRAPPER_BINS: frozenset[str] = frozenset(
    {"sudo", "env", "time", "nice", "nohup", "command", "exec", "xargs", "then", "do"}
)
# Remote shell / remote copy: always non-hermetic.
_REMOTE_SHELL_BINS: frozenset[str] = frozenset(
    {"ssh", "scp", "sftp", "rsync", "telnet"}
)
# Container / orchestration: need a live daemon absent from CI runners.
_DAEMON_BINS: frozenset[str] = frozenset(
    {"docker", "docker-compose", "podman", "nerdctl", "kubectl"}
)
# Network fetch: non-hermetic only when the target host is not loopback.
_NET_FETCH_BINS: frozenset[str] = frozenset({"curl", "wget", "nc", "ncat"})

_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Full-octet IPv4 in the private / Tailscale-CGNAT ranges. Anchored to a
# complete dotted quad so version strings ("10.2") never match.
_LAN_IP_RE = re.compile(
    r"\b("
    r"192\.168\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"
    r")\b"
)
_URL_HOST_RE = re.compile(r"https?://([^/\s]+)")
_LOOPBACK_HOSTS: frozenset[str] = frozenset(
    {"localhost", "127.0.0.1", "::1", "ip6-localhost"}
)


def _command_binaries(command: str) -> list[str]:
    """Basenames of the binaries invoked in command position in ``command``.

    Quote-aware via ``shlex`` so a binary name embedded in a quoted argument --
    e.g. the word "docker" in ``grep -q 'no docker exec' file`` -- is NOT
    reported; only tokens that actually start a command word are. Shell
    operators (``;`` ``&&`` ``||`` ``|``) and wrappers (``sudo``, ``env VAR=x``)
    keep the scan in command position.
    """
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        # Unbalanced quotes etc. -- best-effort whitespace split rather than
        # silently passing the guard on a malformed command.
        tokens = command.split()
    binaries: list[str] = []
    expect_command = True
    for token in tokens:
        if token in _SHELL_OPERATORS:
            expect_command = True
            continue
        if not expect_command:
            continue
        if _ASSIGNMENT_RE.match(token):
            # `env FOO=bar cmd` -- leading assignments are not the command.
            continue
        binary = token.rsplit("/", 1)[-1]
        if binary in _WRAPPER_BINS:
            continue  # stay in command position; the next token is the command
        binaries.append(binary)
        expect_command = False
    return binaries


def _is_loopback_host(host: str) -> bool:
    """True if ``host`` (optionally ``user@host:port``) is a loopback target."""
    text = host.strip().strip("\"'[]").lower()
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if text.startswith("127.") or text in _LOOPBACK_HOSTS:
        return True
    return text.split(":", 1)[0] in _LOOPBACK_HOSTS


def _has_nonloopback_url(command: str) -> bool:
    """True if ``command`` contains an http(s) URL whose host is not loopback."""
    return any(not _is_loopback_host(h) for h in _URL_HOST_RE.findall(command))


def _non_hermetic_message(reason: str, command: str) -> str:
    """Actionable rejection message steering the author to the receipt pattern."""
    snippet = (
        command
        if len(command) <= _MAX_SNIPPET_LEN
        else command[:_MAX_SNIPPET_LEN] + "..."
    )
    return (
        f"NON-HERMETIC check_value -- {reason}. A CI runner has no ssh, no route "
        "to the .201/LAN/Tailscale hosts, and no live docker/k8s daemon, so this "
        "command can never pass in CI (OMN-14051); it would BLOCK the PR with a "
        "cryptic runtime error even when the work is real. Run the live probe out "
        "of band, record it in a committed receipt "
        "(drift/dod_receipts/<TICKET>/<evidence-id>/command.yaml with fields "
        "probe_command, probe_stdout, exit_code, status: PASS), then set the "
        "contract check to grep that receipt:\n"
        "  check_value: >-\n"
        "    grep -q '^status: PASS$'\n"
        '    "$CONTRACT_REPO_DIR/drift/dod_receipts/<TICKET>'
        '/<evidence-id>/command.yaml"\n'
        f"  offending check_value: {snippet}"
    )


def _non_hermetic_reason(check: Any) -> str | None:
    """Rejection message if ``check``'s command check_value is non-hermetic, else None.

    "Non-hermetic" == the command depends on ssh/remote-copy, a live
    docker/k8s daemon, or network egress to a non-loopback host -- none of which
    exist on a CI runner. Returns ``None`` for a hermetic command (local file
    assertions, receipt greps, pure compute, loopback probes) so those still run.

    Only ``check_type: command`` is inspected: its check_value is executed as a
    shell command, so it is the only surface where these binaries actually run.
    grep / file_exists / test_exists check_values are file/glob assertions that
    may legitimately *contain* an IP or the word "docker" (e.g. grepping a
    committed config), so scanning them would be a false-positive machine.
    """
    if not isinstance(check, dict) or check.get("check_type") != "command":
        return None
    command = str(check.get("check_value", ""))
    if not command.strip():
        return None

    binaries = _command_binaries(command)
    for binary in binaries:
        if binary in _REMOTE_SHELL_BINS:
            return _non_hermetic_message(
                f"invokes the remote-access binary {binary!r}", command
            )
        if binary in _DAEMON_BINS:
            return _non_hermetic_message(
                f"invokes {binary!r}, which needs a live container/orchestration "
                "daemon that CI runners do not have",
                command,
            )
    lan_ip = _LAN_IP_RE.search(command)
    if lan_ip is not None:
        return _non_hermetic_message(
            f"references the non-routable LAN/Tailscale host {lan_ip.group(1)!r}",
            command,
        )
    for binary in binaries:
        if binary in _NET_FETCH_BINS and _has_nonloopback_url(command):
            return _non_hermetic_message(
                f"performs network egress via {binary!r} to a non-loopback host",
                command,
            )
    return None


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
    #: Repo-relative paths this PR modifies, used by the OMN-15309 predicate's
    #: OUTSIDE-ITS-OWN-DIFF rule. Empty means "not resolved" -- the rule is then
    #: reported as NOT EVALUATED rather than silently passing.
    changed_paths: frozenset[str] = dc_field(default_factory=frozenset)


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


def _pr_changed_paths(pr_number: int, repo: str) -> frozenset[str]:
    """Repo-relative paths this PR modifies, for the OUTSIDE-ITS-OWN-DIFF rule.

    Returns an EMPTY set when the list cannot be resolved. Callers must treat
    empty as "rule NOT EVALUATED" and say so out loud -- a silent skip that
    prints nothing is a false green, which is the class of defect OMN-15309
    exists to close.
    """
    rc, out, err = _run(
        [
            "gh",
            "api",
            f"repos/{repo}/pulls/{pr_number}/files",
            "--paginate",
            "--jq",
            ".[].filename",
        ],
        timeout=60,
    )
    if rc != 0:
        print(f"[WARN] Could not fetch PR file list: {err}", flush=True)
        return frozenset()
    return frozenset(line.strip() for line in out.splitlines() if line.strip())


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

    OMN-14458: ``script_path`` is ``Path(__file__)`` as seen by ``main()``,
    which now lives at ``src/onex_change_control/scripts/contract_compliance_check.py``
    (moved from the repo-root ``scripts/ci/`` script so it is importable).
    That is one directory deeper than before the move, hence 4 ``.parent``
    hops to the repo root instead of 3.
    """
    if cli_contracts_dir:
        return Path(cli_contracts_dir).resolve()

    # When cloned as a sibling repo in CI
    sibling = Path.cwd().parent / "onex_change_control" / "contracts"
    if sibling.exists():
        return sibling

    # When running from within an onex_change_control worktree:
    # contract_compliance_check.py -> scripts -> onex_change_control (package)
    # -> src -> repo root.
    local = script_path.parent.parent.parent.parent / "contracts"
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
        c
        for c in checks
        if c.get("name") not in _SELF_STATUS_CHECK_NAMES
        and c.get("state") not in ("SUCCESS", "SKIPPED", "NEUTRAL")
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
    reason = _non_hermetic_reason(check)
    if reason is not None:
        # OMN-14051: reject an ssh/live-docker/network-egress check_value up
        # front with an actionable message instead of executing it and surfacing
        # a cryptic "command not found" BLOCK. _demote still applies the
        # OMN-14436 grandfather downgrade, so the legacy corpus is reported, not
        # wedged.
        return str(check.get("check_type", "")), _RESULT_BLOCK, reason

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
    INADMISSIBLE under the OMN-15309 predicate (it does not execute, cannot go
    RED, or reads only what this same change authors -- so its verdict says
    nothing about the product) or when the ticket is grandfathered. Everything
    else stands.
    """
    verdict = _classify_check(
        check.get("check_value", ""),
        context.changed_paths,
        str(check.get("check_type", "command") or "command"),
    )
    if not verdict.admissible:
        # Inadmissible checks are demoted whatever they returned: an inadmissible
        # PASS is exactly the laundering this rule exists to stop.
        return (
            _RESULT_WARN,
            f"INERT [{verdict.rule}] -- {verdict.reason}; proves nothing about "
            f"{context.repo}. Original: {detail}",
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
    disclosed_skips = _disclosed_skip_supersession_ids(dod_evidence)
    for dod_item in dod_evidence:
        item_id = dod_item.get("id", "?") if isinstance(dod_item, dict) else "?"
        item_desc = (
            dod_item.get("description", "") if isinstance(dod_item, dict) else ""
        )
        print(f"\n[DoD {item_id}] {str(item_desc)[:80]}", flush=True)

        validated_item, validation_error = _validate_dod_item(dod_item)
        if validation_error is not None:
            results.append(
                (item_id, "dod_evidence_schema", _RESULT_BLOCK, validation_error)
            )
            print(f"  [X] dod_evidence_schema: {validation_error}", flush=True)
            continue
        assert validated_item is not None

        checks = validated_item["checks"]
        if not checks:
            if item_id in disclosed_skips:
                # OMN-15664 AC4: an item with no checks was previously always
                # BLOCK, indistinguishable from an accidental omission. A
                # disclosed skip (status "skipped", no checks, and an
                # evidence_artifact supersession marker naming an earlier
                # item) is an intentional, auditable statement that no check
                # can exist (e.g. a universally-quantified claim no probe can
                # falsify), not an omission. WARN, not BLOCK. See
                # _disclosed_skip_supersession_ids for the exact honesty
                # conditions (OMN-15413 AC6 incident: OCC#5975).
                detail = (
                    "DISCLOSED-SKIP SUPERSESSION -- dod_evidence item explicitly "
                    "declares status='skipped' with no checks and "
                    "evidence_artifact='supersedes_dod_evidence:<earlier-id>'; a "
                    "disclosed, mechanically unprovable claim, not an omission."
                )
                results.append((item_id, "checks", _RESULT_WARN, detail))
                print(f"  [~] checks: {detail}", flush=True)
                continue
            detail = (
                "NO_EXECUTABLE_CHECKS -- dod_evidence item declares no checks; "
                "an evidence requirement with no executable observation cannot "
                "produce a gate result."
            )
            result = _RESULT_BLOCK
            marker = "[X]"
            if context.is_legacy:
                result = _RESULT_WARN
                marker = "[~]"
                detail = "GRANDFATHERED (OMN-14436 content-pinned ratchet) -- " + detail
            results.append((item_id, "checks", result, detail))
            print(f"  {marker} checks: {detail}", flush=True)
            continue

        if item_id in superseded:
            detail = (
                "SUPERSEDED -- a later append-only dod_evidence item declares "
                f"evidence_artifact='supersedes_dod_evidence:{item_id}'; old "
                "evidence is preserved for audit but not re-executed against "
                "the moved PR head."
            )
            results.append((item_id, "superseded", _RESULT_WARN, detail))
            print(f"  [~] superseded: {detail}", flush=True)
            continue

        execution_scope = validated_item["execution_scope"]
        if execution_scope == _EXECUTION_SCOPE_LOCAL_DONE_GATE:
            detail = (
                "NOT-EVALUATED [local_done_gate] -- hosted contract compliance "
                "is not an authorized consumer; the local Done gate must execute "
                "this item and persist its result."
            )
            results.append((item_id, "execution_scope", _RESULT_NOT_EVALUATED, detail))
            print(f"  [-] execution_scope: {detail}", flush=True)
            continue
        for check in checks:
            check_type, result, detail = _run_single_check(check, workspace, context)
            result, detail, label = _demote(check, result, detail, context)
            results.append((item_id, check_type, result, detail))
            icon = {"PASS": "+", "WARN": "~", "BLOCK": "X"}.get(result, "?")
            tag = f"{label} " if label else ""
            print(f"  [{icon}] {tag}{check_type}: {detail}", flush=True)
    return results


def _validate_dod_item(
    dod_item: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """Strictly validate one active item before any declared check can execute."""
    if not isinstance(dod_item, dict):
        return None, "INVALID_DOD_EVIDENCE_ITEM -- item must be a mapping"

    execution_scope = dod_item.get("execution_scope", _EXECUTION_SCOPE_HOSTED_AND_LOCAL)
    if not isinstance(execution_scope, str) or execution_scope not in _EXECUTION_SCOPES:
        return None, (
            f"UNKNOWN_EXECUTION_SCOPE {execution_scope!r} -- allowed values: "
            f"{', '.join(sorted(_EXECUTION_SCOPES))}; refusing to execute with "
            "an ambiguous evidence audience."
        )

    try:
        validated = ModelDodEvidenceItem.model_validate(dod_item)
    except ValidationError as exc:
        locations = sorted(
            {".".join(str(part) for part in error["loc"]) for error in exc.errors()}
        )
        return None, (
            "INVALID_DOD_EVIDENCE_ITEM -- strict schema rejected field(s): "
            f"{', '.join(locations)}"
        )
    return validated.model_dump(mode="json"), None


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


def _disclosed_skip_supersession_ids(dod_evidence: list[Any]) -> set[str]:
    """Return dod_evidence ids that are honest, disclosed-skip terminal supersessions.

    OMN-15664 AC4: an item with EMPTY ``checks`` previously always BLOCKed
    (``NO_EXECUTABLE_CHECKS``), indistinguishable from an accidental
    omission. A disclosed skip -- ``status: "skipped"``, no ``checks``, and
    an ``evidence_artifact: "supersedes_dod_evidence:<id>"`` marker pointing
    at an id already declared EARLIER in the list (same append-only ordering
    rule as ``_superseded_dod_ids``) -- is not an omission: it is an
    intentional, auditable statement that no executable check can exist for
    this requirement (e.g. a universally-quantified claim no probe reachable
    from the gate can falsify). Reported WARN, not BLOCK.

    Any item missing ONE of these three conditions (no explicit "skipped"
    status, non-empty checks, or a supersedes marker whose target was never
    declared) is NOT in the returned set: fail closed, it still BLOCKs under
    the normal NO_EXECUTABLE_CHECKS path.
    """
    seen: set[str] = set()
    disclosed: set[str] = set()
    for dod_item in dod_evidence:
        if not isinstance(dod_item, dict):
            continue
        item_id = dod_item.get("id")
        target = _supersedes_marker(dod_item.get("evidence_artifact"))
        checks = dod_item.get("checks", [])
        has_checks = isinstance(checks, list) and len(checks) > 0
        if (
            isinstance(item_id, str)
            and target is not None
            and target in seen
            and dod_item.get("status") == "skipped"
            and not has_checks
        ):
            disclosed.add(item_id)
        if isinstance(item_id, str):
            seen.add(item_id)
    return disclosed


def _supersedes_marker(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    prefix = "supersedes_dod_evidence:"
    if not value.startswith(prefix):
        return None
    superseded = value[len(prefix) :].strip()
    return superseded or None


def _has_effective_check(
    dod_evidence: list[Any], changed_paths: frozenset[str] | None = None
) -> bool:
    """True if any check is ADMISSIBLE under the OMN-15309 predicate.

    A contract whose every check is inadmissible carries zero proof about the
    code it claims to certify. Before OMN-14436 that was the norm, because the
    runner only ever showed authors the receipt store -- so the legacy corpus is
    grandfathered. A NEW ticket gets no such pass.
    """
    superseded = _superseded_dod_ids(dod_evidence)
    for dod_item in dod_evidence:
        if not isinstance(dod_item, dict):
            continue
        validated_item, validation_error = _validate_dod_item(dod_item)
        if validation_error is not None or validated_item is None:
            continue
        if validated_item["id"] in superseded:
            continue
        if validated_item["execution_scope"] != _EXECUTION_SCOPE_HOSTED_AND_LOCAL:
            continue
        for check in validated_item["checks"]:
            if not isinstance(check, dict):
                continue
            check_type = str(check.get("check_type", "") or "")
            if check_type not in _CHECK_RUNNERS:
                continue
            if not _is_inert_check(
                check.get("check_value", ""), changed_paths, check_type
            ):
                return True
    return False


def _outside_diff_state(changed_paths: frozenset[str]) -> str:
    """One-line report of whether the OUTSIDE-ITS-OWN-DIFF rule could run.

    An unresolved PR file list must be SAID OUT LOUD. A rule that silently does
    not run is a false green -- the class OMN-15309 exists to close.
    """
    if changed_paths:
        return f"ENFORCED ({len(changed_paths)} changed path(s))"
    return (
        "NOT EVALUATED -- PR file list unresolved; a check whose every path "
        "operand is authored by this same PR will NOT be caught on this run"
    )


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
    changed_paths = _pr_changed_paths(pr_number, repo)
    outside_diff_state = _outside_diff_state(changed_paths)
    print(
        f"[INFO] Workspace (product under test): {workspace}\n"
        f"[INFO] Grandfathered (OMN-14436 ratchet): {is_legacy}\n"
        f"[INFO] Admissibility predicate (OMN-15309): EXECUTED + FALSIFIABLE + "
        f"OUTSIDE-ITS-OWN-DIFF\n"
        f"[INFO] OUTSIDE-ITS-OWN-DIFF rule: {outside_diff_state}",
        flush=True,
    )

    results = _run_dod_checks(
        dod_evidence,
        workspace,
        _CheckContext(
            pr_number, repo, ticket_id, contracts_dir, is_legacy, changed_paths
        ),
    )

    total = len(results)
    passes = sum(1 for _, _, r, _ in results if r == _RESULT_PASS)
    warns = sum(1 for _, _, r, _ in results if r == _RESULT_WARN)
    blocks = sum(1 for _, _, r, _ in results if r == _RESULT_BLOCK)
    not_evaluated = sum(1 for _, _, r, _ in results if r == _RESULT_NOT_EVALUATED)

    not_evaluated_summary = f", {not_evaluated} NOT_EVALUATED" if not_evaluated else ""
    print(
        f"\n[SUMMARY] {ticket_id}: {passes}/{total} PASS"
        f"{not_evaluated_summary}, {warns} WARN, {blocks} BLOCK",
        flush=True,
    )

    # A contract with no check that can observe the product proves nothing about
    # it. The legacy corpus is grandfathered (it was authored against a runner
    # that only ever showed it the receipt store); a new ticket is not.
    if not _has_effective_check(dod_evidence, changed_paths):
        if is_legacy:
            print(
                "[WARN] Every check is INADMISSIBLE under the OMN-15309 predicate. "
                "Grandfathered under the OMN-14436 ratchet -- reported, not enforced.",
                flush=True,
            )
        else:
            print(
                f"[BLOCK] {ticket_id}: no hosted-and-local effective check exists "
                f"-- every hosted check is INADMISSIBLE or every evidence item is "
                f"reserved for another execution scope. Not one hosted check is "
                f"EXECUTED, FALSIFIABLE, and OUTSIDE ITS OWN DIFF, so this contract "
                f"cannot certify the code it claims to about {repo}.\n"
                f"{admissible_evidence_guidance(repo)}",
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
