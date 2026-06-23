# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Reference resolver for documentation references.

Takes extracted references and checks whether their targets still exist
in the filesystem, codebase, or environment configuration.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from onex_change_control.enums.enum_doc_reference_type import EnumDocReferenceType
from onex_change_control.models.model_doc_reference import ModelDocReference

# Default wall-clock budget for a full resolve_references pass. Once exceeded the
# resolver stops doing work and returns the remaining references marked
# exists=None (fail-loud, bounded). The old per-ref ``grep -r`` resolver had no
# budget and could run for tens of minutes (OMN-13521).
_DEFAULT_TIME_BUDGET_SECONDS = 120.0

# Directories never worth walking when building the per-repo symbol index.
# Nested worktrees (.claude/worktrees, omni_worktrees) are duplicate copies of
# the canonical tree; walking them re-scans the same source many times over and
# was a major contributor to the non-terminating sweep (OMN-13521).
_INDEX_SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".claude",
        "worktrees",
        "omni_worktrees",
    }
)

# Match a top-level (or indented) ``class Name`` / ``def name`` definition. The
# mandatory whitespace after the keyword keeps us from matching ``classify``
# when looking for ``class``; the captured group is the symbol name.
_CLASS_RE = re.compile(r"^[ \t]*class[ \t]+([A-Za-z_][A-Za-z0-9_]*)")
_DEF_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+([A-Za-z_][A-Za-z0-9_]*)")


