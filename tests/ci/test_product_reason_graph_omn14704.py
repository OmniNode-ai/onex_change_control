# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Shadow fixtures for the Phase 3 Product Readiness reason-graph (OMN-14704).

These are the onex_change_control slice of the design fixture matrix
(``docs/plans/2026-07-17-product-first-ci-decouple-design.md`` §4). They prove
that a seeded product failure surfaces in Product Readiness as a typed
``PRODUCT_FAILED`` root, that the reason-graph is single-rooted and replay-
deterministic, and — structurally — that the shadow surface has NO ``occ-preflight``
in its needs-chain and mints NO OCC request (it never references
``call-occ-preflight.yml``).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.ci.product_reason_graph import (
    DEPLOY_TRIGGER_FAILED,
    EVIDENCE_MISSING,
    EXIT_PRODUCT_FAILED,
    GITHUB_API_OUTAGE,
    POLICY_HELD,
    PRODUCT_FAILED,
    RUNNER_INFRA,
    STATUS_BLOCKED_UPSTREAM,
    STATUS_FAILED,
    build_reason_graph,
    enforcement_exit_code,
    root_receipt_id,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "product_reason_graph.py"
_SHADOW_WF = _REPO_ROOT / ".github" / "workflows" / "product-readiness-shadow.yml"

_HEAD = "a" * 40


def _green_subchecks() -> dict[str, str]:
    return dict.fromkeys(
        ("change_detection", "lint", "typecheck", "tests", "coverage"), "success"
    )


# --------------------------------------------------------------------------
# Seeded product failures — PRODUCT_FAILED root, OCC-independent.
# --------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failing_check", "expected_signal"),
    [
        ("lint", "lint=failure"),  # seeded-lint-fail-occ
        ("typecheck", "typecheck=failure"),  # seeded-typecheck-fail-occ
        ("tests", "tests=failure"),  # seeded-test-fail-occ
        ("coverage", "coverage=failure"),  # seeded-coverage-fail-occ
    ],
)
def test_seeded_product_failure_roots_as_product_failed(
    failing_check: str, expected_signal: str
) -> None:
    subchecks = _green_subchecks()
    subchecks[failing_check] = "failure"
    graph = build_reason_graph({"head_sha": _HEAD, "subchecks": subchecks})

    assert graph["root"] is not None
    assert graph["root"]["kind"] == PRODUCT_FAILED
    assert graph["root"]["primary_signal"] == expected_signal
    assert graph["blocked_candidate_count"] == 1
    # The failing subcheck is the root's own reporter (independent defect).
    reporter = next(n for n in graph["nodes"] if n["name"] == failing_check)
    assert reporter["status"] == STATUS_FAILED
    assert reporter["is_root"] is True
    assert reporter["root_receipt_id"] == graph["root"]["root_receipt_id"]


@pytest.mark.unit
def test_product_failed_is_occ_independent() -> None:
    # A real product defect surfaces as PRODUCT_FAILED even when OCC eligibility
    # is red — the two dimensions no longer collapse into one another.
    subchecks = _green_subchecks()
    subchecks["tests"] = "failure"
    graph = build_reason_graph(
        {"head_sha": _HEAD, "subchecks": subchecks, "occ_eligibility": "failure"}
    )
    assert graph["root"]["kind"] == PRODUCT_FAILED
    assert graph["root"]["primary_signal"] == "tests=failure"


# --------------------------------------------------------------------------
# Green — single-node graph, freeze-eligible.
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_green_all_pass_is_ready_single_node() -> None:  # green-all-pass-occ
    graph = build_reason_graph({"head_sha": _HEAD, "subchecks": _green_subchecks()})
    assert graph["root"] is None
    assert graph["ready"] is True
    assert graph["freeze_eligible"] is True
    assert graph["blocked_candidate_count"] == 0
    assert graph["blocked_upstream_count"] == 0
    assert all(n["status"] != STATUS_BLOCKED_UPSTREAM for n in graph["nodes"])


