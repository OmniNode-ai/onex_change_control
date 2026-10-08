# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18945: committed enforcement claims resolve on the governed branch."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml
from pydantic import ValidationError

from onex_change_control.scripts import check_platform_leads_review_tripwire as tripwire

pytestmark = pytest.mark.unit
NODE = "onex_change_control.nodes.node_contract_drift_effect"
ROOT = Path(__file__).resolve().parents[2]


def _request(**claim_changes: object) -> Any:
    from onex_change_control.nodes.node_contract_drift_effect.models import (
        model_enforcement_placement,
    )

    claim = {
        "name": "example-refusal",
        "source_path": "src/example.py",
        "workflow_path": ".github/workflows/example.yml",
        "job_id": "refusal",
        "command": "python src/example.py",
        "required_context": "example-refusal",
        **claim_changes,
    }
    return model_enforcement_placement.ModelEnforcementPlacementRequest(
        repository="owner/example", governed_branch="main", surfaces=[claim]
    )


GOOD_WORKFLOW = """\
on:
  pull_request:
    branches: [main]
jobs:
  refusal:
    name: example-refusal
    steps:
      - run: python src/example.py
"""


def _run(
    request: Any = None,
    *,
    source: str | None = "real refusal",
    workflow: str = GOOD_WORKFLOW,
    protection: dict[str, Any] | None = None,
) -> Any:
    from onex_change_control.nodes.node_contract_drift_effect.handlers import (
        handler_enforcement_placement,
    )

    protection = (
        protection if protection is not None else {"contexts": ["example-refusal"]}
    )
    seen: list[tuple[str, str, str]] = []

    def read(path: str, ref: str, **kwargs: Any) -> str | None:
        seen.append((kwargs["repo"], path, ref))
        if path == "src/example.py":
            return source
        return workflow if path == ".github/workflows/example.yml" else None

    with (
        mock.patch.object(tripwire, "_read_repo_file_at_ref", side_effect=read),
        mock.patch.object(
            tripwire,
            "_run_gh_checked",
            return_value=subprocess.CompletedProcess(
                ["gh"], 0, json.dumps(protection), ""
            ),
        ) as protection_read,
    ):
        result = handler_enforcement_placement.HandlerEnforcementPlacement().handle(
            request or _request()
        )
    return result, seen, protection_read


@pytest.mark.parametrize(
    ("failures", "passed"), [((), False), (("absent enforcement surface",), True)]
)
def test_serialized_verdict_cannot_disagree_with_findings(
    failures: tuple[str, ...], *, passed: bool
) -> None:
    from onex_change_control.nodes.node_contract_drift_effect.models import (
        model_enforcement_placement,
    )

    with pytest.raises(ValidationError, match="placement verdict must agree"):
        model_enforcement_placement.ModelEnforcementPlacementResult(
            repository="owner/example",
            governed_branch="main",
            checked_surface_count=1,
            failures=failures,
            passed=passed,
        )


def test_positive_control_checks_a_real_surface_and_live_main_protection() -> None:
    result, seen, protection_read = _run()
    assert result.passed
    assert result.checked_surface_count == 1
    assert result.failures == ()
    assert result.model_dump()["passed"] is True
    assert result.model_dump()["contract_name"] == "enforcement-placement"
    assert {ref for _, _, ref in seen} == {"main"}
    assert {repo for repo, _, _ in seen} == {"owner/example"}
    assert protection_read.call_args.args[0] == [
        "api",
        "repos/owner/example/branches/main/protection/required_status_checks",
    ]


@pytest.mark.parametrize("source", [None, ""])
def test_absent_surface_on_main_fails_even_if_the_checkout_has_it(
    source: str | None,
) -> None:
    result, _, _ = _run(source=source)
    assert not result.passed
    assert "src/example.py" in " ".join(result.failures)
    assert "main" in " ".join(result.failures)


@pytest.mark.parametrize("protection", [{"contexts": []}, {"checks": []}])
def test_a_workflow_comment_or_local_manifest_is_not_live_required_protection(
    protection: dict[str, Any],
) -> None:
    result, _, _ = _run(protection=protection)
    assert not result.passed
    assert "example-refusal" in " ".join(result.failures)


def test_checks_shape_is_a_positive_control_for_app_bound_required_contexts() -> None:
    result, _, _ = _run(
        protection={"checks": [{"context": "example-refusal", "app_id": 42}]}
    )
    assert result.passed


