# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Workspace guard for imperative node/handler code.

This command wraps the handler-contract compliance scanner across one or more
repositories. Existing imperative debt is allowed only when it appears in an
explicit allowlist; any new unallowlisted violation exits non-zero.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from onex_change_control.enums.enum_compliance_verdict import EnumComplianceVerdict
from onex_change_control.enums.enum_reachability import EnumReachability
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
    non_live_violation_count: int
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
            "non_live_violation_count": self.non_live_violation_count,
            "freestanding_scanned": self.freestanding_scanned,
            "freestanding_module_count": self.freestanding_module_count,
            "new_violations": [
                result.model_dump(mode="json")
                for result in self.results
                if result.violations
                and not result.allowlisted
                and result.reachability == EnumReachability.LIVE
            ],
            "non_live_violations": [
                result.model_dump(mode="json")
                for result in self.results
                if result.violations
                and not result.allowlisted
                and result.reachability != EnumReachability.LIVE
            ],
            "freestanding_violations": [
                result.model_dump(mode="json")
                for result in self.freestanding_results
                if result.violations
                and not result.allowlisted
                and result.reachability == EnumReachability.LIVE
            ],
            "non_live_freestanding_violations": [
                result.model_dump(mode="json")
                for result in self.freestanding_results
                if result.violations
                and not result.allowlisted
                and result.reachability != EnumReachability.LIVE
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
        1
        for result in results
        if result.violations
        and not result.allowlisted
        and result.reachability == EnumReachability.LIVE
    ) + sum(
        1
        for result in freestanding_results
        if result.violations
        and not result.allowlisted
        and result.reachability == EnumReachability.LIVE
    )
    non_live_violation_count = sum(
        1
        for result in results
        if result.violations
        and not result.allowlisted
        and result.reachability != EnumReachability.LIVE
    ) + sum(
        1
        for result in freestanding_results
        if result.violations
        and not result.allowlisted
        and result.reachability != EnumReachability.LIVE
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
        non_live_violation_count=non_live_violation_count,
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
    reachability = _classify_module_reachability(repo_root, modules)
    src_dir = repo_root / "src"
    results: list[ModelFreestandingImperativeResult] = []
    for module in modules:
        rel_path = str(module.relative_to(src_dir.parent))
        module_reachability = reachability.get(module, EnumReachability.DEAD)
        result = scan_freestanding_imperative_io(
            module,
            repo=repo_name,
            allowlisted=rel_path in allowlisted_paths,
            reachability=module_reachability,
        )
        results.append(
            result.model_copy(
                update={
                    "module_path": rel_path,
                    "reachability": module_reachability,
                    "findings": [
                        finding.model_copy(update={"reachability": module_reachability})
                        for finding in result.findings
                    ],
                }
            )
        )
    return len(modules), results


def _classify_module_reachability(
    repo_root: Path,
    modules: list[Path],
) -> dict[Path, EnumReachability]:
    """Classify freestanding modules from repo entrypoint import reachability."""
    freestanding_by_name = {
        name: module
        for module in modules
        if (name := _module_name_for_file(repo_root, module)) is not None
    }
    all_modules_by_name = _all_source_modules(repo_root)
    graph = _build_import_graph(all_modules_by_name)
    entrypoints = _collect_entrypoint_modules(repo_root)
    reachable_names = _reachable_modules(entrypoints, graph, all_modules_by_name)
    return {
        module: (
            EnumReachability.TEST_HARNESS
            if _is_test_harness_path(module)
            else (
                EnumReachability.LIVE
                if module_name in reachable_names
                else EnumReachability.DEAD
            )
        )
        for module_name, module in freestanding_by_name.items()
    }


def _all_source_modules(repo_root: Path) -> dict[str, Path]:
    """Return importable module names for all Python source files under src."""
    src_dir = repo_root / "src"
    if not src_dir.exists():
        return {}
    modules: dict[str, Path] = {}
    for path in src_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        name = _module_name_for_file(repo_root, path)
        if name is not None:
            modules[name] = path
    return modules


def _module_name_for_file(repo_root: Path, module: Path) -> str | None:
    """Return the importable module name for a Python file under ``src``."""
    try:
        rel = module.relative_to(repo_root / "src")
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if not parts:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def _is_test_harness_path(path: Path) -> bool:
    parts = set(path.parts)
    return "tests" in parts or path.name.startswith("test_")


def _build_import_graph(module_by_name: dict[str, Path]) -> dict[str, set[str]]:
    """Build an intra-repo import graph keyed by importable module name."""
    graph: dict[str, set[str]] = {name: set() for name in module_by_name}
    for module_name, module_path in module_by_name.items():
        try:
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        imports = _extract_imports(
            tree,
            current_module=module_name,
            is_package=module_path.name == "__init__.py",
        )
        graph[module_name].update(
            target
            for imported in imports
            for target in _resolve_import_target(imported, module_by_name)
        )
    return graph


def _extract_imports(
    tree: ast.AST,
    *,
    current_module: str,
    is_package: bool = False,
) -> set[str]:
    """Extract absolute import targets from a module AST."""
    imports: set[str] = set()
    current_package = (
        current_module
        if is_package
        else current_module.rsplit(".", 1)[0]
        if "." in current_module
        else ""
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from_import_base(
                current_package=current_package,
                module=node.module,
                level=node.level,
            )
            if not base:
                continue
            imports.add(base)
            imports.update(f"{base}.{alias.name}" for alias in node.names)
    return imports


def _resolve_from_import_base(
    *,
    current_package: str,
    module: str | None,
    level: int,
) -> str | None:
    """Resolve an ``ast.ImportFrom`` base module."""
    if level == 0:
        return module
    package_parts = current_package.split(".") if current_package else []
    if level > len(package_parts) + 1:
        return module
    base_parts = package_parts[: len(package_parts) - level + 1]
    if module:
        base_parts.extend(module.split("."))
    return ".".join(part for part in base_parts if part)


def _resolve_import_target(
    imported: str,
    module_by_name: dict[str, Path],
) -> set[str]:
    """Map an import string onto known repo modules."""
    if imported in module_by_name:
        return {imported}
    parts = imported.split(".")
    for end in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in module_by_name:
            return {candidate}
    return set()


def _collect_entrypoint_modules(repo_root: Path) -> set[str]:
    """Collect contract and project-script entrypoints for live reachability."""
    entrypoints: set[str] = set()
    for contract_path in (repo_root / "src").rglob("contract.yaml"):
        entrypoints.update(_contract_entrypoint_modules(contract_path))
        node_py = contract_path.parent / "node.py"
        node_module = _module_name_for_file(repo_root, node_py)
        if node_py.exists() and node_module is not None:
            entrypoints.add(node_module)
    entrypoints.update(_pyproject_entrypoint_modules(repo_root / "pyproject.toml"))
    return entrypoints


def _contract_entrypoint_modules(contract_path: Path) -> set[str]:
    """Extract handler modules declared by a node contract."""
    try:
        data = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return set()
    if not isinstance(data, dict):
        return set()
    modules: set[str] = set()
    routing = data.get("handler_routing")
    if isinstance(routing, dict):
        handlers = routing.get("handlers")
        if isinstance(handlers, list):
            for entry in handlers:
                if not isinstance(entry, dict):
                    continue
                handler = entry.get("handler")
                if isinstance(handler, dict) and isinstance(handler.get("module"), str):
                    modules.add(handler["module"])
    return modules


def _pyproject_entrypoint_modules(pyproject_path: Path) -> set[str]:
    """Extract module roots from pyproject scripts and entry-points."""
    if not pyproject_path.exists():
        return set()
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return set()
    project = data.get("project")
    if not isinstance(project, dict):
        return set()
    modules: set[str] = set()
    scripts = project.get("scripts")
    if isinstance(scripts, dict):
        modules.update(_entrypoint_spec_module(spec) for spec in scripts.values())
    entry_points = project.get("entry-points")
    if isinstance(entry_points, dict):
        for group in entry_points.values():
            if isinstance(group, dict):
                modules.update(_entrypoint_spec_module(spec) for spec in group.values())
    return {module for module in modules if module}


def _entrypoint_spec_module(spec: object) -> str:
    if not isinstance(spec, str):
        return ""
    return spec.split(":", 1)[0].strip()


def _reachable_modules(
    entrypoints: set[str],
    graph: dict[str, set[str]],
    module_by_name: dict[str, Path],
) -> set[str]:
    """Return modules reachable from known entrypoints."""
    roots = {
        target
        for entrypoint in entrypoints
        for target in _resolve_import_target(entrypoint, module_by_name)
    }
    reachable: set[str] = set()
    stack = list(roots)
    while stack:
        current = stack.pop()
        if current in reachable:
            continue
        reachable.add(current)
        stack.extend(sorted(graph.get(current, set()) - reachable))
    return reachable


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


def render_text_report(  # noqa: C901  Why: report has separate live/non-live sections.
    summaries: list[RepoImperativeSummary],
) -> str:
    """Render a human-readable table plus unbaselined violation details."""
    lines = [
        "Imperative contract guard",
        "",
        (
            f"{'repo':28} {'nodes':>5} {'handlers':>8} {'clean':>6} "
            f"{'baseline':>8} {'live':>5} {'nonlive':>7} allowlist"
        ),
        "-" * 102,
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
            f"{summary.non_live_violation_count:7} "
            f"{allowlist}"
        )

    new_results = [
        result
        for summary in summaries
        for result in summary.results
        if result.violations
        and not result.allowlisted
        and result.reachability == EnumReachability.LIVE
    ]
    if new_results:
        lines.extend(["", "Blocking LIVE imperative violations:"])
        for result in new_results:
            lines.append(
                f"- {result.handler_path}: {result.verdict.value} "
                f"({result.reachability.value})"
            )
            for detail in result.violation_details:
                lines.append(f"  - {detail}")

    freestanding = [
        fs_result
        for summary in summaries
        for fs_result in summary.freestanding_results
        if fs_result.violations
        and not fs_result.allowlisted
        and fs_result.reachability == EnumReachability.LIVE
    ]
    if freestanding:
        lines.extend(["", "Blocking LIVE freestanding imperative violations:"])
        for fs_result in freestanding:
            lines.append(
                f"- {fs_result.module_path}: {fs_result.verdict.value} "
                f"({fs_result.reachability.value})"
            )
            for finding in fs_result.active_findings:
                lines.append(
                    f"  - L{finding.line} [{finding.reachability.value}] "
                    f"{finding.detail}"
                )

    non_live = [
        result
        for summary in summaries
        for result in summary.results
        if result.violations
        and not result.allowlisted
        and result.reachability != EnumReachability.LIVE
    ]
    non_live_freestanding = [
        fs_result
        for summary in summaries
        for fs_result in summary.freestanding_results
        if fs_result.violations
        and not fs_result.allowlisted
        and fs_result.reachability != EnumReachability.LIVE
    ]
    if non_live or non_live_freestanding:
        lines.extend(["", "Reported non-live imperative smells:"])
        for result in non_live:
            lines.append(
                f"- {result.handler_path}: {result.verdict.value} "
                f"({result.reachability.value})"
            )
        for fs_result in non_live_freestanding:
            lines.append(
                f"- {fs_result.module_path}: {fs_result.verdict.value} "
                f"({fs_result.reachability.value})"
            )
    return "\n".join(lines)


def render_markdown_report(  # noqa: C901  Why: report has separate live/non-live sections.
    summaries: list[RepoImperativeSummary],
) -> str:
    """Render a Markdown report suitable for durable sweep evidence."""
    lines = [
        "# Imperative Contract Guard Sweep",
        "",
        (
            "| Repo | Nodes | Handlers | Clean | Baselined | Live blockers "
            "| Non-live reported | Allowlist |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
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
            f"| {summary.new_violation_count} | {summary.non_live_violation_count} "
            f"| `{allowlist}` |"
        )

    lines.extend(["", "## Blocking LIVE Violations"])
    new_results = [
        result
        for summary in summaries
        for result in summary.results
        if result.violations
        and not result.allowlisted
        and result.reachability == EnumReachability.LIVE
    ]
    if not new_results:
        lines.append("")
        lines.append("None.")
    for result in new_results:
        lines.append("")
        lines.append(f"### `{result.handler_path}`")
        lines.append("")
        lines.append(f"- Verdict: `{result.verdict.value}`")
        lines.append(f"- Reachability: `{result.reachability.value}`")
        for detail in result.violation_details:
            lines.append(f"- {detail}")

    freestanding = [
        fs_result
        for summary in summaries
        for fs_result in summary.freestanding_results
        if fs_result.violations
        and not fs_result.allowlisted
        and fs_result.reachability == EnumReachability.LIVE
    ]
    if any(summary.freestanding_scanned for summary in summaries):
        lines.extend(["", "## Blocking LIVE Freestanding Violations"])
        if not freestanding:
            lines.extend(["", "None."])
        for fs_result in freestanding:
            lines.append("")
            lines.append(f"### `{fs_result.module_path}`")
            lines.append("")
            lines.append(f"- Verdict: `{fs_result.verdict.value}`")
            lines.append(f"- Reachability: `{fs_result.reachability.value}`")
            for finding in fs_result.active_findings:
                lines.append(
                    f"- L{finding.line} [{finding.reachability.value}] {finding.detail}"
                )

    non_live = [
        result
        for summary in summaries
        for result in summary.results
        if result.violations
        and not result.allowlisted
        and result.reachability != EnumReachability.LIVE
    ]
    non_live_freestanding = [
        fs_result
        for summary in summaries
        for fs_result in summary.freestanding_results
        if fs_result.violations
        and not fs_result.allowlisted
        and fs_result.reachability != EnumReachability.LIVE
    ]
    lines.extend(["", "## Reported Non-live Smells"])
    if not non_live and not non_live_freestanding:
        lines.extend(["", "None."])
    for result in non_live:
        lines.append("")
        lines.append(f"### `{result.handler_path}`")
        lines.append(f"- Verdict: `{result.verdict.value}`")
        lines.append(f"- Reachability: `{result.reachability.value}`")
    for fs_result in non_live_freestanding:
        lines.append("")
        lines.append(f"### `{fs_result.module_path}`")
        lines.append(f"- Verdict: `{fs_result.verdict.value}`")
        lines.append(f"- Reachability: `{fs_result.reachability.value}`")
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
