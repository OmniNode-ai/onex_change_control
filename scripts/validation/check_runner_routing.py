# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Validate fork-aware GitHub Actions runner selection and index-host policy."""

from __future__ import annotations

import json
import os
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

# OMN-16682. Package-index env vars are the channel through which a workflow
# can pin a job to a specific PyPI index. Setting any of them to a tailnet-only
# host in workflow YAML is a defect regardless of how the expression is
# guarded, because workflow expressions cannot read runner class: the `runner`
# context is not available in `jobs.<id>.env`, so every such guard is really
# keyed on PR origin or event name, which diverges from runner class the moment
# vars.OMNI_TRUSTED_CI_RUNS_ON_JSON is flipped to a GitHub-hosted value.
#
# Index selection on the self-hosted fleet is owned by exactly one canonical
# mechanism instead: the fleet's runner-job-started.sh `wire_pypi_cache` hook
# (omnibase_infra docker/runners/). It runs only on the fleet, so it is keyed
# on runner class by construction; it HEAD-probes the cache and fails open to
# pypi.org; and it exports UV_INDEX (an extra index) rather than
# UV_DEFAULT_INDEX, so lockfiles recorded against pypi.org stay valid.
INDEX_ENV_VARS = (
    "UV_DEFAULT_INDEX",
    "UV_INDEX",
    "UV_INDEX_URL",
    "UV_EXTRA_INDEX_URL",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
)
# The org's Tailscale tailnet. Any host under it is unreachable from a
# GitHub-hosted runner.
TAILNET_SUFFIX = "tail75df5e.ts.net"


def _compact(value: str) -> str:
    return "".join(value.split()).replace("${{", "").replace("}}", "")


def validate_index_env(*, workflow_path: str, job_name: str, env: object) -> list[str]:
    """Return violations for index env vars pinned to a tailnet-only host.

    Applies to any mapping of env vars — job-level `env:` or a step's `env:`.
    """
    if not isinstance(env, dict):
        return []
    violations: list[str] = []
    for key, value in env.items():
        if str(key) not in INDEX_ENV_VARS:
            continue
        if not isinstance(value, str):
            continue
        if TAILNET_SUFFIX not in value:
            continue
        violations.append(
            f"{workflow_path}:{job_name} sets {key} to the tailnet-only index "
            f"host {TAILNET_SUFFIX}. Workflow expressions cannot read runner "
            "class, so this pins a GitHub-hosted runner to an unreachable "
            "index whenever OMNI_TRUSTED_CI_RUNS_ON_JSON is flipped "
            "(OMN-16682). Remove it and let the self-hosted fleet's "
            "runner-job-started.sh hook select the cache."
        )
    return violations


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


