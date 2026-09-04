# SPDX-License-Identifier: MIT
# Copyright (c) 2026 OmniNode Team
# ruff: noqa: S603, S607
"""check_seam_contract_coverage.py -- Enforce contract existence for seam tickets.

Detects when a branch modifies interface-surface files (Kafka emitters, Pydantic
models) without a corresponding contract YAML in contracts/<ticket_id>.yaml.

Exit 0 = contract exists or no seam files changed.
Exit 1 = seam files changed but no contract found.
Exit 2 = usage error or git failure.

Usage:
    # Run against the current branch's configured upstream (default)
    python3 scripts/validation/check_seam_contract_coverage.py

    # Specify base branch explicitly
    python3 scripts/validation/check_seam_contract_coverage.py --base origin/main

    # Warn-only (CI info, no failure)
    python3 scripts/validation/check_seam_contract_coverage.py --warn-only

OMN-5388
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Files touching these path patterns are considered seam surface changes.
SEAM_PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^src/onex_change_control/kafka/"),
    re.compile(r"^src/onex_change_control/models/model_.*\.py$"),
    re.compile(r"^src/onex_change_control/enums/enum_interface"),
]

# Branch name patterns for extracting a ticket ID.
TICKET_PATTERN = re.compile(r"(OMN-\d+)", re.IGNORECASE)
TRUSTED_BASE_PATTERN = re.compile(r"^origin/(?:dev|main|trunk)$")


class BaseResolutionError(RuntimeError):
    """Raised when a comparison base cannot be established truthfully."""

    @classmethod
    def git_command_failed(cls, args: list[str], detail: str) -> BaseResolutionError:
        return cls(f"{' '.join(args)} failed: {detail}")

    @classmethod
    def upstream_unavailable(cls) -> BaseResolutionError:
        return cls(
            "could not determine this branch's upstream comparison base; "
            "configure an upstream or pass --base <remote>/<target> explicitly"
        )

    @classmethod
    def detached_head_requires_base(cls) -> BaseResolutionError:
        return cls(
            "HEAD is detached, so a local hook cannot infer the intended comparison "
            "target; pass --base <remote>/<target> explicitly"
        )

    @classmethod
    def self_tracking_upstream(cls, branch: str, upstream: str) -> BaseResolutionError:
        return cls(
            f"branch {branch!r} tracks itself via {upstream!r}, which is not a "
            "trustworthy PR target; configure its target upstream or pass --base "
            "<remote>/<target> explicitly"
        )

    @classmethod
    def current_branch_unavailable(cls, detail: str) -> BaseResolutionError:
        return cls(f"could not determine current branch: {detail}")

    @classmethod
    def base_unavailable(cls, base: str) -> BaseResolutionError:
        return cls(
            f"comparison base {base!r} is unavailable; fetch/configure that ref "
            "or pass an available --base explicitly"
        )

    @classmethod
    def diff_failed(cls, base: str, detail: str) -> BaseResolutionError:
        return cls(f"could not diff comparison base {base!r} against HEAD: {detail}")

    @classmethod
    def untrusted_base(cls, base: str) -> BaseResolutionError:
        return cls(f"comparison base {base!r} is not a trusted canonical remote target")

    @classmethod
    def incomplete_event_binding(cls) -> BaseResolutionError:
        return cls(
            "CI supplied a partial seam comparison binding; base, immutable head, "
            "and ticket identity must all be present"
        )


def _event_binding_from_environment() -> tuple[str, str, str] | None:
    """Return the explicit CI binding, rejecting any incomplete event context."""
    binding = (
        os.environ.get("ONEX_SEAM_CONTRACT_BASE", ""),
        os.environ.get("ONEX_SEAM_CONTRACT_HEAD_REF", ""),
        os.environ.get("ONEX_SEAM_CONTRACT_TICKET_ID", ""),
    )
    if not any(binding):
        return None
    if not all(binding):
        raise BaseResolutionError.incomplete_event_binding()
    return binding


def _get_current_branch() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no git detail"
        raise BaseResolutionError.current_branch_unavailable(detail)
    return result.stdout.strip()


def _git_output(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no git detail"
        raise BaseResolutionError.git_command_failed(args, detail)
    return result.stdout.strip()


def _is_self_tracking_upstream(branch: str, upstream: str) -> bool:
    """Return whether a feature branch tracks itself rather than its PR target."""
    if branch in {"dev", "main", "trunk"}:
        return False
    remote_separator = upstream.find("/")
    return remote_separator >= 0 and upstream[remote_separator + 1 :] == branch


def _resolve_base(explicit_base: str | None) -> str:
    """Resolve an explicit base or the configured, trusted PR-target upstream."""
    if explicit_base is not None:
        base = explicit_base
    else:
        branch = _get_current_branch()
        if branch == "HEAD":
            raise BaseResolutionError.detached_head_requires_base()
        try:
            base = _git_output(["git", "rev-parse", "--abbrev-ref", "@{upstream}"])
        except BaseResolutionError as error:
            raise BaseResolutionError.upstream_unavailable() from error
        if _is_self_tracking_upstream(branch, base):
            raise BaseResolutionError.self_tracking_upstream(branch, base)

    if not TRUSTED_BASE_PATTERN.fullmatch(base):
        raise BaseResolutionError.untrusted_base(base)
    try:
        _git_output(["git", "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"])
    except BaseResolutionError as error:
        raise BaseResolutionError.base_unavailable(base) from error
    return base


def _get_changed_files(base: str, head_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head_ref}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no git detail"
        raise BaseResolutionError.diff_failed(base, detail)
    staged = _git_output(["git", "diff", "--cached", "--name-only"])
    return sorted({*result.stdout.splitlines(), *staged.splitlines()} - {""})


def _seam_files_changed(files: list[str]) -> list[str]:
    return [f for f in files if any(p.search(f) for p in SEAM_PATH_PATTERNS)]


def _extract_ticket_id(branch: str) -> str | None:
    match = TICKET_PATTERN.search(branch)
    if match:
        return match.group(1).upper()
    return None


def _contract_exists(ticket_id: str, contracts_dir: Path) -> bool:
    return (contracts_dir / f"{ticket_id}.yaml").exists()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        help="Base ref to diff against (default: branch upstream; required if absent)",
    )
    parser.add_argument(
        "--head-ref",
        help="Trusted immutable event head; required for detached seam checks",
    )
    parser.add_argument(
        "--ticket-id", help="Trusted event ticket identity for detached seam checks"
    )
    parser.add_argument(
        "--contracts-dir",
        default="contracts",
        help="Path to contracts directory (default: contracts/)",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Print warning but exit 0 even if contract is missing",
    )
    args = parser.parse_args(argv)

    try:
        event_binding = _event_binding_from_environment()
        event_base, event_head_ref, event_ticket_id = event_binding or (None, None, None)
        base = _resolve_base(args.base or event_base)
        branch = _get_current_branch()
        head_ref = args.head_ref or event_head_ref or ("HEAD" if branch != "HEAD" else None)
        if head_ref is None:
            raise BaseResolutionError.detached_head_requires_base()
        _git_output(
            ["git", "rev-parse", "--verify", "--quiet", f"{head_ref}^{{commit}}"]
        )
        changed = _get_changed_files(base, head_ref)
    except BaseResolutionError as error:
        print(f"[ERROR] Seam coverage cannot establish a trustworthy base: {error}")
        return 2

    contracts_dir = Path(args.contracts_dir)
    seam_changed = _seam_files_changed(changed)

    if not seam_changed:
        print("[OK] No interface-surface files changed — no contract required.")
        return 0

    print(f"[INFO] Seam files changed on branch '{branch}':")
    for f in seam_changed:
        print(f"  {f}")

    ticket_id = _extract_ticket_id(args.ticket_id or event_ticket_id or branch)
    if not ticket_id:
        print(f"[ERROR] Cannot establish ticket ID from branch '{branch}'.")
        return 2

    if _contract_exists(ticket_id, contracts_dir):
        print(f"[OK] Contract found: contracts/{ticket_id}.yaml")
        return 0

    msg = (
        f"\n[FAIL] Seam ticket {ticket_id} is missing a contract.\n"
        f"  Interface-surface files were modified but contracts/{ticket_id}.yaml "
        f"does not exist.\n\n"
        f"  To fix: create contracts/{ticket_id}.yaml using the template at\n"
        f"  templates/ticket_contract.template.yaml and set is_seam_ticket: true.\n\n"
        f"  Validate with: uv run validate-yaml contracts/{ticket_id}.yaml\n"
    )
    print(msg)
    return 0 if args.warn_only else 1


if __name__ == "__main__":
    sys.exit(main())
