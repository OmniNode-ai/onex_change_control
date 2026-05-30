# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Workspace guard for imperative node/handler code.

This command wraps the handler-contract compliance scanner across one or more
repositories. Existing imperative debt is allowed only when it appears in an
explicit allowlist; any new unallowlisted violation exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from onex_change_control.enums.enum_compliance_verdict import EnumComplianceVerdict
from onex_change_control.scanners.handler_contract_compliance import (
    cross_reference,
    scan_freestanding_imperative_io,
)
from onex_change_control.validators.arch_handler_contract_compliance import (
    _find_freestanding_modules,
    _find_node_dirs,
    _infer_repo_name,
    _load_allowlist,
)

if TYPE_CHECKING:
    from onex_change_control.models.model_freestanding_imperative_result import (
        ModelFreestandingImperativeResult,
    )
    from onex_change_control.models.model_handler_compliance_result import (
        ModelHandlerComplianceResult,
    )

_SKIP_REPO_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".repowise",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
        "omni_worktrees",
    }
)


@dataclass(frozen=True)
class RepoImperativeSummary:
    """Repo-level imperative-contract scan summary."""

    repo: str
    repo_root: Path
    allowlist_path: Path | None
    node_count: int
    handler_count: int
    compliant_count: int
    allowlisted_count: int
    new_violation_count: int
    results: list[ModelHandlerComplianceResult]
    freestanding_scanned: bool = False
    freestanding_module_count: int = 0
    freestanding_results: list[ModelFreestandingImperativeResult] = field(
        default_factory=list
    )

    def to_json(self) -> dict[str, Any]:
        """Return JSON-serializable summary data."""
        return {
            "repo": self.repo,
            "repo_root": str(self.repo_root),
            "allowlist_path": str(self.allowlist_path) if self.allowlist_path else None,
            "node_count": self.node_count,
            "handler_count": self.handler_count,
            "compliant_count": self.compliant_count,
            "allowlisted_count": self.allowlisted_count,
            "new_violation_count": self.new_violation_count,
            "freestanding_scanned": self.freestanding_scanned,
            "freestanding_module_count": self.freestanding_module_count,
            "new_violations": [
                result.model_dump(mode="json")
                for result in self.results
                if result.violations and not result.allowlisted
            ],
            "freestanding_violations": [
                result.model_dump(mode="json")
                for result in self.freestanding_results
                if result.violations and not result.allowlisted
            ],
        }


def discover_repo_roots(workspace_root: Path) -> list[Path]:
    """Return first-level git repositories under ``workspace_root``."""
    repos: list[Path] = []
    for child in sorted(workspace_root.iterdir(), key=lambda path: path.name):
        if child.name in _SKIP_REPO_DIRS or child.name.startswith("."):
            continue
        if child.is_dir() and (child / ".git").exists():
            repos.append(child)
    return repos


def scan_repo(
    repo_root: Path,
    *,
    allowlists_dir: Path | None = None,
    scan_freestanding: bool = False,
) -> RepoImperativeSummary:
    """Scan a repository and return its imperative-contract summary.

    When ``scan_freestanding`` is set, every ``src/`` module that is not a node
    handler is additionally audited for freestanding imperative IO so coverage
    extends beyond ``node_*/handlers/``.
    """
    repo_root = repo_root.resolve()
    repo_name = _infer_repo_name(repo_root)
    repo_label = repo_root.name
    allowlist_path = _resolve_allowlist_path(
        repo_root=repo_root,
        repo_label=repo_label,
        repo_name=repo_name,
        allowlists_dir=allowlists_dir,
    )
    allowlisted_paths = (
        frozenset(_load_allowlist(allowlist_path).keys())
        if allowlist_path is not None
        else frozenset()
    )

    node_dirs = _find_node_dirs(repo_root)
    results: list[ModelHandlerComplianceResult] = []
    for node_dir in node_dirs:
        results.extend(
            cross_reference(
                node_dir=node_dir,
                repo=repo_name,
                allowlisted_paths=allowlisted_paths,
            )
        )

    freestanding_results: list[ModelFreestandingImperativeResult] = []
    freestanding_module_count = 0
    if scan_freestanding:
        freestanding_module_count, freestanding_results = _scan_freestanding_repo(
            repo_root=repo_root,
            repo_name=repo_name,
            allowlisted_paths=allowlisted_paths,
        )

    new_violation_count = sum(
        1 for result in results if result.violations and not result.allowlisted
    ) + sum(
        1
        for result in freestanding_results
        if result.violations and not result.allowlisted
    )

    return RepoImperativeSummary(
        repo=repo_label,
        repo_root=repo_root,
        allowlist_path=allowlist_path,
        node_count=len(node_dirs),
        handler_count=len(results),
        compliant_count=sum(
            1
            for result in results
            if not result.violations
            and result.verdict == EnumComplianceVerdict.COMPLIANT
        ),
        allowlisted_count=sum(1 for result in results if result.allowlisted)
        + sum(1 for result in freestanding_results if result.allowlisted),
        new_violation_count=new_violation_count,
        results=results,
        freestanding_scanned=scan_freestanding,
        freestanding_module_count=freestanding_module_count,
        freestanding_results=freestanding_results,
    )