def hardcoded_label_violations(
    *, workflow_path: str, job_name: str, runs_on: object, labels: set[str]
) -> list[str]:
    """Return violations for a runner label hard-coded into a runs-on value.

    OMN-18031. Every job must resolve its runner through a centralized selector
    variable, so a label appearing literally in ``runs-on:`` bypasses that seam.

    WHY THIS READS THE PARSED VALUE. The check this replaces lived inline in
    ``.github/workflows/omni-standards-compliance.yml`` and ran
    ``grep -RIn <label> .github/workflows`` over whole files, filtering only
    lines that carried one of the two selector expressions. It therefore matched
    the label ANYWHERE, prose included. Its label set is built from the live
    routing variables, which made it self-referential: when
    ``OMNI_TRUSTED_CI_RUNS_ON_JSON`` gained ``self-hosted`` at
    2026-09-11T21:12:33Z it began failing on a comment in
    ``release-staleness-gate.yml`` reading "not contend for the same
    self-hosted runners", untouched since 2026-09-07. ``CI Naming Convention``
    went red, the required ``CI Summary`` failed with it, and no OCC companion
    could merge -- which blocked every product PR that needs one. A gate that
    forbids a label by grepping prose will always fire on documentation about
    that label.

    Reading the YAML-parsed ``runs-on`` value instead makes comments, ``name:``
    fields and step scripts structurally out of scope, and it handles every
    shape the value takes -- inline scalar, flow sequence, block sequence, and
    the folded block scalar that most of this repo's declarations use -- because
    the YAML parser has already resolved them.
    """
    if isinstance(runs_on, str):
        text = runs_on
    elif isinstance(runs_on, list):
        text = " ".join(str(item) for item in runs_on)
    else:
        # A shape this function cannot read must be reported, never skipped.
        # Returning [] here would make the gate quietly stop gating that job,
        # which is how the grep it replaced passed for weeks while matching
        # the wrong thing. `uses:` jobs legitimately carry no runs-on and are
        # filtered out by the caller before reaching this point.
        return [
            f"{workflow_path}:{job_name} has a runs-on this gate cannot read "
            f"(type {type(runs_on).__name__}); it cannot be checked for a "
            "hard-coded runner label, so it is rejected rather than skipped"
        ]

    if PUBLIC_SELECTOR in text or TRUSTED_SELECTOR in text:
        # The label text inside a selector expression is that variable's
        # FALLBACK, not a hard-coded choice.
        return []

    return [
        f"{workflow_path}:{job_name} hard-codes the runner label "
        f"{label!r} in its runs-on value; route it through "
        "vars.OMNI_TRUSTED_CI_RUNS_ON_JSON or "
        "vars.OMNI_PUBLIC_PR_RUNS_ON_JSON instead"
        for label in sorted(labels)
        if label in text
    ]


def routing_labels_from_env() -> set[str]:
    """Labels the live selector variables currently resolve to.

    Empty when the variables are absent. The omni-standards-compliance workflow
    step fails closed on absent variables BEFORE calling this script, so CI can
    never silently skip the check; pre-commit runs without them and exercises
    the routing and index-host checks only. That asymmetry is deliberate and is
    why the workflow keeps its own guard rather than delegating it here.
    """
    labels: set[str] = set()
    for name in ("OMNI_PUBLIC_PR_RUNS_ON_JSON", "OMNI_TRUSTED_CI_RUNS_ON_JSON"):
        raw = os.environ.get(name, "")
        if not raw:
            continue
        labels.update(json.loads(raw))
    return labels


def _validate_job(
    *,
    workflow_path: str,
    job_name: str,
    job: dict[str, object],
    labels: set[str],
) -> list[str]:
    """Return every routing and index-host violation for one job."""
    violations: list[str] = []
    runs_on = job.get("runs-on")
    if isinstance(runs_on, str):
        violations.extend(
            validate_runs_on(
                workflow_path=workflow_path, job_name=job_name, runs_on=runs_on
            )
        )
    # A job that delegates to a reusable workflow (`uses:`) declares no
    # runs-on of its own; the callee owns placement. Every other job must
    # declare one this gate can read.
    if "uses" not in job:
        violations.extend(
            hardcoded_label_violations(
                workflow_path=workflow_path,
                job_name=job_name,
                runs_on=runs_on,
                labels=labels,
            )
        )
    # OMN-16682: job-level env, plus every step's env, since a step env pins
    # the index just as effectively as a job env does.
    violations.extend(
        validate_index_env(
            workflow_path=workflow_path, job_name=job_name, env=job.get("env")
        )
    )
    steps = job.get("steps")
    if not isinstance(steps, list):
        return violations
    for step in steps:
        if not isinstance(step, dict):
            continue
        violations.extend(
            validate_index_env(
                workflow_path=workflow_path, job_name=job_name, env=step.get("env")
            )
        )
    return violations


def validate_workflows(repo_root: Path, labels: set[str] | None = None) -> list[str]:
    """Validate every workflow job that references the public runner selector.

    ``labels`` defaults to whatever the live selector variables resolve to.
    """
    if labels is None:
        labels = routing_labels_from_env()
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
            violations.extend(
                _validate_job(
                    workflow_path=workflow_path,
                    job_name=str(job_name),
                    job=job,
                    labels=labels,
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
    print("Fork-aware runner routing and index-host policy passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
