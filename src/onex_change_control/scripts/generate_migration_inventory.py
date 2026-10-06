# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Derive ``migration_inventory.yaml`` from the peer repositories' git trees.

OMN-15772, structural fix option (a). The migration inventory used to be
hand-maintained data: every migration added to a peer repo had to be
transcribed into ``migration_inventory.yaml`` by a human who remembered to.
Making the peer clone fail closed (onex_change_control#6635) stopped the gate
*under-reporting*, but a fail-closed gate over a manual dual write does not
prevent divergence -- it only complains sooner.

Here the file lists become a BUILD PRODUCT. The gate's question is no longer
"are these hand-written names right?" but "does the committed artifact equal
what regeneration produces?", which is mechanically answerable and blind to
provenance -- so it also catches drift from force-landed commits,
direct-to-branch pushes, and bot-authored changes that never ran the peer
repo's own CI, none of which a writer-side PR gate can see.

Three files, one boundary each:

``migration_inventory_sources.yaml`` (hand-maintained)
    Which repo, which branch, which directory, plus runner/tracking metadata.
    None of it is readable off a tree, all of it is a deliberate config
    decision.

``migration_inventory.yaml`` (generated -- this script writes it)
    The declared config plus the derived ``*.sql`` file list per set.

``migration_table_annotations.yaml`` (hand-maintained, optional)
    ``tables:`` values. Measured against the peer corpus, a comment-stripped
    ``CREATE|ALTER TABLE``/``INSERT INTO``/``UPDATE`` regex disagreed with 30
    hand-authored entries and lost information in both directions: it misses
    the target of an index-only migration (``CREATE INDEX ... ON <table>``)
    and it adds migration-bookkeeping tables (``db_metadata``,
    ``migrations_log``, ``schema_migrations``) the annotations deliberately
    omit. Gating a whole-file comparison on a heuristic that lossy would turn
    every SQL text edit in a peer into a red check here, so table sets stay
    annotations. A migration with no annotation is not an error.

Ground truth is the declared branch's TREE, never a working tree: the
OMN-15771 reconciliation produced a false ``MISSING_FILE`` purely because a
canonical clone sat 3 commits behind ``origin/dev``.

Usage::

    uv run generate-migration-inventory --repos-root /path/to/repos --check
    uv run generate-migration-inventory --repos-root /path/to/repos --write
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

_BOUNDARIES = Path(__file__).resolve().parent.parent / "boundaries"
DEFAULT_SOURCES = _BOUNDARIES / "migration_inventory_sources.yaml"
DEFAULT_ANNOTATIONS = _BOUNDARIES / "migration_table_annotations.yaml"
DEFAULT_INVENTORY = _BOUNDARIES / "migration_inventory.yaml"

REGEN_COMMAND = "uv run generate-migration-inventory --repos-root <repos-root> --write"

_MIGRATION_SUFFIX = ".sql"

# Plain (unquoted) YAML is emitted only for scalars that can carry no
# structural meaning; everything else is double-quoted. JSON string syntax is a
# strict subset of YAML's double-quoted scalar, so ``json.dumps`` is a correct
# and deterministic quoter here.
_PLAIN_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

_MAX_LINE = 100  # keep every emitted line inside the repo's yamlfmt width


class GenerationError(Exception):
    """A fail-closed precondition was violated. Never write on this path."""


def _scalar(value: object) -> str:
    if value is None:
        return "null"
    text = str(value)
    if _PLAIN_SAFE.match(text):
        return text
    return json.dumps(text)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(  # noqa: S603  Why: fixed argv, no shell.
        ["git", "-C", str(repo), *args],  # noqa: S607  Why: `git` from PATH, repo convention.
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GenerationError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def _resolve_ref(repo_dir: Path, repo: str, branch: str) -> tuple[str, str]:
    """Return ``(ref, sha)`` for ``branch`` in ``repo_dir``.

    A fresh clone (what CI produces) carries ``refs/remotes/origin/<branch>``;
    a canonical local clone carries both. The remote-tracking ref is preferred
    because it is the one a clone cannot have diverged from locally.
    """
    for ref in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}"):
        try:
            sha = _git(
                repo_dir, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"
            )
        except GenerationError:
            continue
        return ref, sha.strip()
    msg = (
        f"{repo}: branch {branch!r} is not present in {repo_dir} "
        f"(looked for refs/remotes/origin/{branch} and refs/heads/{branch})"
    )
    raise GenerationError(msg)


def _require_peer(repos_root: Path, repo: str) -> Path:
    """Fail closed unless the peer is present AND is a git repository.

    An absent peer used to be a WARNING-severity ``MISSING_REPO`` that made the
    validator return early with zero findings -- a failed clone and a
    drift-free peer were indistinguishable downstream. A half-landed clone can
    also leave a populated-but-not-git directory, which is the same class.
    """
    repo_dir = repos_root / repo
    if not repo_dir.exists():
        msg = f"{repo}: expected peer repository not found at {repo_dir}"
        raise GenerationError(msg)
    if not (repo_dir / ".git").exists():
        msg = f"{repo}: {repo_dir} exists but is not a git repository"
        raise GenerationError(msg)
    return repo_dir


def _tree_files(repo_dir: Path, ref: str, directory: str, repo: str) -> list[str]:
    try:
        listing = _git(repo_dir, "ls-tree", "--name-only", f"{ref}:{directory}")
    except GenerationError as exc:
        msg = f"{repo}: cannot read {directory!r} from {ref} ({exc})"
        raise GenerationError(msg) from exc
    return sorted(
        name for name in listing.splitlines() if name.endswith(_MIGRATION_SUFFIX)
    )


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        msg = f"{label} not found at {path}"
        raise GenerationError(msg)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"{label} at {path} is not a mapping"
        raise GenerationError(msg)
    return data


def _emit_tables(indent: str, tables: list[str]) -> list[str]:
    """Flow sequence when it fits the line budget, block sequence otherwise.

    Both shapes are yamlfmt fixed points; a long flow sequence is not, and a
    formatter rewriting the generated artifact would redden the very gate that
    compares them.
    """
    flow = f"{indent}tables: [{', '.join(_scalar(t) for t in tables)}]"
    if len(flow) <= _MAX_LINE:
        return [flow]
    return [f"{indent}tables:", *(f"{indent}  - {_scalar(t)}" for t in tables)]


def render(
    sources: dict[str, Any],
    annotations: dict[str, list[str]],
    resolved: dict[tuple[str, str], list[str]],
) -> str:
    """Render the artifact. Pure function of its arguments."""
    lines = [
        "---",
        "# GENERATED FILE -- DO NOT EDIT BY HAND (OMN-15772).",
        "#",
        "# Migration file lists are DERIVED from each peer repository's declared",
        "# branch. Editing this file by hand will be reverted by the next",
        "# regeneration and rejected by the Migration Inventory Sync gate.",
        "#",
        f"# Regenerate with: {REGEN_COMMAND}",
        "#",
        "# To add a peer repo, retarget a branch, or move a migration directory,",
        "# edit migration_inventory_sources.yaml. To record the tables a migration",
        "# touches, edit migration_table_annotations.yaml. Then regenerate.",
        'version: "1"',
        "description: |",
        "  Cross-repo migration inventory. Maps every SQL migration file to its",
        "  target database, runner mechanism, and (where annotated) the tables it",
        "  creates or modifies. Derived from the peer git trees and validated by",
        "  check_migration_inventory.py in CI.",
        "databases:",
    ]

    for db_name, db_config in sources["databases"].items():
        lines.append(f"  {_scalar(db_name)}:")
        lines.append(f"    connection_env: {_scalar(db_config.get('connection_env'))}")
        lines.append("    migration_sets:")
        for mset in db_config["migration_sets"]:
            repo = str(mset["source_repo"])
            directory = str(mset["directory"])
            lines.append(f"      - source_repo: {_scalar(repo)}")
            lines.append(f"        branch: {_scalar(mset['branch'])}")
            lines.append(f"        directory: {_scalar(directory)}")
            for key in ("runner", "tracking_table", "note"):
                if mset.get(key) is not None:
                    lines.append(f"        {key}: {_scalar(mset[key])}")
            lines.append("        migrations:")
            for fname in resolved[(repo, directory)]:
                lines.append(f"          - file: {_scalar(fname)}")
                tables = annotations.get(f"{repo}/{directory}/{fname}")
                if tables:
                    lines.extend(_emit_tables(" " * 12, tables))

    return "\n".join(lines) + "\n"


def generate(repos_root: Path, sources_path: Path, annotations_path: Path) -> str:
    """Derive the artifact text, or raise ``GenerationError``."""
    sources = _load_yaml(sources_path, "sources config")
    annotations_doc: dict[str, Any] = {}
    if annotations_path.exists():
        annotations_doc = _load_yaml(annotations_path, "annotations")
    annotations = {
        str(k): [str(t) for t in (v or [])]
        for k, v in (annotations_doc.get("annotations") or {}).items()
    }

    sets = [
        mset
        for db_config in sources["databases"].values()
        for mset in db_config["migration_sets"]
    ]
    declared_peers = sorted({str(mset["source_repo"]) for mset in sets})

    resolved: dict[tuple[str, str], list[str]] = {}
    resolved_peers: set[str] = set()
    known_keys: set[str] = set()
    errors: list[str] = []

    for mset in sets:
        repo = str(mset["source_repo"])
        directory = str(mset["directory"])
        branch = str(mset["branch"])
        try:
            repo_dir = _require_peer(repos_root, repo)
            ref, sha = _resolve_ref(repo_dir, repo, branch)
            files = _tree_files(repo_dir, ref, directory, repo)
        except GenerationError as exc:
            errors.append(str(exc))
            continue
        resolved[(repo, directory)] = files
        resolved_peers.add(repo)
        known_keys.update(f"{repo}/{directory}/{f}" for f in files)
        print(
            f"peer {repo}@{branch} ref={ref} sha={sha} "
            f"dir={directory} migrations={len(files)}"
        )

    print(f"peers resolved: {len(resolved_peers)}/{len(declared_peers)} declared")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        msg = (
            f"peers resolved: {len(resolved_peers)}/{len(declared_peers)} declared "
            "-- a partial peer set cannot produce a complete inventory"
        )
        raise GenerationError(msg)

    orphans = sorted(set(annotations) - known_keys)
    if orphans:
        for key in orphans:
            print(
                f"ERROR: stale annotation {key!r} targets a file that is not in "
                "any declared peer tree",
                file=sys.stderr,
            )
        msg = f"{len(orphans)} stale table annotation(s)"
        raise GenerationError(msg)

    return render(sources, annotations, resolved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive migration_inventory.yaml from peer git trees"
    )
    parser.add_argument(
        "--repos-root",
        type=Path,
        required=True,
        help="Root directory containing one clone per peer repo",
    )
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="Compare the committed artifact against regeneration (default)",
    )
    mode.add_argument(
        "--write",
        action="store_true",
        help="Rewrite the committed artifact in place",
    )
    args = parser.parse_args(argv)

    try:
        rendered = generate(args.repos_root, args.sources, args.annotations)
    except GenerationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.write:
        args.inventory.write_text(rendered, encoding="utf-8")
        print(f"Wrote {args.inventory}")
        return 0

    # --check is the default: a CI invocation must never be one missing flag
    # away from silently self-healing the drift it exists to report.
    if not args.inventory.exists():
        print(f"ERROR: {args.inventory} does not exist", file=sys.stderr)
        print(f"Regenerate with: {REGEN_COMMAND}", file=sys.stderr)
        return 1

    committed = args.inventory.read_text(encoding="utf-8")
    if committed == rendered:
        print("Migration inventory: committed artifact matches the peer trees.")
        return 0

    import difflib

    diff = difflib.unified_diff(
        committed.splitlines(keepends=True),
        rendered.splitlines(keepends=True),
        fromfile=f"{args.inventory} (committed)",
        tofile=f"{args.inventory} (regenerated)",
    )
    sys.stdout.writelines(diff)
    print(
        f"\nERROR: {args.inventory} does not match the peer trees it is derived "
        f"from.\nRegenerate and commit: {REGEN_COMMAND}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
