#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""check_context_integrity_contracts.py -- CI guard for contract compliance.

Scans all agent YAML configs for ``metadata.context_integrity_contract_id``
and validates that each referenced contract ID exists in the handler contract
registry and declares a ``context_integrity`` subcontract section.

Reports three categories of issues:

* **missing_contract** -- contract_id present in agent YAML but no matching handler
  ``contract.yaml`` found in the registry root.
* **no_context_integrity** -- contract_id resolves to a contract file but that file does
  not declare a ``context_integrity`` subcontract section.
* **stale_reference** -- contract_id is not in the canonical IDs list (when
  ``--registry-list`` is provided).

Exit codes:
    0 -- all checks passed (or ``--dry-run``)
    1 -- one or more issues found
    2 -- usage/argument error

Usage:
    python3 scripts/check_context_integrity_contracts.py \\
        --agents-root plugins/onex/agents/configs \\
        --contracts-root /path/to/node/contracts

    # Dry-run: print findings without failing
    python3 scripts/check_context_integrity_contracts.py \\
        --agents-root plugins/onex/agents/configs \\
        --contracts-root /path/to/node/contracts \\
        --dry-run

    # With a pre-built list of canonical contract IDs (for stale-reference check)
    python3 scripts/check_context_integrity_contracts.py \\
        --agents-root plugins/onex/agents/configs \\
        --contracts-root /path/to/node/contracts \\
        --registry-list registry/context_integrity_contract_ids.txt

OMN-5243
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ContractIssue:
    """A single context integrity contract compliance issue."""

    agent_yaml: str
    contract_id: str
    issue_type: str  # missing_contract | no_context_integrity | stale_reference
    message: str


@dataclass
class CheckResult:
    """Result of the full compliance scan."""

    issues: list[ContractIssue] = field(default_factory=list)
    agents_scanned: int = 0
    agents_with_contract_id: int = 0

    @property
    def is_clean(self) -> bool:
        """Return True when no issues were found."""
        return len(self.issues) == 0


# ---------------------------------------------------------------------------
# Contract registry helpers
# ---------------------------------------------------------------------------


def _build_contract_registry(contracts_root: Path) -> dict[str, Path]:
    """Scan *contracts_root* for handler contract files.

    Searches for all ``contract.yaml`` files under *contracts_root* and builds
    a mapping from the ``name`` or ``contract_name`` field value to the file
    path.  Also indexes by the file's parent directory stem (node name) as a
    fallback key.

    Args:
        contracts_root: Root directory to scan for ``contract.yaml`` files.

    Returns:
        Dict mapping contract ID string to the resolved ``contract.yaml`` path.
    """
    registry: dict[str, Path] = {}

    for contract_path in sorted(contracts_root.rglob("contract.yaml")):
        try:
            with contract_path.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except Exception:  # noqa: BLE001,S112
            continue

        if not isinstance(data, dict):
            continue

        # Index by explicit name fields first
        for key in ("contract_name", "name", "node_name"):
            val = data.get(key)
            if val and isinstance(val, str) and val.strip():
                registry.setdefault(val.strip(), contract_path)

        # Fallback: index by parent directory stem (node directory name)
        node_dir_name = contract_path.parent.name
        registry.setdefault(node_dir_name, contract_path)

    return registry


def _contract_has_context_integrity(contract_path: Path) -> bool:  # noqa: PLR0911
    """Return True if the contract declares a context_integrity section.

    Checks for any of the following YAML patterns:

    * Top-level ``context_integrity:`` key
    * ``subcontracts.context_integrity:`` nested key
    * ``handlers[*].context_integrity:`` within a handlers list

    Args:
        contract_path: Path to a ``contract.yaml`` file.

    Returns:
        True if a ``context_integrity`` declaration is found.
    """
    try:
        with contract_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return False

    if not isinstance(data, dict):
        return False

    # Top-level key
    if "context_integrity" in data:
        return True

    # Nested under subcontracts
    subcontracts = data.get("subcontracts", {})
    if isinstance(subcontracts, dict) and "context_integrity" in subcontracts:
        return True
    if isinstance(subcontracts, list) and "context_integrity" in subcontracts:
        return True

    # Nested under handlers list
    handlers = data.get("handlers", [])
    if isinstance(handlers, list):
        for handler in handlers:
            if isinstance(handler, dict) and "context_integrity" in handler:
                return True

    return False


# ---------------------------------------------------------------------------
# Agent YAML scanning
# ---------------------------------------------------------------------------


def _extract_contract_id(agent_yaml_path: Path) -> str | None:
    """Extract ``metadata.context_integrity_contract_id`` from an agent YAML config.

    Args:
        agent_yaml_path: Path to the agent YAML configuration file.

    Returns:
        The contract ID string, or None if not present or unreadable.
    """
    try:
        with agent_yaml_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(data, dict):
        return None

    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        return None

    val = metadata.get("context_integrity_contract_id")
    if val is None:
        return None
    return str(val).strip() or None


def scan_agent_configs(agents_root: Path) -> list[Path]:
    """Return all ``*.yaml`` files under *agents_root*.

    Args:
        agents_root: Directory containing agent YAML configuration files.

    Returns:
        Sorted list of ``.yaml`` file paths.
    """
    return sorted(agents_root.rglob("*.yaml"))


# ---------------------------------------------------------------------------
# Core check logic
# ---------------------------------------------------------------------------


