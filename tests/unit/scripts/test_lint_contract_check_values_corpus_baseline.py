# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Corpus-wide shrink-only baseline for OMN-15382 Rule A / Rule B.

scripts/lint_contract_check_values.py's new executable-command-shape (Rule A)
and per-item PR binding (Rule B) checks are enforced going forward via
pre-commit (only on changed files), but a corpus-wide survey of every
contracts/*.yaml file at OMN-15382 landing found both defect classes already
present historically -- Rule A in ~250 legacy items (bare English prose or a
raw path with no command at all), Rule B in ~1,190 legacy items (mostly the
OMN-14431-era pattern where a differently-named shell variable, not the bare
``${PR_NUMBER}`` runner placeholder, carries the literal number -- a
different, less severe shape than the OMN-14968 defect this ticket repairs).

These are frozen into ``.onex_ratchets/omn_15382_rule_a_baseline.yaml`` and
``.onex_ratchets/omn_15382_rule_b_baseline.yaml`` as a SHRINK-ONLY ratchet:

* A NEW violation (not in the baseline) on any contract -- including one
  authored today -- hard-fails immediately.
* The baseline set must match the live corpus scan EXACTLY (set equality).
  Partial cleanup (removing some baseline entries without actually repairing
  the underlying contracts) hard-fails just as loudly as padding the baseline
  with entries that no longer violate anything -- both silently defeat the
  ratchet's purpose (tracking real, live debt).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import lint_contract_check_values as linter  # noqa: E402

_RULE_A_BASELINE_PATH = _REPO_ROOT / ".onex_ratchets" / "omn_15382_rule_a_baseline.yaml"
_RULE_B_BASELINE_PATH = _REPO_ROOT / ".onex_ratchets" / "omn_15382_rule_b_baseline.yaml"


def _load_baseline(path: Path) -> frozenset[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("baseline", []) or []
    return frozenset(str(e) for e in entries)


def _scan_corpus() -> tuple[frozenset[str], frozenset[str]]:
    """Return (rule_a_ids, rule_b_ids) for every contracts/*.yaml file.

    Each id is ``"<relative-contract-path>::<dod_id>"`` -- the same shape the
    baseline files use, and the same shape OMN-15382's corpus survey used to
    generate them.
    """
    contracts_dir = _REPO_ROOT / "contracts"
    rule_a: set[str] = set()
    rule_b: set[str] = set()

    for path in sorted(contracts_dir.glob("*.yaml")):
        rel = f"contracts/{path.name}"
        findings = linter.lint_contract(path)
        for _path_str, label, fragment in findings:
            if "executable-command-shape" in label:
                dod_id = label.split(":", 1)[0]
                rule_a.add(f"{rel}::{dod_id}")
            elif "pr-binding" in label:
                # fragment carries the dod_id for pr-binding findings.
                rule_b.add(f"{rel}::{fragment}")

    return frozenset(rule_a), frozenset(rule_b)


@pytest.mark.unit
def test_rule_a_corpus_matches_frozen_baseline_exactly() -> None:
    baseline = _load_baseline(_RULE_A_BASELINE_PATH)
    live_a, _live_b = _scan_corpus()

    new_violations = live_a - baseline
    healed = baseline - live_a

    assert not new_violations, (
        f"{len(new_violations)} NEW Rule A (executable-command-shape) "
        "violation(s) found that are not in the frozen shrink-only baseline "
        f"({_RULE_A_BASELINE_PATH}): {sorted(new_violations)[:20]}. Fix the "
        "check_value(s), or if this is a genuine, deliberate pre-existing "
        "legacy item being newly surveyed, that is not how this ratchet "
        "works -- repair it instead."
    )
    assert not healed, (
        f"{len(healed)} baseline entr{'y is' if len(healed) == 1 else 'ies are'} "
        "no longer reproduced by a live corpus scan, but the baseline file "
        f"({_RULE_A_BASELINE_PATH}) was not updated to remove them: "
        f"{sorted(healed)[:20]}. Update the baseline file to match (shrink "
        "it) when you repair a contract -- do not leave stale entries."
    )


@pytest.mark.unit
def test_rule_b_corpus_matches_frozen_baseline_exactly() -> None:
    baseline = _load_baseline(_RULE_B_BASELINE_PATH)
    _live_a, live_b = _scan_corpus()

    new_violations = live_b - baseline
    healed = baseline - live_b

    assert not new_violations, (
        f"{len(new_violations)} NEW Rule B (per-item PR binding) violation(s) "
        "found that are not in the frozen shrink-only baseline "
        f"({_RULE_B_BASELINE_PATH}): {sorted(new_violations)[:20]}. A "
        "dod_evidence id embedding a PR number must literally pin that "
        "number in every gh pr view/checks/diff check_value -- a bare "
        "${PR_NUMBER} placeholder resolves to whatever PR the compliance "
        "runner is evaluating, not the pinned PR."
    )
    assert not healed, (
        f"{len(healed)} baseline entr{'y is' if len(healed) == 1 else 'ies are'} "
        "no longer reproduced by a live corpus scan, but the baseline file "
        f"({_RULE_B_BASELINE_PATH}) was not updated to remove them: "
        f"{sorted(healed)[:20]}. Update the baseline file to match (shrink "
        "it) when you rebind a contract -- do not leave stale entries."
    )


@pytest.mark.unit
def test_omn_14968_and_omn_15382_contribute_nothing_to_either_baseline() -> None:
    """The two contracts this ticket lands must never appear in either
    baseline -- they are either repaired (OMN-14968, via append-only
    supersession) or authored clean from the start (OMN-15382).
    """
    baseline_a = _load_baseline(_RULE_A_BASELINE_PATH)
    baseline_b = _load_baseline(_RULE_B_BASELINE_PATH)
    for entry in (*baseline_a, *baseline_b):
        assert "OMN-14968" not in entry, entry
        assert "OMN-15382" not in entry, entry