# --------------------------------------------------------------------------
# EVIDENCE_MISSING cascade collapse — the CI-01 projection contract.
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_evidence_missing_collapses_cascade_to_one_root() -> None:
    # occ-preflight red while all product checks are SKIPPED (the needs:
    # occ-preflight jobs that never ran). Exactly one EVIDENCE_MISSING root; the
    # M skipped checks are all BLOCKED_UPSTREAM under the SAME receipt id.
    subchecks = dict.fromkeys(
        ("change_detection", "lint", "typecheck", "tests", "coverage"), "skipped"
    )
    graph = build_reason_graph(
        {"head_sha": _HEAD, "subchecks": subchecks, "occ_eligibility": "failure"}
    )
    assert graph["root"]["kind"] == EVIDENCE_MISSING
    assert graph["blocked_candidate_count"] == 1  # not M
    receipt = graph["root"]["root_receipt_id"]
    dependents = [n for n in graph["nodes"] if n["status"] == STATUS_BLOCKED_UPSTREAM]
    assert len(dependents) == 5  # the five skipped product subchecks
    assert all(n["root_receipt_id"] == receipt for n in dependents)


@pytest.mark.unit
def test_absent_occ_input_does_not_fire_evidence_missing() -> None:
    # The product shadow deliberately does not consume OCC; an empty
    # occ_eligibility must NOT invent an EVIDENCE_MISSING root on a green head.
    graph = build_reason_graph(
        {"head_sha": _HEAD, "subchecks": _green_subchecks(), "occ_eligibility": ""}
    )
    assert graph["root"] is None
    assert graph["ready"] is True


# --------------------------------------------------------------------------
# Single-rooting precedence — deterministic arbitration.
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_precedence_infra_and_api_outrank_product_and_evidence() -> None:
    subchecks = _green_subchecks()
    subchecks["lint"] = "failure"
    facts: dict[str, Any] = {
        "head_sha": _HEAD,
        "subchecks": subchecks,
        "occ_eligibility": "failure",
        "policy": "prod-hold",
        "runner_signal": "disk-preflight",
        "gh_api": "5xx",
        "deploy_trigger": "failure",
    }
    graph = build_reason_graph(facts)
    # GITHUB_API_OUTAGE is highest precedence.
    assert graph["root"]["kind"] == GITHUB_API_OUTAGE

    del facts["gh_api"]
    assert build_reason_graph(facts)["root"]["kind"] == RUNNER_INFRA

    del facts["runner_signal"]
    assert build_reason_graph(facts)["root"]["kind"] == POLICY_HELD

    del facts["policy"]
    # occ red + an affirmative product failure -> product wins (EVIDENCE_MISSING
    # only fires when NO product check independently failed).
    assert build_reason_graph(facts)["root"]["kind"] == PRODUCT_FAILED

    facts["subchecks"]["lint"] = "success"
    # now no product failure; occ red -> EVIDENCE_MISSING.
    assert build_reason_graph(facts)["root"]["kind"] == EVIDENCE_MISSING


@pytest.mark.unit
def test_deploy_trigger_failed_is_lowest_precedence() -> None:
    graph = build_reason_graph(
        {
            "head_sha": _HEAD,
            "subchecks": _green_subchecks(),
            "deploy_trigger": "failure",
        }
    )
    assert graph["root"]["kind"] == DEPLOY_TRIGGER_FAILED


# --------------------------------------------------------------------------
# Replay determinism — identical head + facts => identical receipt id.
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_replay_is_byte_identical() -> None:  # replay-determinism
    subchecks = _green_subchecks()
    subchecks["lint"] = "failure"
    facts = {"head_sha": _HEAD, "subchecks": subchecks}
    first = build_reason_graph(dict(facts))
    second = build_reason_graph(dict(facts))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["root"]["root_receipt_id"] == second["root"]["root_receipt_id"]