@pytest.mark.parametrize(
    "workflow",
    [
        GOOD_WORKFLOW.replace("branches: [main]", "branches: [dev]"),
        GOOD_WORKFLOW.replace("branches: [main]", "branches: [main, '!main']"),
        GOOD_WORKFLOW.replace("branches: [main]", "branches-ignore: [main]"),
        GOOD_WORKFLOW.replace(
            "branches: [main]", "branches: [main]\n    paths: ['src/**']"
        ),
        GOOD_WORKFLOW.replace("  refusal:\n", "  refusal:\n    if: false\n"),
        GOOD_WORKFLOW.replace(
            "run: python src/example.py",
            "run: |\n          # python src/example.py\n          echo ok",
        ),
        GOOD_WORKFLOW.replace("name: example-refusal", "name: another-check"),
        GOOD_WORKFLOW.replace("python src/example.py", "true python src/example.py"),
    ],
)
def test_a_present_file_with_no_reachable_required_producer_fails(
    workflow: str,
) -> None:
    result, _, _ = _run(workflow=workflow)
    assert not result.passed
    assert "example-refusal" in " ".join(result.failures)


def test_a_required_rollup_must_actually_hold_the_claimed_job() -> None:
    request = _request(required_context="CI Summary", rollup_job_id="summary")
    workflow = (
        GOOD_WORKFLOW
        + "  summary:\n    name: CI Summary\n    if: always()\n    needs: [unrelated]\n"
    )
    result, _, _ = _run(
        request, workflow=workflow, protection={"contexts": ["CI Summary"]}
    )
    assert not result.passed
    assert "refusal" in " ".join(result.failures)
    workflow = workflow.replace("[unrelated]", "[refusal]")
    assert _run(request, workflow=workflow, protection={"contexts": ["CI Summary"]})[
        0
    ].passed


def test_empty_claims_cannot_turn_into_a_vacuous_pass() -> None:
    from onex_change_control.nodes.node_contract_drift_effect.models import (
        model_enforcement_placement,
    )

    with pytest.raises(ValidationError):
        model_enforcement_placement.ModelEnforcementPlacementRequest(
            repository="owner/example", governed_branch="main", surfaces=[]
        )


@pytest.mark.parametrize(
    "protection", [{}, {"contexts": "example-refusal"}, {"checks": [None]}]
)
def test_unreadable_protection_shape_is_inconclusive(
    protection: dict[str, Any],
) -> None:
    with pytest.raises(tripwire.TripwireInconclusiveError):
        _run(protection=protection)


def test_a_permission_failure_propagates_instead_of_passing() -> None:
    from onex_change_control.nodes.node_contract_drift_effect.handlers import (
        handler_enforcement_placement,
    )

    with (
        mock.patch.object(
            tripwire,
            "_run_gh_checked",
            side_effect=tripwire.TripwireInconclusiveError("HTTP 403"),
        ),
        pytest.raises(tripwire.TripwireInconclusiveError),
    ):
        handler_enforcement_placement.HandlerEnforcementPlacement().handle(_request())


def test_committed_claim_is_routed_to_the_handler_over_declared_topics() -> None:
    contract = yaml.safe_load(
        (
            ROOT
            / "src/onex_change_control/nodes/node_contract_drift_effect/contract.yaml"
        ).read_text()
    )
    route = contract["handler_routing"]["handlers"][0]
    assert route["operation"] == "check_enforcement_placement"
    assert route["handler"]["name"] == "HandlerEnforcementPlacement"
    assert contract["event_bus"]["subscribe_topics"]
    assert contract["event_bus"]["publish_topics"]
    claims = contract["enforcement_surfaces"]
    assert claims[0]["source_path"] == tripwire.MAIN_SELF_APPROVAL_MODULE
    assert claims[0]["governed_branch"] == "main"


def test_ci_and_precommit_execute_the_same_focused_regression_file() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["check-platform-leads-review-tripwire"]["steps"]
    test_path = "tests/unit/test_enforcement_surface_placement.py"
    assert any(test_path in step.get("run", "") for step in steps)
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    assert any(
        test_path in hook.get("entry", "")
        for repo in hooks["repos"]
        for hook in repo["hooks"]
    )