def run_check(
    agents_root: Path,
    contracts_root: Path,
    registry_ids: list[str] | None = None,
) -> CheckResult:
    """Run the full context integrity contract compliance check.

    Args:
        agents_root: Directory containing agent YAML configuration files.
        contracts_root: Root directory to scan for handler ``contract.yaml`` files.
        registry_ids: Optional pre-built list of canonical contract IDs.  When
            provided, contract IDs not in this list are reported as
            ``stale_reference``.  Pass ``None`` to skip the stale-reference check.

    Returns:
        CheckResult with all discovered issues and scan statistics.
    """
    result = CheckResult()

    # Build contract registry from all contract.yaml files
    registry = _build_contract_registry(contracts_root)

    # Optional canonical ID set for stale-reference check
    canonical_ids: set[str] | None = (
        set(registry_ids) if registry_ids is not None else None
    )

    # Scan agent YAML configs
    agent_files = scan_agent_configs(agents_root)
    result.agents_scanned = len(agent_files)

    for agent_path in agent_files:
        contract_id = _extract_contract_id(agent_path)
        if contract_id is None:
            continue

        result.agents_with_contract_id += 1
        agent_label = str(agent_path)

        # Check 1: stale reference (ID not in canonical list)
        if canonical_ids is not None and contract_id not in canonical_ids:
            result.issues.append(
                ContractIssue(
                    agent_yaml=agent_label,
                    contract_id=contract_id,
                    issue_type="stale_reference",
                    message=(
                        f"contract_id '{contract_id}' not in canonical registry list"
                    ),
                )
            )
            # Do not also flag missing_contract if stale; it may be a typo
            continue

        # Check 2: contract file must exist in the registry
        contract_path = registry.get(contract_id)
        if contract_path is None:
            result.issues.append(
                ContractIssue(
                    agent_yaml=agent_label,
                    contract_id=contract_id,
                    issue_type="missing_contract",
                    message=(
                        f"contract_id '{contract_id}' not found in contract registry"
                        f" (scanned {len(registry)} contract.yaml files"
                        f" under {contracts_root})"
                    ),
                )
            )
            continue

        # Check 3: the resolved contract must declare context_integrity
        if not _contract_has_context_integrity(contract_path):
            result.issues.append(
                ContractIssue(
                    agent_yaml=agent_label,
                    contract_id=contract_id,
                    issue_type="no_context_integrity",
                    message=(
                        f"contract '{contract_id}' at {contract_path} does not declare"
                        " a 'context_integrity' subcontract section"
                    ),
                )
            )

    return result


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _print_result(result: CheckResult, *, dry_run: bool = False) -> None:
    """Print the check result to stdout.

    Args:
        result: The completed check result.
        dry_run: If True, prefix the summary with ``[DRY RUN]``.
    """
    prefix = "[DRY RUN] " if dry_run else ""

    print(
        f"{prefix}Scanned {result.agents_scanned} agent YAML files,"
        f" {result.agents_with_contract_id} with context_integrity_contract_id."
    )

    if result.is_clean:
        print(f"{prefix}OK: all context_integrity contract references are valid.")
        return

    by_type: dict[str, list[ContractIssue]] = {}
    for issue in result.issues:
        by_type.setdefault(issue.issue_type, []).append(issue)

    for issue_type, issues in sorted(by_type.items()):
        print(f"\n{prefix}{issue_type.upper()} ({len(issues)} issue(s)):")
        for issue in issues:
            print(f"  {issue.agent_yaml}")
            print(f"    contract_id: {issue.contract_id}")
            print(f"    {issue.message}")

    print(f"\n{prefix}{len(result.issues)} issue(s) found.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_registry_list(path: Path) -> list[str]:
    """Load a plain-text list of canonical contract IDs (one per line).

    Blank lines and lines starting with ``#`` are ignored.

    Args:
        path: Path to the registry list file.

    Returns:
        List of canonical contract ID strings.
    """
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return lines


def main() -> None:
    """Entry point for the CI validation script."""
    parser = argparse.ArgumentParser(
        description=(
            "CI guard: validate agent YAML context_integrity_contract_id references"
            " against the handler contract registry."
        )
    )
    parser.add_argument(
        "--agents-root",
        type=Path,
        required=True,
        help="Directory containing agent YAML configs (scanned recursively).",
    )
    parser.add_argument(
        "--contracts-root",
        type=Path,
        required=True,
        help="Root directory to scan for handler contract.yaml files.",
    )
    parser.add_argument(
        "--registry-list",
        type=Path,
        default=None,
        help=(
            "Optional plain-text file with canonical contract IDs (one per line)."
            " When provided, IDs not in this list are reported as stale_reference."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print findings without exiting non-zero.",
    )

    args = parser.parse_args()

    agents_root: Path = args.agents_root.resolve()
    contracts_root: Path = args.contracts_root.resolve()

    if not agents_root.is_dir():
        print(
            f"ERROR: --agents-root '{agents_root}' is not a directory",
            file=sys.stderr,
        )
        sys.exit(2)

    if not contracts_root.is_dir():
        print(
            f"ERROR: --contracts-root '{contracts_root}' is not a directory",
            file=sys.stderr,
        )
        sys.exit(2)

    registry_ids: list[str] | None = None
    if args.registry_list is not None:
        list_path: Path = args.registry_list.resolve()
        if not list_path.is_file():
            print(
                f"ERROR: --registry-list '{list_path}' is not a file",
                file=sys.stderr,
            )
            sys.exit(2)
        registry_ids = _load_registry_list(list_path)

    result = run_check(
        agents_root=agents_root,
        contracts_root=contracts_root,
        registry_ids=registry_ids,
    )

    _print_result(result, dry_run=args.dry_run)

    if not result.is_clean and not args.dry_run:
        sys.exit(1)


if __name__ == "__main__":
    main()
