#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Typed CI reason-graph emitter (OMN-14704 / OMN-14644 Phase 3, CI-01).

Root cause this module addresses
--------------------------------
When one prerequisite (``occ-preflight``) fails, dozens of ``needs: occ-preflight``
product and governance jobs skip or fail, and the projection shows *N failed
checks* instead of *one root cause*. This emitter collapses a run into exactly
one dominant reason-graph root plus ``BLOCKED_UPSTREAM`` dependents that all
point back at the root, so a 30-check cascade projects as **one** blocked
candidate — not thirty.

``onex_change_control`` has **no** ``needs: occ-preflight`` product jobs of its
own (``occ-preflight / eligibility`` is a standalone required context that gates
nothing downstream). Rather than depend on an omnimarket-only WS1 classifier
module, this file is **self-contained**: it inlines the small deterministic
product classifier it needs and layers the reason-graph on top. The public
surface (``build_reason_graph``, ``root_receipt_id``, the six root-kind
constants, the ``STATUS_*`` constants) is byte-for-byte the same contract as the
omnimarket canary (``omnimarket#1796``) so the reason-graph is fleet-uniform.
See design ``docs/plans/2026-07-17-product-first-ci-decouple-design.md`` §2.

Design invariants
-----------------
- **No network I/O. Stdlib only.** Runs under a bare ``setup-python`` step; the
  workflow resolves sibling conclusions and passes them in. This module only
  classifies and hashes.
- **Single-rooted + deterministic.** When several signals are present the
  dominant root is chosen by a fixed precedence, so replay yields an identical
  graph. The root carries a content-addressed receipt id
  ``root_receipt_id = sha256(head_sha || root_kind || primary_signal)[:16]``.
- **Fail closed.** An unconfirmable product subcheck (skipped/cancelled/absent)
  never masquerades as a pass; it becomes a ``RUNNER_INFRA`` root (product
  dimension) or a ``BLOCKED_UPSTREAM`` dependent under a non-product root.
- **Product defects are OCC-independent.** ``EVIDENCE_MISSING`` fires ONLY when
  OCC eligibility is observed red/absent *and* no product check independently
  failed. A real product defect therefore surfaces as ``PRODUCT_FAILED``
  regardless of OCC state — the two dimensions no longer collapse into one
  another.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ci-reason-graph/v1"

# --- Product classifier vocabulary (inlined, deterministic, fail-closed) -----
# String values mirror the canonical omnimarket EnumProductReadinessOutcome so
# the reason-graph product dimension is fleet-uniform.
PRODUCT_GREEN = "product_green"
CHANGE_DETECTION_FAILED = "change_detection_failed"
LINT_FAILED = "lint_failed"
TYPE_FAILED = "type_failed"
TEST_FAILED = "test_failed"
COVERAGE_FAILED = "coverage_failed"
PRODUCT_INFRA = "product_infra"


class EnumSubcheckOutcome(StrEnum):
    """Coarse category a raw GitHub check conclusion maps to."""

    PASS = "pass"  # noqa: S105 - CI outcome label, not a credential
    FAIL = "fail"
    INFRA = "infra"
    ABSENT = "absent"


_PASS_CONCLUSIONS = frozenset({"success", "neutral"})
_FAIL_CONCLUSIONS = frozenset({"failure", "action_required"})
_INFRA_CONCLUSIONS = frozenset(
    {
        "cancelled",
        "canceled",
        "timed_out",
        "startup_failure",
        "stale",
        "skipped",  # a path-filtered/administrative skip is fail-closed, never a pass
    }
)
_ABSENT_CONCLUSIONS = frozenset(
    {
        "",
        "none",
        "null",
        "pending",
        "queued",
        "in_progress",
        "waiting",
        "expected",
        "requested",
    }
)

# The product subchecks in fixed precedence order. change-detection is first
# (its output gates the others); coverage is last (it depends on tests). When
# several fail, the first failing subcheck in this order names the outcome.
_SUBCHECK_ORDER: tuple[tuple[str, str], ...] = (
    ("change_detection", CHANGE_DETECTION_FAILED),
    ("lint", LINT_FAILED),
    ("typecheck", TYPE_FAILED),
    ("tests", TEST_FAILED),
    ("coverage", COVERAGE_FAILED),
)

_PRODUCT_SUBCHECKS: tuple[str, ...] = tuple(name for name, _ in _SUBCHECK_ORDER)


def categorize_conclusion(conclusion: str | None) -> EnumSubcheckOutcome:
    """Map a raw GitHub check conclusion to a coarse outcome (fail-closed)."""
    value = (conclusion or "").strip().lower()
    if value in _PASS_CONCLUSIONS:
        return EnumSubcheckOutcome.PASS
    if value in _FAIL_CONCLUSIONS:
        return EnumSubcheckOutcome.FAIL
    if value in _ABSENT_CONCLUSIONS:
        return EnumSubcheckOutcome.ABSENT
    if value in _INFRA_CONCLUSIONS:
        return EnumSubcheckOutcome.INFRA
    # Fail closed: an unrecognized conclusion is treated as infra, not a pass.
    return EnumSubcheckOutcome.INFRA


def classify_product(subchecks_raw: dict[str, Any]) -> tuple[str, bool]:
    """Classify the product dimension into (outcome, freeze_eligible).

    ``product_green`` (freeze-eligible) is returned only when every subcheck is
    affirmatively ``PASS``. An affirmative ``FAIL`` names the outcome by fixed
    precedence; an ``INFRA``/``ABSENT`` subcheck (with no affirmative failure)
    fails closed to ``product_infra`` — never green.
    """
    cats = {
        name: categorize_conclusion(subchecks_raw.get(name))
        for name in _PRODUCT_SUBCHECKS
    }
    # Affirmative product failures first, in fixed precedence.
    for name, outcome_code in _SUBCHECK_ORDER:
        if cats[name] is EnumSubcheckOutcome.FAIL:
            return outcome_code, False
    # No affirmative failure — any unconfirmed subcheck fails closed.
    if any(
        cats[name] in (EnumSubcheckOutcome.INFRA, EnumSubcheckOutcome.ABSENT)
        for name in _PRODUCT_SUBCHECKS
    ):
        return PRODUCT_INFRA, False
    return PRODUCT_GREEN, True


# --- Root kinds (the six typed causes) -------------------------------------
GITHUB_API_OUTAGE = "GITHUB_API_OUTAGE"
RUNNER_INFRA = "RUNNER_INFRA"
POLICY_HELD = "POLICY_HELD"
EVIDENCE_MISSING = "EVIDENCE_MISSING"
PRODUCT_FAILED = "PRODUCT_FAILED"
DEPLOY_TRIGGER_FAILED = "DEPLOY_TRIGGER_FAILED"

# Fixed precedence (highest wins). Infra/API failures invalidate the
# observability of everything below them; an intentional hold outranks absent
# evidence; a product defect precedes any deploy attempt.
ROOT_PRECEDENCE: tuple[str, ...] = (
    GITHUB_API_OUTAGE,
    RUNNER_INFRA,
    POLICY_HELD,
    EVIDENCE_MISSING,
    PRODUCT_FAILED,
    DEPLOY_TRIGGER_FAILED,
)

# --- Node statuses ---------------------------------------------------------
STATUS_PASS = "PASS"  # noqa: S105 - node status label, not a credential
STATUS_FAILED = "FAILED"
STATUS_BLOCKED_UPSTREAM = "BLOCKED_UPSTREAM"
STATUS_INFRA = "INFRA"
STATUS_ABSENT = "ABSENT"

# Product-outcome code -> the subcheck name that reported the defect.
_OUTCOME_TO_CHECK: dict[str, str] = {
    CHANGE_DETECTION_FAILED: "change_detection",
    LINT_FAILED: "lint",
    TYPE_FAILED: "typecheck",
    TEST_FAILED: "tests",
    COVERAGE_FAILED: "coverage",
}

# Raw GitHub-API signals that count as an outage.
_API_OUTAGE_SIGNALS = frozenset(
    {"5xx", "ratelimit", "rate_limit", "graphql_down", "503", "502", "500"}
)
# OCC eligibility conclusions that count as observed red/absent evidence.
# NOTE: an *empty* string means "not part of this graph's inputs" (the product
# shadow deliberately does not consume OCC) and never fires EVIDENCE_MISSING.
_OCC_RED_SIGNALS = frozenset(
    {"failure", "action_required", "absent", "missing", "none", "null"}
)
_DEPLOY_FAIL_SIGNALS = frozenset({"failure", "action_required", "error"})


# --- Enforcement exit code (ENFORCING shadow — OMN-14709) -------------------
# The shadow flips from report-only to ENFORCING: it exits NON-ZERO only when the
# reason-graph root is a genuine PRODUCT defect (``PRODUCT_FAILED`` — lint /
# typecheck / tests / coverage red). Non-product roots (``RUNNER_INFRA``,
# ``EVIDENCE_MISSING``, ``GITHUB_API_OUTAGE``, ``POLICY_HELD``,
# ``DEPLOY_TRIGGER_FAILED``) and a green/READY graph stay exit 0, so the shadow
# reports without failing on non-product (infra / evidence / policy) causes. The
# workflow stays NON-required, so a red shadow reports but cannot block merges —
# it generates red-side parity without gating.
EXIT_PRODUCT_FAILED = 1


def enforcement_exit_code(graph: dict[str, Any]) -> int:
    """Exit code for ENFORCING mode.

    Returns ``EXIT_PRODUCT_FAILED`` (non-zero) iff the single elected root is a
    genuine product defect (``PRODUCT_FAILED``); returns 0 for a green/READY
    graph and for every non-product root. Only a product defect is fatal — infra,
    evidence-missing, API-outage, policy-hold and deploy-trigger roots are
    non-blocking so the shadow does not fail on causes outside the product
    dimension.
    """
    root = graph.get("root")
    if root is not None and root.get("kind") == PRODUCT_FAILED:
        return EXIT_PRODUCT_FAILED
    return 0


def root_receipt_id(head_sha: str, root_kind: str, primary_signal: str) -> str:
    """Content-addressed, deterministic root receipt id.

    ``sha256(head_sha || root_kind || primary_signal)[:16]`` (first 16 hex
    chars). Fields are joined with an unambiguous separator so distinct triples
    cannot collide by concatenation. Replay against the identical head + facts
    yields an identical id (fixture ``replay-determinism``).
    """
    payload = f"{head_sha}||{root_kind}||{primary_signal}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _norm(value: Any) -> str:
    return (str(value) if value is not None else "").strip().lower()


_SYNTHETIC_REPORTER: dict[str, str] = {
    GITHUB_API_OUTAGE: "github-api",
    RUNNER_INFRA: "runner",
    POLICY_HELD: "policy",
    EVIDENCE_MISSING: "occ-preflight",
    DEPLOY_TRIGGER_FAILED: "deploy-trigger",
}


@dataclass(frozen=True)
class _Signals:
    """Normalized non-product run signals (each empty string => not observed)."""

    occ: str
    runner: str
    gh_api: str
    policy: str
    deploy: str
    affirmative_product_fail: bool


def _candidate_roots(
    product_outcome: str,
    subcheck_cat: dict[str, EnumSubcheckOutcome],
    signals: _Signals,
) -> dict[str, str]:
    """Every candidate root whose firing condition holds, keyed by root kind."""
    candidates: dict[str, str] = {}
    occ = signals.occ
    occ_red = occ in _OCC_RED_SIGNALS
    gh_api = signals.gh_api
    runner = signals.runner
    policy = signals.policy
    deploy = signals.deploy
    affirmative_product_fail = signals.affirmative_product_fail

    if gh_api in _API_OUTAGE_SIGNALS:
        candidates[GITHUB_API_OUTAGE] = f"gh_api={gh_api}"

    if runner:
        candidates[RUNNER_INFRA] = f"runner={runner}"
    elif product_outcome == PRODUCT_INFRA and not occ_red:
        # An unconfirmable product subcheck (skipped/cancelled/absent) with NO
        # observed OCC-red explanation is a product-dimension infra fault, not a
        # pass. When OCC *is* observed red, those same skips are downstream of
        # the OCC root and are attributed to EVIDENCE_MISSING instead (below),
        # so a needs:occ-preflight cascade collapses to ONE root — not N.
        infra_names = ",".join(
            name
            for name in _PRODUCT_SUBCHECKS
            if subcheck_cat[name]
            in (EnumSubcheckOutcome.INFRA, EnumSubcheckOutcome.ABSENT)
        )
        candidates[RUNNER_INFRA] = f"product_infra:{infra_names}"

    if policy:
        candidates[POLICY_HELD] = f"policy={policy}"

    # EVIDENCE_MISSING fires ONLY when OCC is observed red/absent AND no product
    # check independently failed (the OCC-independence property).
    if occ_red and not affirmative_product_fail:
        candidates[EVIDENCE_MISSING] = f"occ_eligibility={occ or 'absent'}"

    if affirmative_product_fail:
        check = _OUTCOME_TO_CHECK[product_outcome]
        candidates[PRODUCT_FAILED] = f"{check}=failure"

    if deploy in _DEPLOY_FAIL_SIGNALS:
        candidates[DEPLOY_TRIGGER_FAILED] = f"deploy_trigger={deploy}"

    return candidates


def _classify_node(
    name: str,
    cat: EnumSubcheckOutcome,
    reporter_check: str | None,
    root_kind: str | None,
    receipt_id: str | None,
) -> dict[str, Any]:
    """Classify one product subcheck into a graph node (early-return, fail-closed)."""
    if name == reporter_check:
        # This subcheck IS the root cause (independent defect).
        return {
            "name": name,
            "status": STATUS_FAILED,
            "is_root": True,
            "root_receipt_id": receipt_id,
        }
    if cat is EnumSubcheckOutcome.PASS:
        # Reported independently — not blocked, not counted as a failure.
        return {
            "name": name,
            "status": STATUS_PASS,
            "is_root": False,
            "root_receipt_id": None,
        }
    if cat is EnumSubcheckOutcome.FAIL:
        # A product FAIL that is NOT the elected root only happens under a
        # higher-precedence non-product root (infra/API/policy invalidated its
        # observability): it is a dependent, never independently counted.
        return {
            "name": name,
            "status": STATUS_BLOCKED_UPSTREAM,
            "is_root": False,
            "root_receipt_id": receipt_id,
        }
    if root_kind is None:
        # INFRA / ABSENT with no elected root: an independent product-infra fault.
        status = STATUS_INFRA if cat is EnumSubcheckOutcome.INFRA else STATUS_ABSENT
        return {
            "name": name,
            "status": status,
            "is_root": False,
            "root_receipt_id": None,
        }
    # INFRA / ABSENT under a higher-precedence root: a dependent.
    return {
        "name": name,
        "status": STATUS_BLOCKED_UPSTREAM,
        "is_root": False,
        "root_receipt_id": receipt_id,
    }


def build_reason_graph(facts: dict[str, Any]) -> dict[str, Any]:
    """Collapse a run's facts into exactly one typed root + dependents.

    ``facts`` keys:
      - ``head_sha``: exact head SHA the graph is content-addressed to.
      - ``subchecks``: dict of product subcheck conclusions (change_detection,
        lint, typecheck, tests, coverage) — the product dimension.
      - ``occ_eligibility`` (optional): observed ``occ-preflight / eligibility``
        conclusion. Empty => not consumed (never fires EVIDENCE_MISSING).
      - ``runner_signal`` / ``gh_api`` / ``policy`` / ``deploy_trigger``
        (optional): non-product signals for full-fleet reuse.
    """
    head_sha = str(facts.get("head_sha", "") or "")
    subchecks_raw: dict[str, Any] = dict(facts.get("subchecks", {}) or {})

    product_outcome, freeze_eligible = classify_product(subchecks_raw)
    affirmative_product_fail = product_outcome in _OUTCOME_TO_CHECK

    # Coarse per-subcheck categories for node rendering.
    subcheck_cat: dict[str, EnumSubcheckOutcome] = {
        name: categorize_conclusion(subchecks_raw.get(name))
        for name in _PRODUCT_SUBCHECKS
    }

    candidates = _candidate_roots(
        product_outcome,
        subcheck_cat,
        _Signals(
            occ=_norm(facts.get("occ_eligibility")),
            runner=_norm(facts.get("runner_signal")),
            gh_api=_norm(facts.get("gh_api")),
            policy=_norm(facts.get("policy")),
            deploy=_norm(facts.get("deploy_trigger")),
            affirmative_product_fail=affirmative_product_fail,
        ),
    )

    # --- Single-rooting by fixed precedence --------------------------------
    root_kind: str | None = None
    primary_signal = ""
    for kind in ROOT_PRECEDENCE:
        if kind in candidates:
            root_kind = kind
            primary_signal = candidates[kind]
            break

    root: dict[str, Any] | None = None
    receipt_id: str | None = None
    if root_kind is not None:
        receipt_id = root_receipt_id(head_sha, root_kind, primary_signal)
        root = {
            "kind": root_kind,
            "primary_signal": primary_signal,
            "root_receipt_id": receipt_id,
        }

    # --- Nodes -------------------------------------------------------------
    reporter_check = (
        _OUTCOME_TO_CHECK.get(product_outcome) if root_kind == PRODUCT_FAILED else None
    )
    nodes: list[dict[str, Any]] = [
        _classify_node(name, subcheck_cat[name], reporter_check, root_kind, receipt_id)
        for name in _PRODUCT_SUBCHECKS
    ]

    # For a non-product root, add a synthetic reporter node naming the cause.
    if root_kind is not None and root_kind != PRODUCT_FAILED:
        nodes.insert(
            0,
            {
                "name": _SYNTHETIC_REPORTER[root_kind],
                "status": STATUS_FAILED,
                "is_root": True,
                "root_receipt_id": receipt_id,
            },
        )

    dependents = [n for n in nodes if n["status"] == STATUS_BLOCKED_UPSTREAM]
    ready = root_kind is None and product_outcome == PRODUCT_GREEN

    return {
        "schema_version": SCHEMA_VERSION,
        "head_sha": head_sha,
        "root": root,
        "nodes": nodes,
        # count of distinct roots — the projection contract number (typically 1).
        "blocked_candidate_count": 1 if root_kind is not None else 0,
        "blocked_upstream_count": len(dependents),
        "product_outcome": product_outcome,
        "freeze_eligible": freeze_eligible,
        "ready": ready,
    }


def _render_summary(graph: dict[str, Any]) -> str:
    root = graph["root"]
    lines = ["## CI Reason Graph (report-only)", ""]
    if root is None:
        lines.append(
            f"> READY — product dimension `{graph['product_outcome']}`, "
            f"freeze_eligible=`{str(graph['freeze_eligible']).lower()}`. "
            "Single-node graph, no BLOCKED_UPSTREAM dependents."
        )
    else:
        lines += [
            f"> ROOT `{root['kind']}` — signal `{root['primary_signal']}` — "
            f"receipt `{root['root_receipt_id']}`",
            "",
            f"blocked_candidate_count = **{graph['blocked_candidate_count']}** "
            f"(BLOCKED_UPSTREAM dependents: {graph['blocked_upstream_count']})",
        ]
    lines += ["", "| node | status | root_receipt_id |", "| --- | --- | --- |"]
    for node in graph["nodes"]:
        rid = node.get("root_receipt_id") or "-"
        lines.append(f"| `{node['name']}` | `{node['status']}` | `{rid}` |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Typed CI reason-graph emitter (OMN-14704 / OMN-14644 Phase 3)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("graph", help="Emit the reason graph as JSON")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--facts-json", help="JSON object of run facts")
    src.add_argument("--facts-file", help="Path to a file containing the facts JSON")
    p.add_argument(
        "--summary",
        action="store_true",
        help="Also print a Markdown summary block to stderr.",
    )
    p.add_argument(
        "--enforce",
        action="store_true",
        help=(
            "ENFORCING mode: exit non-zero when the reason-graph root is a "
            "genuine product defect (PRODUCT_FAILED). Non-product roots and a "
            "green graph still exit 0. Omit for the legacy report-only surface."
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "graph":
        if args.facts_file:
            with Path(args.facts_file).open(encoding="utf-8") as fh:
                data = json.load(fh)
        else:
            data = json.loads(args.facts_json)
        if not isinstance(data, dict):
            print("facts JSON must be an object", file=sys.stderr)
            return 2
        graph = build_reason_graph(data)
        print(json.dumps(graph, sort_keys=True))
        if args.summary:
            print(_render_summary(graph), file=sys.stderr)
        if args.enforce:
            # ENFORCING: non-zero ONLY on a PRODUCT_FAILED root; non-product
            # roots and green stay 0. The workflow remains non-required.
            return enforcement_exit_code(graph)
        # Report-only (default): this surface never fails the check.
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover - argparse exits first


if __name__ == "__main__":
    raise SystemExit(main())
