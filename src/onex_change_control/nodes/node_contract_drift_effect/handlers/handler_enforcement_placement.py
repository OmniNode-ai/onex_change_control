# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18945: reuse the tripwire's bounded reads for general branch placement."""

from __future__ import annotations

import fnmatch
import json
import shlex
from typing import Any
from urllib.parse import quote

import yaml

from onex_change_control.nodes.node_contract_drift_effect.models import (
    model_enforcement_placement as models,
)
from onex_change_control.scripts import check_platform_leads_review_tripwire as tripwire


def _required_contexts(source: str) -> set[str]:
    """Validate the live response, supporting legacy and App-bound contexts."""
    try:
        protection = json.loads(source)
    except json.JSONDecodeError as exc:
        message = "could not parse governed-branch required_status_checks"
        raise tripwire.TripwireInconclusiveError(message) from exc
    if not isinstance(protection, dict) or not (
        "contexts" in protection or "checks" in protection
    ):
        message = "governed-branch required_status_checks has no contexts or checks"
        raise tripwire.TripwireInconclusiveError(message)
    contexts = protection.get("contexts", [])
    checks = protection.get("checks", [])
    if (
        not isinstance(contexts, list)
        or any(not isinstance(context, str) for context in contexts)
        or not isinstance(checks, list)
        or any(
            not isinstance(check, dict) or not isinstance(check.get("context"), str)
            for check in checks
        )
    ):
        message = "governed-branch required_status_checks has an unreadable shape"
        raise tripwire.TripwireInconclusiveError(message)
    return set(contexts) | {check["context"] for check in checks}


def _command_is_run(job: dict[str, Any], command: str) -> bool:
    """Read executable steps, excluding comments, echo and conditional steps."""
    steps = job.get("steps", [])
    if not isinstance(steps, list):
        return False
    expected = shlex.split(command)
    for step in steps:
        if not isinstance(step, dict) or "if" in step:
            continue
        run = step.get("run", "")
        if not isinstance(run, str):
            continue
        for line in run.replace("\\\n", " ").splitlines():
            try:
                tokens = shlex.split(line, comments=True)
            except ValueError:
                continue
            if not tokens or tokens[0] in {"echo", "printf"}:
                continue
            executable = tokens[2:] if tokens[:2] == ["uv", "run"] else tokens
            if (
                tokens[: len(expected)] == expected
                or executable[: len(expected)] == expected
            ):
                return True
    return False


def _trigger_failures(workflow: dict[str | bool, Any], branch: str) -> list[str]:
    """A required producer must report for every PR to the governed branch."""
    failures: list[str] = []
    # PyYAML's YAML 1.1 reader represents the unquoted Actions `on` key as True.
    triggers = workflow.get("on", workflow.get(True))
    pr = triggers.get("pull_request") if isinstance(triggers, dict) else None
    has_pr = (
        (isinstance(triggers, dict) and "pull_request" in triggers)
        or (isinstance(triggers, list) and "pull_request" in triggers)
        or triggers == "pull_request"
    )
    if not has_pr:
        failures.append("workflow does not run on pull_request")
    if isinstance(pr, dict):
        branches = pr.get("branches", ["*"])
        ignored = pr.get("branches-ignore", [])
        if (
            not isinstance(branches, list)
            or not all(isinstance(pattern, str) for pattern in branches)
            or not _branch_selected(branch, branches)
            or not isinstance(ignored, list)
            or not all(isinstance(pattern, str) for pattern in ignored)
            or any(fnmatch.fnmatchcase(branch, pattern) for pattern in ignored)
        ):
            failures.append(f"workflow excludes pull requests targeting {branch}")
        if "paths" in pr or "paths-ignore" in pr:
            failures.append(
                "required producer can be omitted by a workflow path filter"
            )
    return failures


def _branch_selected(branch: str, patterns: list[str]) -> bool:
    """Actions evaluates positive and negative branch patterns in order."""
    selected = False
    for pattern in patterns:
        excluded = pattern.startswith("!")
        if fnmatch.fnmatchcase(branch, pattern[1:] if excluded else pattern):
            selected = not excluded
    return selected


