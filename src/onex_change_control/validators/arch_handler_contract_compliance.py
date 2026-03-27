# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""CI validator: arch-handler-contract-compliance.

Scans all node directories in a repo for handler contract compliance
violations. Exits non-zero if any new (non-allowlisted) violation is found.

Usage:
    uv run python -m onex_change_control.validators.arch_handler_contract_compliance \
        --repo-root . --allowlist-path arch-handler-contract-compliance-allowlist.yaml

    # Generate initial allowlist from current violations
    uv run python -m onex_change_control.validators.arch_handler_contract_compliance \
        --repo-root . --generate-allowlist
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.enums.enum_compliance_verdict import EnumComplianceVerdict
from onex_change_control.scanners.handler_contract_compliance import cross_reference

logger = logging.getLogger(__name__)


def _find_node_dirs(repo_root: Path) -> list[Path]:
    """Find all node directories (containing contract.yaml or handlers/)."""
    src_dir = repo_root / "src"
    if not src_dir.exists():
        return []

    node_dirs: list[Path] = []
    for nodes_dir in src_dir.rglob("nodes"):
        if not nodes_dir.is_dir():
            continue
        for child in sorted(nodes_dir.iterdir()):
            if child.is_dir() and child.name.startswith("node_"):
                handlers_dir = child / "handlers"
                if handlers_dir.exists():
                    node_dirs.append(child)

    return node_dirs


def _load_allowlist(allowlist_path: Path) -> dict[str, list[str]]:
    """Load allowlist YAML, returning handler_path -> list of violation types."""
    if not allowlist_path.exists():
        return {}

    with allowlist_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        return {}

    result: dict[str, list[str]] = {}
    for entry in data.get("allowlisted_handlers", []) or []:
        path = entry.get("path", "")
        violations = entry.get("violations", [])
        if path:
            result[path] = violations

    return result


def _infer_repo_name(repo_root: Path) -> str:
    """Infer repository name from the repo root directory."""
    src_dir = repo_root / "src"
    if src_dir.exists():
        for child in src_dir.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                return child.name
    return repo_root.name


def _write(msg: str) -> None:
    """Write a line to stdout (CI output)."""
    sys.stdout.write(msg + "\n")


def run_scan(
    repo_root: Path,
    allowlist_path: Path | None = None,
    *,
    generate_allowlist: bool = False,
    output_json: bool = False,
) -> int:
    """Run the handler contract compliance scan.

    Returns:
        Exit code: 0 if clean, 1 if violations found.
    """
    repo_name = _infer_repo_name(repo_root)
    node_dirs = _find_node_dirs(repo_root)

    if not node_dirs:
        _write(f"No node directories found in {repo_root}")
        return 0

    # Load allowlist
    allowlist = _load_allowlist(allowlist_path) if allowlist_path else {}
    allowlisted_paths = frozenset(allowlist.keys())

    # Scan all nodes
    all_results = []
    for node_dir in node_dirs:
        results = cross_reference(
            node_dir=node_dir,
            repo=repo_name,
            allowlisted_paths=allowlisted_paths,
        )
        all_results.extend(results)

    if generate_allowlist:
        _output_allowlist(all_results)
        return 0

    if output_json:
        json_output = [r.model_dump(mode="json") for r in all_results]
        _write(json.dumps(json_output, indent=2))

    # Count violations
    new_violations = [r for r in all_results if r.violations and not r.allowlisted]

    compliant = sum(
        1
        for r in all_results
        if not r.violations and r.verdict == EnumComplianceVerdict.COMPLIANT
    )
    allowlisted_count = sum(1 for r in all_results if r.allowlisted)
    total = len(all_results)

    _write(f"\n=== Handler Contract Compliance: {repo_name} ===")
    _write(f"Total handlers: {total}")
    _write(f"Compliant: {compliant}")
    _write(f"Allowlisted: {allowlisted_count}")
    _write(f"New violations: {len(new_violations)}")

    if new_violations:
        _write("\n--- New violations (not allowlisted) ---")
        for r in new_violations:
            _write(f"\n  {r.handler_path}")
            _write(f"    Verdict: {r.verdict}")
            for detail in r.violation_details:
                _write(f"    - {detail}")
        return 1

    return 0


def _output_allowlist(
    results: list[Any],
) -> None:
    """Output current violations as allowlist YAML."""
    entries = []
    for r in results:
        if r.violations:
            entries.append(
                {
                    "path": r.handler_path,
                    "violations": [str(v) for v in r.violations],
                    "ticket": "# migration pending",
                }
            )

    allowlist = {"allowlisted_handlers": entries}
    _write(yaml.dump(allowlist, default_flow_style=False, sort_keys=False))


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(
        description="Handler contract compliance validator"
    )
    parser.add_argument(
        "--repo-root",
        type=pathlib.Path,
        required=True,
        help="Repository root path",
    )
    parser.add_argument(
        "--allowlist-path",
        type=pathlib.Path,
        default=None,
        help="Path to allowlist YAML",
    )
    parser.add_argument(
        "--generate-allowlist",
        action="store_true",
        help="Output current violations as allowlist",
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output JSON report",
    )

    args = parser.parse_args()
    exit_code = run_scan(
        repo_root=args.repo_root,
        allowlist_path=args.allowlist_path,
        generate_allowlist=args.generate_allowlist,
        output_json=args.output_json,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
