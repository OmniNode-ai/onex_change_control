# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""OMN-15309 — the anti-drift mechanism for the single admissibility predicate.

OMN-15309 AC: "No fourth definition of admissibility is introduced; if (iii),
``validate_pr_deploy_required.py``'s predicate is the cited single source."

A comment saying "adopted from X" is not a mechanism — a rule that agents must
remember to apply is not enforcement. This module is the mechanism: it EXECUTES
the shared corpus (``tests/fixtures/evidence_admissibility_cases.yaml``) against
BOTH implementations and asserts they agree.

Two assertions, in order of strength:

1. **Direction (unconditional, every case).** This predicate may REFUSE what
   deploy-gate admits; it may never ADMIT what deploy-gate refuses. A looser
   OCC predicate is a laundering hole and fails immediately.
2. **Equality (every case without an explicit ``parity_divergence`` ticket).**
   A case may declare a known, ticketed divergence; anything else must match
   exactly on the ``live`` profile, which is deploy-gate's contract.

Resolution of the upstream file, in order: ``$OMNICLAUDE_DEPLOY_GATE_PY``, then
``$OMNI_HOME/omniclaude/.github/actions/deploy-gate/``. When neither resolves the
suite SKIPS locally but FAILS when ``OMN15309_PARITY_REQUIRED=1`` — which the CI
step sets. A skip in CI would be a false green, so CI does not permit one.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import ModuleType

import pytest
import yaml

from onex_change_control.validation.evidence_admissibility import (
    LIVE_PROBE_COMMANDS,
    classify_evidence,
)

_CASES_PATH = Path(__file__).parent / "fixtures" / "evidence_admissibility_cases.yaml"
_REL = Path(".github/actions/deploy-gate/validate_pr_deploy_required.py")
_PARITY_REQUIRED = os.environ.get("OMN15309_PARITY_REQUIRED", "").strip() == "1"


def _candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("OMNICLAUDE_DEPLOY_GATE_PY", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    omni_home = os.environ.get("OMNI_HOME", "").strip()
    if omni_home:
        candidates.append(Path(omni_home) / "omniclaude" / _REL)
    checkout = os.environ.get("OMNICLAUDE_CHECKOUT", "").strip()
    if checkout:
        candidates.append(Path(checkout) / _REL)
    return candidates


def _load_upstream() -> ModuleType:
    tried = _candidate_paths()
    for path in tried:
        if path.is_file():
            spec = importlib.util.spec_from_file_location(
                "omn15309_upstream_deploy_gate", path
            )
            assert spec is not None
            assert spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    message = (
        "OMN-15309 parity check could NOT resolve omniclaude's "
        f"{_REL} (tried: {[str(p) for p in tried] or 'nothing — no env var set'}). "
        "Set OMNICLAUDE_DEPLOY_GATE_PY, OMNICLAUDE_CHECKOUT, or OMNI_HOME."
    )
    if _PARITY_REQUIRED:
        pytest.fail(message + " OMN15309_PARITY_REQUIRED=1, so a skip is not allowed.")
    pytest.skip(message)


def _load_cases() -> list[dict[str, Any]]:
    raw = yaml.safe_load(_CASES_PATH.read_text(encoding="utf-8"))
    return list(raw["cases"])


CASES = _load_cases()
CASE_IDS = [c["id"] for c in CASES]


@pytest.fixture(scope="module")
def upstream() -> ModuleType:
    return _load_upstream()


def test_upstream_exposes_the_cited_predicate(upstream: ModuleType) -> None:
    """The citation must name a symbol that exists and is callable."""
    assert hasattr(upstream, "classify_check_value"), (
        "omniclaude's deploy-gate no longer exposes classify_check_value — the "
        "OMN-15309 citation is stale and the two definitions are unpinned."
    )
    verdict = upstream.classify_check_value("docker exec c true")
    assert verdict.falsifiable is True, (
        "sanity: upstream must still accept a plain live probe, or this parity "
        "run is measuring a broken import rather than a real disagreement"
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_occ_is_never_looser_than_deploy_gate(
    upstream: ModuleType, case: dict[str, Any]
) -> None:
    """Direction invariant — the fail-closed half. Holds for EVERY case."""
    ours = classify_evidence(
        case["check_value"],
        admissible_probes=LIVE_PROBE_COMMANDS,
        changed_paths=case.get("changed_paths"),
    )
    theirs = upstream.classify_check_value(case["check_value"])
    assert not (ours.admissible and not theirs.falsifiable), (
        f"{case['id']}: onex_change_control ADMITS a check_value that "
        f"deploy-gate REFUSES. A looser adopted predicate is a laundering hole.\n"
        f"  ours:   {ours.reason}\n"
        f"  theirs: {theirs.reason}"
    )


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_live_profile_matches_deploy_gate(
    upstream: ModuleType, case: dict[str, Any]
) -> None:
    """Equality on the ``live`` profile, except for ticketed divergences."""
    divergence = case.get("parity_divergence")
    ours = classify_evidence(
        case["check_value"],
        admissible_probes=LIVE_PROBE_COMMANDS,
        changed_paths=case.get("changed_paths"),
    )
    theirs = upstream.classify_check_value(case["check_value"])

    if divergence:
        assert ours.admissible is not theirs.falsifiable, (
            f"{case['id']}: declares parity_divergence={divergence} but the two "
            "implementations now AGREE. The divergence has been resolved "
            "upstream — delete the parity_divergence field so this case is held "
            "to strict equality again."
        )
        assert ours.admissible is False, (
            f"{case['id']}: a declared divergence must make onex_change_control "
            "STRICTER, never looser."
        )
        return

    assert ours.admissible is theirs.falsifiable, (
        f"{case['id']}: the two implementations disagree and no "
        "parity_divergence ticket is declared. Either fix the drift or record "
        "the divergence with a ticket id.\n"
        f"  ours:   admissible={ours.admissible} — {ours.reason}\n"
        f"  theirs: falsifiable={theirs.falsifiable} — {theirs.reason}"
    )
