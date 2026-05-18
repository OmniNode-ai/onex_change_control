# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Generate doctrine coverage matrix from doctrine_clauses.yaml.

For each clause, checks whether the declared ci_gate job name exists in
.github/workflows/ and classifies coverage:
  ENFORCED  — ci_gate is declared and the job exists in a workflow file
  ADVISORY  — job exists but clause coverage field says 'advisory'
  UNCOVERED — no ci_gate declared or job not found

Writes docs/standards/doctrine_coverage.md.

Exit code 1 if an enforcement regression is detected (a clause whose
coverage was previously ENFORCED now has no matching CI job).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    current = start.resolve()
    while current != current.parent:
        if (current / ".github").is_dir():
            return current
        current = current.parent
    return start.resolve()


def _load_clauses(repo_root: Path) -> list[dict[str, object]]:
    import yaml  # deferred: yaml is a dep; don't fail at import time for tests

    clauses_path = repo_root / "docs" / "standards" / "doctrine_clauses.yaml"
    raw = yaml.safe_load(clauses_path.read_text())
    return list(raw["clauses"])


def _collect_job_names(workflows_dir: Path) -> set[str]:
    """Collect CI identifiers from all workflow YAML files.

    Collects three kinds of identifiers to match diverse ci_gate formats:
    - Top-level workflow `name:` (e.g. "Receipt Gate")
    - Job `name:` fields indented 4 spaces (e.g. "Run Receipt-Gate")
    - Job key identifiers under `jobs:` (e.g. "verify")
    """
    identifiers: set[str] = set()
    # Workflow-level name: "^name: ..."
    wf_name_re = re.compile(r"^name:\s+(.+)$", re.MULTILINE)
    # Job-level name: indented 4 spaces
    job_name_re = re.compile(r"^\s{4}name:\s+(.+)$", re.MULTILINE)
    # Job key: top-level key under jobs block (indented 2 spaces, ends with colon)
    job_key_re = re.compile(r"^\s{2}([a-zA-Z0-9_-]+):\s*$", re.MULTILINE)

    for wf_file in sorted(workflows_dir.glob("*.yml")):
        text = wf_file.read_text()
        for m in wf_name_re.finditer(text):
            identifiers.add(m.group(1).strip())
        for m in job_name_re.finditer(text):
            identifiers.add(m.group(1).strip())
        for m in job_key_re.finditer(text):
            identifiers.add(m.group(1).strip())
    return identifiers


def _effective_coverage(
    clause: dict[str, object],
    job_names: set[str],
) -> str:
    """Compute effective coverage given observed CI job names."""
    ci_gate = clause.get("ci_gate")
    declared = str(clause.get("coverage", "uncovered"))

    if not ci_gate:
        return "UNCOVERED"

    # ci_gate: "workflow_name/job_name", "job_key/job_name", or "job_name".
    # Match if any slash-separated part appears in the collected identifiers.
    gate_str = str(ci_gate)
    gate_parts = [p.strip() for p in gate_str.split("/") if p.strip()]
    found = any(
        any(part in identifier or identifier.endswith(part) for identifier in job_names)
        for part in gate_parts
    )
    if found:
        if declared == "advisory":
            return "ADVISORY"
        return "ENFORCED"
    return "UNCOVERED"


def _detect_regression(
    clauses: list[dict[str, object]],
    effective: dict[str, str],
) -> list[str]:
    """Return list of clause_ids where declared=enforced but effective=UNCOVERED."""
    regressions = []
    for clause in clauses:
        cid = str(clause["clause_id"])
        if (
            str(clause.get("coverage", "uncovered")) == "enforced"
            and effective.get(cid) == "UNCOVERED"
        ):
            regressions.append(cid)
    return regressions


def _coverage_badge(status: str) -> str:
    return {
        "ENFORCED": "✅ ENFORCED",
        "ADVISORY": "⚠️ ADVISORY",
        "UNCOVERED": "❌ UNCOVERED",
    }.get(status, status)


def generate_coverage_table(
    clauses: list[dict[str, object]],
    job_names: set[str],
) -> tuple[str, dict[str, str]]:
    """Generate the markdown table and return (markdown, effective_map)."""
    effective: dict[str, str] = {}
    for clause in clauses:
        cid = str(clause["clause_id"])
        effective[cid] = _effective_coverage(clause, job_names)

    rows = ["| Clause | Title | CI Gate | Coverage |", "| --- | --- | --- | --- |"]
    for clause in clauses:
        cid = str(clause["clause_id"])
        title = str(clause.get("title", ""))
        ci_gate = clause.get("ci_gate") or "—"
        rows.append(
            f"| {cid} | {title} | {ci_gate} | {_coverage_badge(effective[cid])} |"
        )

    summary = {
        "enforced": sum(1 for v in effective.values() if v == "ENFORCED"),
        "advisory": sum(1 for v in effective.values() if v == "ADVISORY"),
        "uncovered": sum(1 for v in effective.values() if v == "UNCOVERED"),
    }

    md_lines = [
        "# Doctrine Coverage Matrix",
        "",
        "Auto-generated by `scripts/generate_doctrine_coverage.py`."
        " Do not edit manually.",
        "",
        f"**{summary['enforced']} ENFORCED** | "
        f"**{summary['advisory']} ADVISORY** | "
        f"**{summary['uncovered']} UNCOVERED**",
        "",
        "\n".join(rows),
        "",
    ]
    return "\n".join(md_lines), effective


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate doctrine coverage matrix")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repo root (default: auto-detect from __file__)",
    )
    parser.add_argument(
        "--check-regression",
        action="store_true",
        default=False,
        help="Exit 1 if a previously ENFORCED clause lost its CI gate",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print output without writing file",
    )
    args = parser.parse_args(argv)

    repo_root = (
        args.repo_root
        if args.repo_root is not None
        else _find_repo_root(Path(__file__))
    )
    workflows_dir = repo_root / ".github" / "workflows"

    clauses = _load_clauses(repo_root)
    job_names = _collect_job_names(workflows_dir)
    markdown, effective = generate_coverage_table(clauses, job_names)

    if args.dry_run:
        print(markdown)
    else:
        out_path = repo_root / "docs" / "standards" / "doctrine_coverage.md"
        out_path.write_text(markdown)
        print(f"Written: {out_path}")

    regressions = _detect_regression(clauses, effective)
    if args.check_regression and regressions:
        regression_list = ", ".join(regressions)
        n = len(regressions)
        print(
            f"ENFORCEMENT REGRESSION: {n} clause(s) lost CI gate: {regression_list}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