@pytest.mark.unit
def test_receipt_id_is_head_and_kind_sensitive() -> None:
    a = root_receipt_id(_HEAD, PRODUCT_FAILED, "lint=failure")
    assert len(a) == 16
    assert a != root_receipt_id("b" * 40, PRODUCT_FAILED, "lint=failure")
    assert a != root_receipt_id(_HEAD, PRODUCT_FAILED, "tests=failure")
    assert a != root_receipt_id(_HEAD, EVIDENCE_MISSING, "lint=failure")


@pytest.mark.unit
def test_synchronize_new_head_supersedes_receipt() -> None:  # synchronize-new-head
    subchecks = _green_subchecks()
    subchecks["lint"] = "failure"
    old = build_reason_graph({"head_sha": "a" * 40, "subchecks": subchecks})
    new = build_reason_graph({"head_sha": "b" * 40, "subchecks": subchecks})
    # A new head SHA yields a distinct content-addressed receipt (not stale reuse).
    assert old["root"]["root_receipt_id"] != new["root"]["root_receipt_id"]


# --------------------------------------------------------------------------
# Fail-closed — a skipped/absent product subcheck is never a silent pass.
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_skipped_product_subcheck_without_occ_is_runner_infra() -> None:
    # No OCC-red explanation: an unconfirmable (skipped) product subcheck is a
    # product-dimension infra fault, never freeze-eligible.
    subchecks = _green_subchecks()
    subchecks["tests"] = "skipped"
    graph = build_reason_graph({"head_sha": _HEAD, "subchecks": subchecks})
    assert graph["root"]["kind"] == RUNNER_INFRA
    assert graph["ready"] is False
    assert graph["freeze_eligible"] is False


# --------------------------------------------------------------------------
# CLI surface — report-only, always exit 0.
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_cli_graph_is_report_only_exit_zero_on_red() -> None:
    facts = {"head_sha": _HEAD, "subchecks": {"lint": "failure"}}
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "graph", "--facts-json", json.dumps(facts)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["root"]["kind"] == PRODUCT_FAILED


# --------------------------------------------------------------------------
# ENFORCING mode (OMN-14709) — exit non-zero ONLY on a PRODUCT_FAILED root.
# This is the load-bearing evidence the shadow now enforces on product defects
# while staying non-fatal on non-product roots (and NON-required at the branch-
# protection layer, so it reports without blocking merges).
# --------------------------------------------------------------------------


