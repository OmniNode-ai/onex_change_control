# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Guard against workflows that open divergent automation PRs (OMN-14778).

The live defect this gate closes
--------------------------------
``.github/workflows/occ-rerun-downstream.yml`` (OCC Downstream Gate Rerun,
epic OMN-10487) triggered ``on: push: branches: [main]``, checked out the
**main** merge commit, cut a fresh branch off that main HEAD
(``git checkout -B automation/occ-rerun-state-<sha8>``) and opened
``gh pr create --base dev``. Because ``dev`` and ``main`` diverge by hundreds of
commits, every such PR carried the full main<->dev divergence (onex_change_control#4396
was 132 files) instead of the intended two-line state update — unmergeable
add/add conflicts, closed by hand, 55 orphan ``automation/occ-rerun-state-*``
branches accumulated. The workflow was retired under OMN-14778; this gate
prevents the anti-pattern from being reintroduced.

Policy
------
A workflow is REJECTED when ALL of the following hold:

1. It is triggered by a ``push`` whose branch filter includes ``main`` (or has no
   branch filter, i.e. every branch including main).
2. Any step cuts a **fresh branch** (``git checkout -B|-b``, ``git switch -c``,
   ``git branch <name>``, ``git push ... HEAD:refs/heads/...``).
3. Any step runs ``gh pr create`` with a **literal** ``--base <X>`` where ``X``
   is not one of the push-trigger branches (e.g. base ``dev`` on a push-to-main
   workflow) — i.e. the PR head is cut from ``main`` but targeted at a divergent
   base.
4. The workflow does NOT first re-base that fresh branch onto ``origin/<X>``
   (no ``git fetch``/``rebase``/``reset``/``checkout`` against ``origin/<X>``).

Condition 4 is the escape hatch: a workflow that legitimately needs to open a PR
to a different base MUST first reset its branch onto that base so the diff is
minimal. A ``--base`` given as a shell variable / ``${{ }}`` expression is treated
as UNKNOWN and never flagged (it cannot be statically proven divergent; e.g.
``nightly-promote.yml`` fans out ``--base "$base"`` and is schedule-triggered).

Tombstone
---------
The two retired workflows of the OMN-10487 dead subsystem
(``occ-rerun-downstream.yml`` and its unwired feeder ``occ-record-dep-edge.yml``)
must not silently return. Their reappearance is a hard failure pointing back to
OMN-14778.

Exit codes: 0 = clean, 1 = one or more violations, 2 = usage error.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# Retired workflows (OMN-14778 / dead OMN-10487 subsystem). Reappearance is a
# hard failure — reopen OMN-14778 before reintroducing either.
RETIRED_WORKFLOW_BASENAMES = frozenset(
    {
        "occ-rerun-downstream.yml",
        "occ-record-dep-edge.yml",
    }
)

_FRESH_BRANCH_PATTERNS = (
    re.compile(r"\bgit\s+checkout\s+-B\b"),
    re.compile(r"\bgit\s+checkout\s+-b\b"),
    re.compile(r"\bgit\s+switch\s+(?:-c|--create)\b"),
    re.compile(r"\bgit\s+branch\s+(?!-)[\w./$'\"{}-]+"),
    re.compile(r"\bgit\s+push\b[^\n]*\bHEAD:refs/heads/"),
)

# gh pr create ... --base <X>  /  --base=<X>  (X captured; may span the same
# logical command across backslash-continued lines).
_PR_CREATE_RE = re.compile(r"gh\s+pr\s+create\b(?P<args>(?:[^\n]|\\\n)*)", re.MULTILINE)
_BASE_RE = re.compile(r"--base(?:=|\s+)(?P<val>[^\s\\]+)")


@dataclass(frozen=True)
class Violation:
    workflow: str
    kind: str
    detail: str


def _load_yaml(path: Path) -> dict[object, object] | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _get_on(data: dict[object, object]) -> object:
    """Return the ``on:`` config, tolerating YAML 1.1 turning ``on`` into True."""
    if "on" in data:
        return data["on"]
    # PyYAML parses the bare key ``on`` as the boolean True.
    return data.get(True)


def _push_branches(on_cfg: object) -> tuple[bool, frozenset[str] | None]:
    """Return (triggers_on_push, branch_filter).

    branch_filter is None when push has no branch restriction (all branches).
    """
    # Bare string form, e.g. ``on: push``.
    if on_cfg == "push":
        return True, None
    # List form, e.g. ``on: [push, pull_request]``.
    if isinstance(on_cfg, list):
        return ("push" in on_cfg), None
    if not isinstance(on_cfg, dict):
        return False, None
    if "push" not in on_cfg:
        return False, None
    push = on_cfg["push"]
    if not isinstance(push, dict):
        # ``push:`` with null / empty value → all branches.
        return True, None
    branches = push.get("branches")
    if branches is None:
        # No positive branch filter (branches-ignore or none) → treat as all.
        return True, None
    if isinstance(branches, str):
        branches = [branches]
    if isinstance(branches, list):
        return True, frozenset(str(b) for b in branches)
    return True, None