def _workflow_failures(
    workflow: dict[str | bool, Any],
    surface: models.ModelEnforcementSurface,
    branch: str,
) -> list[str]:
    """Prove the claimed producer runs on that branch and reaches its context."""
    failures = _trigger_failures(workflow, branch)
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        message = "governed-branch workflow has no jobs mapping"
        raise tripwire.TripwireInconclusiveError(message)
    job = jobs.get(surface.job_id)
    if not isinstance(job, dict):
        return [*failures, f"workflow declares no {surface.job_id} job"]
    if "if" in job or "needs" in job:
        failures.append(f"{surface.job_id} can be skipped by if or needs")
    if not _command_is_run(job, surface.command):
        failures.append(f"{surface.job_id} does not execute {surface.command}")
    producer = job
    if surface.rollup_job_id is not None:
        producer = jobs.get(surface.rollup_job_id, {})
        needs = producer.get("needs", []) if isinstance(producer, dict) else []
        needs = [needs] if isinstance(needs, str) else needs
        if not isinstance(needs, list) or surface.job_id not in needs:
            failures.append(f"{surface.rollup_job_id} does not hold {surface.job_id}")
        condition = producer.get("if") if isinstance(producer, dict) else None
        if condition not in {"always()", "${{ always() }}"}:
            failures.append(
                f"{surface.rollup_job_id} does not always evaluate its dependencies"
            )
    producer_id = surface.rollup_job_id or surface.job_id
    if (
        not isinstance(producer, dict)
        or producer.get("name", producer_id) != surface.required_context
    ):
        failures.append(f"producer does not report {surface.required_context}")
    return failures


class HandlerEnforcementPlacement:
    """Check all claims using the same governed branch for every live read."""

    def __init__(
        self, *, credential_origin: str = "unknown", deadline: float | None = None
    ) -> None:
        self._credential_origin = credential_origin
        self._deadline = deadline

    def handle(
        self, request: models.ModelEnforcementPlacementRequest
    ) -> models.ModelEnforcementPlacementResult:
        """An exhausted read budget cannot certify an active placement claim."""
        try:
            return self._check(request)
        except tripwire.TripwireDeferredRateLimitError as exc:
            message = f"enforcement placement was not verified: {exc}"
            raise tripwire.TripwireInconclusiveError(message) from exc

    def _check(
        self, request: models.ModelEnforcementPlacementRequest
    ) -> models.ModelEnforcementPlacementResult:
        branch = quote(request.governed_branch, safe="")
        subject = f"{request.repository}@{request.governed_branch}"
        result = tripwire.run_gh_checked(
            [
                "api",
                f"repos/{request.repository}/branches/{branch}"
                "/protection/required_status_checks",
            ],
            action=f"could not read protection of {subject}",
            credential_origin=self._credential_origin,
            deadline=self._deadline,
        )
        contexts = _required_contexts(result.stdout)
        files: dict[str, str | None] = {}
        failures: list[str] = []
        for surface in request.surfaces:
            for path in (surface.source_path, surface.workflow_path):
                if path not in files:
                    files[path] = tripwire.read_repo_file_at_ref(
                        path,
                        quote(request.governed_branch, safe=""),
                        repo=request.repository,
                        credential_origin=self._credential_origin,
                        deadline=self._deadline,
                    )
            prefix = f"{surface.name} on {request.repository}@{request.governed_branch}"
            if not files[surface.source_path]:
                failures.append(
                    f"{prefix}: absent enforcement surface {surface.source_path}"
                )
            if surface.required_context not in contexts:
                failures.append(
                    f"{prefix}: {surface.required_context} absent "
                    "from live required_status_checks"
                )
            workflow_source = files[surface.workflow_path]
            if not workflow_source:
                failures.append(
                    f"{prefix}: absent producer workflow {surface.workflow_path}"
                )
                continue
            try:
                workflow = yaml.safe_load(workflow_source)
            except yaml.YAMLError as exc:
                message = (
                    f"could not parse {surface.workflow_path}@{request.governed_branch}"
                )
                raise tripwire.TripwireInconclusiveError(message) from exc
            if not isinstance(workflow, dict):
                message = (
                    f"{surface.workflow_path}@{request.governed_branch} "
                    "is not a mapping"
                )
                raise tripwire.TripwireInconclusiveError(message)
            failures.extend(
                f"{prefix}: {failure}"
                for failure in _workflow_failures(
                    workflow, surface, request.governed_branch
                )
            )
        return models.ModelEnforcementPlacementResult(
            repository=request.repository,
            governed_branch=request.governed_branch,
            checked_surface_count=len(request.surfaces),
            failures=tuple(failures),
            passed=not failures,
        )