def _run_graph(facts: dict[str, Any], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "graph",
            "--facts-json",
            json.dumps(facts),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.unit
def test_enforcement_exit_code_nonzero_only_on_product_failed() -> None:
    # Unit-level exit-logic contract: PRODUCT_FAILED -> non-zero; everything
    # else -> 0.
    product_fail = build_reason_graph(
        {"head_sha": _HEAD, "subchecks": {**_green_subchecks(), "tests": "failure"}}
    )
    assert product_fail["root"]["kind"] == PRODUCT_FAILED
    assert enforcement_exit_code(product_fail) == EXIT_PRODUCT_FAILED
    assert EXIT_PRODUCT_FAILED != 0

    green = build_reason_graph({"head_sha": _HEAD, "subchecks": _green_subchecks()})
    assert green["root"] is None
    assert enforcement_exit_code(green) == 0

    # Every non-product root stays non-fatal (exit 0).
    runner_infra = build_reason_graph(
        {"head_sha": _HEAD, "subchecks": {**_green_subchecks(), "tests": "skipped"}}
    )
    assert runner_infra["root"]["kind"] == RUNNER_INFRA
    assert enforcement_exit_code(runner_infra) == 0

    evidence_missing = build_reason_graph(
        {
            "head_sha": _HEAD,
            "subchecks": dict.fromkeys(
                ("change_detection", "lint", "typecheck", "tests", "coverage"),
                "skipped",
            ),
            "occ_eligibility": "failure",
        }
    )
    assert evidence_missing["root"]["kind"] == EVIDENCE_MISSING
    assert enforcement_exit_code(evidence_missing) == 0

    api_outage = build_reason_graph(
        {"head_sha": _HEAD, "subchecks": _green_subchecks(), "gh_api": "5xx"}
    )
    assert api_outage["root"]["kind"] == GITHUB_API_OUTAGE
    assert enforcement_exit_code(api_outage) == 0

    policy_held = build_reason_graph(
        {"head_sha": _HEAD, "subchecks": _green_subchecks(), "policy": "prod-hold"}
    )
    assert policy_held["root"]["kind"] == POLICY_HELD
    assert enforcement_exit_code(policy_held) == 0

    deploy_fail = build_reason_graph(
        {
            "head_sha": _HEAD,
            "subchecks": _green_subchecks(),
            "deploy_trigger": "failure",
        }
    )
    assert deploy_fail["root"]["kind"] == DEPLOY_TRIGGER_FAILED
    assert enforcement_exit_code(deploy_fail) == 0


@pytest.mark.unit
@pytest.mark.parametrize("failing_check", ["lint", "typecheck", "tests", "coverage"])
def test_cli_enforce_product_failed_exits_nonzero(failing_check: str) -> None:
    subchecks = _green_subchecks()
    subchecks[failing_check] = "failure"
    proc = _run_graph({"head_sha": _HEAD, "subchecks": subchecks}, "--enforce")
    assert proc.returncode == EXIT_PRODUCT_FAILED
    # The graph JSON is still emitted on stdout even in the failing path.
    payload = json.loads(proc.stdout)
    assert payload["root"]["kind"] == PRODUCT_FAILED


@pytest.mark.unit
def test_cli_enforce_green_exits_zero() -> None:
    proc = _run_graph({"head_sha": _HEAD, "subchecks": _green_subchecks()}, "--enforce")
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["root"] is None


@pytest.mark.unit
def test_cli_enforce_runner_infra_exits_zero() -> None:
    # A non-product infra root must NOT fail the enforcing shadow.
    subchecks = _green_subchecks()
    subchecks["tests"] = "skipped"
    proc = _run_graph({"head_sha": _HEAD, "subchecks": subchecks}, "--enforce")
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["root"]["kind"] == RUNNER_INFRA


@pytest.mark.unit
def test_cli_enforce_evidence_missing_exits_zero() -> None:
    subchecks = dict.fromkeys(
        ("change_detection", "lint", "typecheck", "tests", "coverage"), "skipped"
    )
    proc = _run_graph(
        {"head_sha": _HEAD, "subchecks": subchecks, "occ_eligibility": "failure"},
        "--enforce",
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["root"]["kind"] == EVIDENCE_MISSING


# --------------------------------------------------------------------------
# STRUCTURAL — the shadow surface never couples to OCC (mints no OCC request).
# --------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    loaded: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


@pytest.mark.unit
def test_shadow_workflow_has_no_occ_preflight_in_needs_chain() -> None:
    wf = _load_yaml(_SHADOW_WF)
    jobs = wf["jobs"]
    for name, job in jobs.items():
        needs = job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "occ-preflight" not in needs, f"job {name} must not need occ-preflight"


@pytest.mark.unit
def test_shadow_workflow_never_triggers_occ_request() -> None:
    text = _SHADOW_WF.read_text(encoding="utf-8")
    # No EXECUTABLE reference to the OCC request minter (comments may name it for
    # documentation; what matters is that no `uses:` line invokes it).
    executable = [
        ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert not any("call-occ-preflight" in ln for ln in executable)
    assert not any("occ-preflight" in ln for ln in executable)


@pytest.mark.unit
def test_shadow_workflow_is_standalone_not_folded_into_ci_summary() -> None:
    # The shadow surface must be its OWN workflow named product-readiness-shadow,
    # NOT wired into ci.yml's required CI Summary rollup (which would make it
    # authoritative and violate the shadow invariant).
    wf = _load_yaml(_SHADOW_WF)
    assert wf["name"] == "product-readiness-shadow"
    assert "reason-graph" in wf["jobs"]