def _run_text(data: dict[object, object]) -> str:
    jobs = data.get("jobs")
    if not isinstance(jobs, dict):
        return ""
    chunks: list[str] = []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                chunks.append(step["run"])
    return "\n".join(chunks)


def _creates_fresh_branch(run_text: str) -> bool:
    return any(p.search(run_text) for p in _FRESH_BRANCH_PATTERNS)


def _literal_pr_bases(run_text: str) -> set[str]:
    """Literal ``--base`` values passed to ``gh pr create``.

    Shell variables / GitHub expressions (``$x``, ``${x}``, ``${{ ... }}``) are
    UNKNOWN — dropped, never flagged.
    """
    bases: set[str] = set()
    for m in _PR_CREATE_RE.finditer(run_text):
        for bm in _BASE_RE.finditer(m.group("args")):
            val = bm.group("val").strip("\"'")
            if not val or "$" in val or "{" in val:
                continue  # unresolved expression
            bases.add(val)
    return bases


def _rebases_onto(run_text: str, base: str) -> bool:
    """True if the fresh branch is re-based/reset onto ``origin/<base>``.

    Deliberately lenient: any git reset/rebase/merge/checkout/restore/switch that
    references ``origin/<base>`` counts, so a correctly-authored PR-to-<base>
    workflow is never flagged.
    """
    ref = re.escape(f"origin/{base}")
    verb = r"(?:reset|rebase|merge|checkout|restore|switch|read-tree)"
    verb_then_ref = re.search(rf"\bgit\s+{verb}\b[^\n]*\b{ref}\b", run_text)
    ref_and_verb = re.search(rf"\b{ref}\b", run_text) and re.search(
        rf"\bgit\s+{verb}\b", run_text
    )
    return bool(verb_then_ref or ref_and_verb)


def check_workflow(path: Path) -> list[Violation]:
    violations: list[Violation] = []
    if path.name in RETIRED_WORKFLOW_BASENAMES:
        violations.append(
            Violation(
                workflow=str(path),
                kind="retired-workflow-returned",
                detail=(
                    f"{path.name} was retired under OMN-14778 (dead OMN-10487 "
                    "rerun-downstream subsystem that opened unmergeable main→dev "
                    "PRs). Reopen OMN-14778 before reintroducing it."
                ),
            )
        )
    data = _load_yaml(path)
    if data is None:
        return violations
    triggers_push, branches = _push_branches(_get_on(data))
    if not triggers_push:
        return violations
    main_in_scope = branches is None or "main" in branches
    if not main_in_scope:
        return violations
    run_text = _run_text(data)
    if not _creates_fresh_branch(run_text):
        return violations
    allowed_bases = branches if branches is not None else frozenset({"main"})
    for base in sorted(_literal_pr_bases(run_text)):
        if base in allowed_bases:
            continue
        if _rebases_onto(run_text, base):
            continue
        violations.append(
            Violation(
                workflow=str(path),
                kind="divergent-automation-pr",
                detail=(
                    f"triggers on push to {sorted(allowed_bases)} but cuts a fresh "
                    f"branch and opens `gh pr create --base {base}` without "
                    f"rebasing onto origin/{base}. The PR head is cut from a "
                    f"push-trigger branch and targeted at the divergent base "
                    f"'{base}', so the diff carries the full branch divergence "
                    f"(the OMN-14778 defect). Either target '{base}' only after "
                    f"`git fetch origin {base}` + reset/rebase onto origin/{base}, "
                    f"or target one of {sorted(allowed_bases)}."
                ),
            )
        )
    return violations


def find_violations(workflow_paths: list[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted(workflow_paths):
        violations.extend(check_workflow(path))
    return violations


def _discover_workflows(repo_root: Path) -> list[Path]:
    wf_dir = repo_root / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    return sorted(p for p in wf_dir.iterdir() if p.suffix in {".yml", ".yaml"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reject workflows that open divergent automation PRs "
            "(push-to-main branch cut from main, PR to a divergent base). OMN-14778."
        )
    )
    parser.add_argument(
        "workflows",
        nargs="*",
        type=Path,
        help="Workflow YAML files to check. Default: scan .github/workflows/.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repo root used to locate .github/workflows when no files are given.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan every workflow under .github/workflows (default with no files).",
    )
    args = parser.parse_args(argv)

    if args.workflows and not args.all:
        paths = [p for p in args.workflows if p.suffix in {".yml", ".yaml"}]
    else:
        paths = _discover_workflows(args.repo_root)

    if not paths:
        print("check-no-divergent-automation-pr: no workflow files to check.")
        return 0

    violations = find_violations(paths)
    if not violations:
        print(f"check-no-divergent-automation-pr: OK — {len(paths)} workflow(s) clean.")
        return 0

    print(f"check-no-divergent-automation-pr: {len(violations)} violation(s):")
    for v in violations:
        print(f"  [{v.kind}] {v.workflow}")
        print(f"      {v.detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