def _scan_freestanding_repo(
    *,
    repo_root: Path,
    repo_name: str,
    allowlisted_paths: frozenset[str],
) -> tuple[int, list[ModelFreestandingImperativeResult]]:
    """Audit every freestanding module in a repo and return (count, results)."""
    modules = _find_freestanding_modules(repo_root)
    src_dir = repo_root / "src"
    results: list[ModelFreestandingImperativeResult] = []
    for module in modules:
        rel_path = str(module.relative_to(src_dir.parent))
        result = scan_freestanding_imperative_io(
            module,
            repo=repo_name,
            allowlisted=rel_path in allowlisted_paths,
        )
        results.append(result.model_copy(update={"module_path": rel_path}))
    return len(modules), results


def scan_repos(
    repo_roots: list[Path],
    *,
    allowlists_dir: Path | None = None,
    scan_freestanding: bool = False,
) -> list[RepoImperativeSummary]:
    """Scan multiple repositories in deterministic order."""
    return [
        scan_repo(
            repo_root=repo_root,
            allowlists_dir=allowlists_dir,
            scan_freestanding=scan_freestanding,
        )
        for repo_root in sorted(repo_roots, key=lambda path: path.name)
    ]


def _resolve_allowlist_path(
    *,
    repo_root: Path,
    repo_label: str,
    repo_name: str,
    allowlists_dir: Path | None,
) -> Path | None:
    """Resolve the allowlist path for a repo.

    An explicit central allowlists directory wins so workspace CI can use a
    single controlled baseline. Repo-local allowlists remain the default for
    per-repo CI jobs that do not pass ``--allowlists-dir``.
    """
    if allowlists_dir is None:
        repo_local = repo_root / "arch-handler-contract-compliance-allowlist.yaml"
        return repo_local if repo_local.exists() else None
    for name in (repo_label, repo_name):
        candidate = allowlists_dir / f"{name}.yaml"
        if candidate.exists():
            return candidate
    repo_local = repo_root / "arch-handler-contract-compliance-allowlist.yaml"
    return repo_local if repo_local.exists() else None


def render_text_report(summaries: list[RepoImperativeSummary]) -> str:
    """Render a human-readable table plus unbaselined violation details."""
    lines = [
        "Imperative contract guard",
        "",
        (
            f"{'repo':28} {'nodes':>5} {'handlers':>8} {'clean':>6} "
            f"{'baseline':>8} {'new':>5} allowlist"
        ),
        "-" * 92,
    ]
    for summary in summaries:
        allowlist = (
            str(summary.allowlist_path)
            if summary.allowlist_path is not None
            else "(none)"
        )
        lines.append(
            f"{summary.repo:28} {summary.node_count:5} "
            f"{summary.handler_count:8} {summary.compliant_count:6} "
            f"{summary.allowlisted_count:8} {summary.new_violation_count:5} "
            f"{allowlist}"
        )

    new_results = [
        result
        for summary in summaries
        for result in summary.results
        if result.violations and not result.allowlisted
    ]
    if new_results:
        lines.extend(["", "Unbaselined imperative violations:"])
        for result in new_results:
            lines.append(f"- {result.handler_path}: {result.verdict.value}")
            for detail in result.violation_details:
                lines.append(f"  - {detail}")

    freestanding = [
        fs_result
        for summary in summaries
        for fs_result in summary.freestanding_results
        if fs_result.violations and not fs_result.allowlisted
    ]
    if freestanding:
        lines.extend(["", "Unbaselined freestanding imperative violations:"])
        for fs_result in freestanding:
            lines.append(f"- {fs_result.module_path}: {fs_result.verdict.value}")
            for finding in fs_result.active_findings:
                lines.append(f"  - L{finding.line} {finding.detail}")
    return "\n".join(lines)