def _build_symbol_index(root: str) -> dict[str, str]:
    """Scan ``<root>/src`` once and index symbol name -> first defining file.

    Builds an in-memory map of every ``class Name`` and ``def name`` definition
    under the repo's ``src/`` tree. This replaces the per-reference ``grep -r``
    subprocess: the tree is walked exactly once per repo-root, then every
    reference is resolved in pure Python against the map.

    The first file in which a symbol appears wins (matching the prior
    ``grep -r -l | head -1`` semantics).
    """
    index: dict[str, str] = {}
    src_dir = Path(root) / "src"
    if not src_dir.is_dir():
        return index

    for dirpath, dirnames, filenames in os.walk(src_dir):
        # Prune skip dirs in place so os.walk does not descend into them. This
        # is the load-bearing optimisation: nested worktrees / .venv are never
        # entered, so the canonical tree is scanned exactly once.
        dirnames[:] = [d for d in dirnames if d not in _INDEX_SKIP_DIRS]
        dir_path = Path(dirpath)
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            file_path = dir_path / filename
            try:
                with file_path.open(encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        m = _CLASS_RE.match(line)
                        if m is None:
                            m = _DEF_RE.match(line)
                        if m is None:
                            continue
                        name = m.group(1)
                        # First definition wins.
                        index.setdefault(name, str(file_path))
            except OSError:
                continue
    return index


def _unverified(ref: ModelDocReference) -> ModelDocReference:
    """Return a copy of ``ref`` marked as unverified (budget exhausted)."""
    return ModelDocReference(
        doc_path=ref.doc_path,
        line_number=ref.line_number,
        reference_type=ref.reference_type,
        raw_text=ref.raw_text,
        resolved_target=None,
        exists=None,
    )


def _resolve_class_or_function_indexed(
    ref: ModelDocReference, symbol_indices: list[dict[str, str]]
) -> ModelDocReference:
    """Resolve a class/function reference against prebuilt per-root indices."""
    name = ref.raw_text
    for index in symbol_indices:
        target = index.get(name)
        if target is not None:
            return ModelDocReference(
                doc_path=ref.doc_path,
                line_number=ref.line_number,
                reference_type=ref.reference_type,
                raw_text=ref.raw_text,
                resolved_target=target,
                exists=True,
            )
    return ModelDocReference(
        doc_path=ref.doc_path,
        line_number=ref.line_number,
        reference_type=ref.reference_type,
        raw_text=ref.raw_text,
        resolved_target=None,
        exists=False,
    )


def _resolve_file_path(
    ref: ModelDocReference, repo_roots: list[str]
) -> ModelDocReference:
    """Resolve a file path reference by checking if the file exists."""
    raw = ref.raw_text

    # Try absolute path first
    if Path(raw).is_absolute() and Path(raw).exists():
        return ModelDocReference(
            doc_path=ref.doc_path,
            line_number=ref.line_number,
            reference_type=ref.reference_type,
            raw_text=ref.raw_text,
            resolved_target=raw,
            exists=True,
        )

    # Try relative to each repo root
    for root in repo_roots:
        candidate = Path(root) / raw
        if candidate.exists():
            return ModelDocReference(
                doc_path=ref.doc_path,
                line_number=ref.line_number,
                reference_type=ref.reference_type,
                raw_text=ref.raw_text,
                resolved_target=str(candidate),
                exists=True,
            )

    return ModelDocReference(
        doc_path=ref.doc_path,
        line_number=ref.line_number,
        reference_type=ref.reference_type,
        raw_text=ref.raw_text,
        resolved_target=None,
        exists=False,
    )


def _resolve_env_var(
    ref: ModelDocReference, env_file: str | None = None
) -> ModelDocReference:
    """Resolve an env var by checking ~/.omnibase/.env."""
    var_name = ref.raw_text
    env_path = Path(env_file) if env_file else Path.home() / ".omnibase" / ".env"

    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        for raw_line in content.splitlines():
            stripped_line = raw_line.strip()
            if stripped_line.startswith("#") or "=" not in stripped_line:
                continue
            key = stripped_line.split("=", 1)[0].strip()
            if key == var_name:
                return ModelDocReference(
                    doc_path=ref.doc_path,
                    line_number=ref.line_number,
                    reference_type=ref.reference_type,
                    raw_text=ref.raw_text,
                    resolved_target=str(env_path),
                    exists=True,
                )

    return ModelDocReference(
        doc_path=ref.doc_path,
        line_number=ref.line_number,
        reference_type=ref.reference_type,
        raw_text=ref.raw_text,
        resolved_target=None,
        exists=False,
    )


def _resolve_url(ref: ModelDocReference) -> ModelDocReference:
    """URLs are not checked for liveness -- mark as exists=None."""
    return ModelDocReference(
        doc_path=ref.doc_path,
        line_number=ref.line_number,
        reference_type=ref.reference_type,
        raw_text=ref.raw_text,
        resolved_target=ref.raw_text,
        exists=None,
    )


def _resolve_command(
    ref: ModelDocReference, repo_roots: list[str]
) -> ModelDocReference:
    """Resolve a command reference by checking if referenced paths exist."""
    raw = ref.raw_text
    # Extract file/dir paths from the command
    parts = raw.split()
    found_path = False

    for part in parts:
        if "/" in part and not part.startswith("http"):
            for root in repo_roots:
                candidate = Path(root) / part
                if candidate.exists():
                    found_path = True
                    break
            if found_path:
                break

    # If no paths to check, mark as exists=None (can't verify)
    if not any("/" in p and not p.startswith("http") for p in parts):
        return ModelDocReference(
            doc_path=ref.doc_path,
            line_number=ref.line_number,
            reference_type=ref.reference_type,
            raw_text=ref.raw_text,
            resolved_target=None,
            exists=None,
        )

    return ModelDocReference(
        doc_path=ref.doc_path,
        line_number=ref.line_number,
        reference_type=ref.reference_type,
        raw_text=ref.raw_text,
        resolved_target=None,
        exists=found_path if found_path else False,
    )


def _resolve_single(
    ref: ModelDocReference,
    repo_roots: list[str],
    symbol_indices: list[dict[str, str]],
    env_file: str | None,
) -> ModelDocReference:
    """Dispatch a single reference to its type-specific resolver."""
    if ref.reference_type == EnumDocReferenceType.FILE_PATH:
        return _resolve_file_path(ref, repo_roots)
    if ref.reference_type in (
        EnumDocReferenceType.CLASS_NAME,
        EnumDocReferenceType.FUNCTION_NAME,
    ):
        return _resolve_class_or_function_indexed(ref, symbol_indices)
    if ref.reference_type == EnumDocReferenceType.ENV_VAR:
        return _resolve_env_var(ref, env_file)
    if ref.reference_type == EnumDocReferenceType.URL:
        return _resolve_url(ref)
    if ref.reference_type == EnumDocReferenceType.COMMAND:
        return _resolve_command(ref, repo_roots)
    return ref


def _build_symbol_indices(
    references: list[ModelDocReference],
    repo_roots: list[str],
    deadline: float,
) -> list[dict[str, str]]:
    """Build the per-root symbol index, once, if any class/func refs exist."""
    needs_symbol_index = any(
        ref.reference_type
        in (EnumDocReferenceType.CLASS_NAME, EnumDocReferenceType.FUNCTION_NAME)
        for ref in references
    )
    if not needs_symbol_index:
        return []

    symbol_indices: list[dict[str, str]] = []
    for root in repo_roots:
        if time.monotonic() >= deadline:
            break
        symbol_indices.append(_build_symbol_index(root))
    return symbol_indices


def resolve_references(
    references: list[ModelDocReference],
    repo_roots: list[str],
    env_file: str | None = None,
    time_budget_seconds: float = _DEFAULT_TIME_BUDGET_SECONDS,
) -> list[ModelDocReference]:
    """Resolve all references, populating exists and resolved_target fields.

    Class/function references resolve against an in-memory symbol index built
    **once per repo-root** (one ``os.walk`` over each ``src/`` tree) instead of
    the previous one-``grep -r``-subprocess-per-reference-per-root pass. That
    turns the cost from O(refs x roots) subprocess spawns into O(roots) tree
    walks plus O(refs) dict lookups (OMN-13521).

    A wall-clock ``time_budget_seconds`` bounds the whole pass: once the budget
    is exhausted, any not-yet-resolved reference is returned with
    ``exists=None`` (fail-loud, bounded) rather than letting the resolver run
    for an unbounded number of minutes.

    Args:
        references: List of extracted references to resolve.
        repo_roots: List of repository root directories to search.
        env_file: Optional path to env file (defaults to ~/.omnibase/.env).
        time_budget_seconds: Wall-clock budget for the whole pass. References
            still unresolved when the budget is hit come back ``exists=None``.

    Returns:
        List of resolved references with exists field populated. Always the
        same length and order as ``references``.
    """
    deadline = time.monotonic() + max(time_budget_seconds, 0.0)
    symbol_indices = _build_symbol_indices(references, repo_roots, deadline)

    resolved: list[ModelDocReference] = []
    for ref in references:
        # Budget guard: once exhausted, return remaining refs as unverified.
        if time.monotonic() >= deadline:
            resolved.append(_unverified(ref))
            continue
        resolved.append(_resolve_single(ref, repo_roots, symbol_indices, env_file))

    return resolved
