# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Validate fork-aware GitHub Actions runner selection."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

PUBLIC_SELECTOR = "fromJSON(vars.OMNI_PUBLIC_PR_RUNS_ON_JSON || '[\"ubuntu-latest\"]')"
TRUSTED_SELECTOR = (
    'fromJSON(vars.OMNI_TRUSTED_CI_RUNS_ON_JSON || \'["self-hosted","omnibase-ci"]\')'
)
FORK_AWARE_ROUTE = f"""(github.event_name == 'pull_request' &&
github.event.pull_request.head.repo.full_name != github.repository)
&& {PUBLIC_SELECTOR}
|| {TRUSTED_SELECTOR}"""


def _compact(value: str) -> str:
    return "".join(value.split()).replace("${{", "").replace("}}", "")


def expected_selector(
    event_name: str,
    head_repository: str | None,
    repository: str,
) -> str:
    """Return the selector normal fork-aware CI must use for an event."""
    if event_name == "pull_request" and head_repository != repository:
        return "public"
    return "trusted"


def validate_runs_on(*, workflow_path: str, job_name: str, runs_on: str) -> list[str]:
    """Return policy violations for one job's runner expression."""
    if PUBLIC_SELECTOR not in runs_on:
        return []

    compact_runs_on = _compact(runs_on)
    if "github.base_ref=='dev'" in compact_runs_on or (
        "github.event.pull_request.base.ref=='dev'" in compact_runs_on
    ):
        return [
            f"{workflow_path}:{job_name} uses the forbidden dev-base "
            "public-runner shortcut"
        ]

    if compact_runs_on != _compact(FORK_AWARE_ROUTE):
        return [
            f"{workflow_path}:{job_name} must route only fork pull requests "
            "to the public selector"
        ]
    return []


def validate_workflows(repo_root: Path) -> list[str]:
    """Validate every workflow job that references the public runner selector."""
    violations: list[str] = []
    workflow_dir = repo_root / ".github/workflows"
    workflows = sorted((*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")))
    for workflow in workflows:
        raw_content = workflow.read_text(encoding="utf-8")
        workflow_path = str(workflow.relative_to(repo_root))
        if re.search(r"^  pull_request_target:", raw_content, re.MULTILINE):
            violations.append(
                f"{workflow_path} declares pull_request_target; do not run "
                "untrusted PR code with that event"
            )
        content = yaml.safe_load(raw_content)
        if not isinstance(content, dict):
            continue
        jobs = content.get("jobs", {})
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            runs_on = job.get("runs-on")
            if isinstance(runs_on, str):
                violations.extend(
                    validate_runs_on(
                        workflow_path=workflow_path,
                        job_name=str(job_name),
                        runs_on=runs_on,
                    )
                )
    return violations


def format_violations(violations: list[str]) -> str:
    return "\n".join(f"ERROR: {violation}" for violation in violations)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    violations = validate_workflows(repo_root)
    if violations:
        print(format_violations(violations))
        return 1
    print("Fork-aware runner routing policy passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