def render_markdown_report(summaries: list[RepoImperativeSummary]) -> str:
    """Render a Markdown report suitable for durable sweep evidence."""
    lines = [
        "# Imperative Contract Guard Sweep",
        "",
        "| Repo | Nodes | Handlers | Clean | Baselined | New | Allowlist |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for summary in summaries:
        allowlist = (
            str(summary.allowlist_path)
            if summary.allowlist_path is not None
            else "(none)"
        )
        lines.append(
            f"| `{summary.repo}` | {summary.node_count} | {summary.handler_count} "
            f"| {summary.compliant_count} | {summary.allowlisted_count} "
            f"| {summary.new_violation_count} | `{allowlist}` |"
        )

    lines.extend(["", "## Unbaselined Violations"])
    new_results = [
        result
        for summary in summaries
        for result in summary.results
        if result.violations and not result.allowlisted
    ]
    if not new_results:
        lines.append("")
        lines.append("None.")
    for result in new_results:
        lines.append("")
        lines.append(f"### `{result.handler_path}`")
        lines.append("")
        lines.append(f"- Verdict: `{result.verdict.value}`")
        for detail in result.violation_details:
            lines.append(f"- {detail}")

    freestanding = [
        fs_result
        for summary in summaries
        for fs_result in summary.freestanding_results
        if fs_result.violations and not fs_result.allowlisted
    ]
    if any(summary.freestanding_scanned for summary in summaries):
        lines.extend(["", "## Unbaselined Freestanding Violations"])
        if not freestanding:
            lines.extend(["", "None."])
        for fs_result in freestanding:
            lines.append("")
            lines.append(f"### `{fs_result.module_path}`")
            lines.append("")
            lines.append(f"- Verdict: `{fs_result.verdict.value}`")
            for finding in fs_result.active_findings:
                lines.append(f"- L{finding.line} {finding.detail}")
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check-imperative-contracts",
        description="Fail on unbaselined imperative ONEX node/handler code.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Workspace root containing first-level git repositories.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        action="append",
        default=[],
        help="Repository root to scan. May be passed multiple times.",
    )
    parser.add_argument(
        "--allowlists-dir",
        type=Path,
        help="Directory containing <repo>.yaml handler compliance allowlists.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of text.",
    )
    parser.add_argument(
        "--markdown-report",
        type=Path,
        help="Write a Markdown sweep report to this path.",
    )
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="Report findings but exit zero.",
    )
    parser.add_argument(
        "--scan-freestanding",
        action="store_true",
        help=(
            "Also audit freestanding src/ modules (everything outside "
            "node_*/handlers/) for imperative IO: raw HTTP/Kafka/DB, hardcoded "
            "inference params, topics, LAN IPs, and subprocess network ops."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    repo_roots = list(args.repo_root)
    if args.workspace_root is not None:
        repo_roots.extend(discover_repo_roots(args.workspace_root))
    if not repo_roots:
        print(
            "ERROR: pass --workspace-root or at least one --repo-root",
            file=sys.stderr,
        )
        return 2

    summaries = scan_repos(
        repo_roots=repo_roots,
        allowlists_dir=args.allowlists_dir,
        scan_freestanding=args.scan_freestanding,
    )
    if args.markdown_report is not None:
        args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_report.write_text(render_markdown_report(summaries))

    if args.json:
        print(json.dumps([summary.to_json() for summary in summaries], indent=2))
    else:
        print(render_text_report(summaries))

    if args.no_fail:
        return 0
    return 1 if any(summary.new_violation_count for summary in summaries) else 0


if __name__ == "__main__":
    raise SystemExit(main())