def test_existing_tripwire_fails_when_live_protection_loses_its_context() -> None:
    good_module = " ".join(tripwire.MAIN_SELF_APPROVAL_MARKERS)
    workflow = (
        "on: {pull_request: {branches: [main]}}\n"
        "jobs:\n"
        "  prod-promotion-grant-self-approval:\n"
        "    steps:\n"
        "      - run: uv run validate-prod-promotion-grant-self-approval "
        "--requester actor\n"
        "  ci-summary:\n"
        "    name: CI Summary\n"
        "    if: always()\n"
        "    needs: [prod-promotion-grant-self-approval]\n"
    )
    with (
        mock.patch.object(
            tripwire,
            "_read_repo_file_at_ref",
            side_effect=lambda path, _ref, **_kw: (
                good_module if path == tripwire.MAIN_SELF_APPROVAL_MODULE else workflow
            ),
        ),
        mock.patch.object(tripwire, "_run_gh_checked") as protection,
    ):
        protection.return_value = subprocess.CompletedProcess(
            ["gh"], 0, '{"contexts": []}', ""
        )
        ok, detail = tripwire.authoring_time_refusal_present_on_main(
            repo="owner/example"
        )
        assert not ok
        assert "live required_status_checks" in detail
        protection.return_value = subprocess.CompletedProcess(
            ["gh"], 0, '{"contexts": ["CI Summary"]}', ""
        )
        assert tripwire.authoring_time_refusal_present_on_main(repo="owner/example")[0]


def test_a_ref_override_cannot_prove_a_claim_about_a_different_branch() -> None:
    with mock.patch.object(tripwire, "_run_gh_checked") as read:
        ok, detail = tripwire.enforcement_placement_present(
            repo="owner/example", ref="dev"
        )
    assert not ok
    assert "wrong readback branch" in detail
    read.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "/src/example.py",
        "../example.py",
        "src/example.py?ref=dev",
        "src/%2e%2e/example.py",
    ],
)
def test_a_claim_cannot_override_the_governed_branch_query(path: str) -> None:
    with pytest.raises(ValidationError):
        _request(source_path=path)


def test_a_rate_limited_protection_read_is_inconclusive_and_never_passes() -> None:
    from onex_change_control.nodes.node_contract_drift_effect.handlers import (
        handler_enforcement_placement,
    )

    with (
        mock.patch.object(
            tripwire,
            "_run_gh_checked",
            side_effect=tripwire.TripwireDeferredRateLimitError("rate limited"),
        ),
        pytest.raises(tripwire.TripwireInconclusiveError, match="not verified"),
    ):
        handler_enforcement_placement.HandlerEnforcementPlacement().handle(_request())


@pytest.mark.parametrize(("passed", "expected"), [(True, 0), (False, 1)])
def test_precommit_adapter_returns_the_node_verdict(
    *, passed: bool, expected: int
) -> None:
    with mock.patch.object(
        tripwire, "enforcement_placement_present", return_value=(passed, "placement")
    ):
        assert tripwire.main(["--placement-only"]) == expected


def test_precommit_adapter_fails_closed_on_an_unreadable_branch() -> None:
    with mock.patch.object(
        tripwire,
        "enforcement_placement_present",
        side_effect=tripwire.TripwireInconclusiveError("unreadable"),
    ):
        assert tripwire.main(["--placement-only"]) == 2


@pytest.mark.parametrize(
    ("on_main", "expected"), [((False, "absent on main"), 1), (None, 2)]
)
def test_a_deferred_cross_repo_read_cannot_hide_missing_main_placement(
    on_main: tuple[bool, str] | None, expected: int
) -> None:
    with (
        mock.patch.object(
            tripwire, "authoring_time_refusal_behaves", return_value=(True, "behaves")
        ),
        mock.patch.object(
            tripwire, "authoring_time_refusal_wired", return_value=(True, "wired")
        ),
        mock.patch.object(
            tripwire, "authoring_time_refusal_present_on_main"
        ) as main_read,
        mock.patch.object(
            tripwire,
            "promotion_time_refusal_present",
            side_effect=tripwire.TripwireDeferredRateLimitError("rate limited"),
        ),
    ):
        if on_main is None:
            main_read.side_effect = tripwire.TripwireDeferredRateLimitError(
                "rate limited"
            )
        else:
            main_read.return_value = on_main
        assert tripwire.main([]) == expected


def test_one_passing_member_cannot_hide_a_missing_member_and_reads_are_cached() -> None:
    request = _request()
    second = request.surfaces[0].model_copy(
        update={"name": "second", "source_path": "src/missing.py"}
    )
    request = request.model_copy(update={"surfaces": (*request.surfaces, second)})
    result, seen, _ = _run(request)
    assert not result.passed
    assert result.checked_surface_count == 2
    assert "second" in " ".join(result.failures)
    assert "src/missing.py" in " ".join(result.failures)
    assert len(seen) == 3
    second = second.model_copy(update={"source_path": "src/example.py"})
    assert _run(request.model_copy(update={"surfaces": (request.surfaces[0], second)}))[
        0
    ].passed
