# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""A DoD check that pins an artifact and asserts its freshness expires (OMN-18641).

THE INCIDENT. `contracts/OMN-13034.yaml` carried three pairs of checks that
fetched the lane-census snapshot at a pinned commit sha and then asserted the
artifact was under seven days old against wall-clock now. A sha cannot change,
so the artifact cannot get newer, so the predicate could only rot. On
2026-09-17 the newest pair crossed its horizon and `Contract Compliance Check`
went to `OMN-13034: 2/17 PASS, 13 WARN, 2 BLOCK`. Every OCC companion bound to
that ticket blocked, which killed `omnibase_infra#3726` and would have killed
each of its successors.

The contract's own prose shows the shape was understood and accepted rather
than missed: "each refresh of the census must re-point this probe at its own
head". That is a treadmill — every refresh hand-authors a fresh pair that
begins expiring the moment it lands.

WHAT THE TESTS HAVE TO PROVE, in both directions. A gate that refused every
check and a gate that refused nothing would both show a green corpus once the
corpus was repaired. So the negative cases below are paired with positive
controls that must keep PASSING, and the two allowed shapes are named
explicitly because they are the shapes the repair produces.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[1]
_GATE = _REPO / "scripts" / "validation" / "check_expiring_dod_checks.py"
_CONTRACT = _REPO / "contracts" / "OMN-13034.yaml"
_CI = _REPO / ".github" / "workflows" / "ci.yml"

# Load the gate by path, the way this repo's other script-gate tests do
# (tests/unit/scripts/test_yamlfmt_contamination_gate.py). A sys.path insert
# imports fine at runtime and is unresolvable to mypy --strict, which is what
# the pre-push hook checks.
_SPEC = importlib.util.spec_from_file_location("check_expiring_dod_checks", _GATE)
assert _SPEC is not None
assert _SPEC.loader is not None
_GATE_MOD = importlib.util.module_from_spec(_SPEC)
# Register before exec: the gate declares a frozen dataclass, and under
# `from __future__ import annotations` dataclasses resolves its field types via
# sys.modules[cls.__module__]. An unregistered module makes that lookup None.
sys.modules[_SPEC.name] = _GATE_MOD
_SPEC.loader.exec_module(_GATE_MOD)

KNOWN_DEBT: frozenset[tuple[str, str]] = _GATE_MOD.KNOWN_DEBT
now_relative_reason = _GATE_MOD.now_relative_reason
scan_check_value = _GATE_MOD.scan_check_value
scan_contract = _GATE_MOD.scan_contract

_SHA = "e4998cb85762fdab7e299f9a66b5764eb6878b74"
_PINNED = (
    f'gh api "repos/OmniNode-ai/omnibase_infra/contents/deploy/lane-census/'
    f'census-snapshot.json?ref={_SHA}"'
)


# ---------------------------------------------------------------------------
# The shape the gate exists to refuse.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("clock", "label"),
    [
        (
            """--arg now "$(date -u +%s)" '.emitted_at > ($now|tonumber)'""",
            "date epoch",
        ),
        ("""--max-age-days 7""", "product checker resolving now"),
        ("""python3 -c 'import datetime; datetime.datetime.now()'""", "datetime.now"),
    ],
)
def test_a_pinned_ref_plus_a_clock_is_refused(clock: str, label: str) -> None:
    """Pinned artifact, now-relative predicate: expires by construction."""
    findings = scan_check_value("X.yaml", "some-check", f"{_PINNED} | {clock}")
    assert len(findings) == 1, f"{label} was not caught: {findings}"
    assert findings[0].sha == _SHA


# ---------------------------------------------------------------------------
# POSITIVE CONTROLS. Without these the gate could be stuck closed and the
# corpus would still look green after the repair.
# ---------------------------------------------------------------------------


def test_a_pinned_ref_with_an_exact_value_assertion_passes() -> None:
    """Pinning is correct practice and must never be discouraged on its own.

    A pinned fetch whose predicate is an equality carries no clock, so it is
    as true in a year as it is today. This is one of the two shapes the repair
    produces, and a gate that refused it would have no reachable green state.
    """
    value = (
        f"{_PINNED} --jq '.content' | base64 -d | "
        "jq -e '.emitted_at == \"2026-09-10T10:06:57.003308+00:00\"'"
    )
    assert scan_check_value("X.yaml", "exact", value) == []


def test_a_pinned_ref_measured_against_the_pinned_commits_date_passes() -> None:
    """The other repaired shape: same 7-day claim, immutable epoch.

    `gh api .../commits/<sha>` is not a clock — it returns the same answer
    forever — so "this census was fresh when it landed" stays provable.
    """
    value = (
        f"{_PINNED} --jq '.content' | base64 -d | jq -e "
        f'--arg committed "$(gh api repos/OmniNode-ai/omnibase_infra/commits/{_SHA} '
        f'--jq .commit.committer.date)" '
        f"'.emitted_at > (($committed | fromdateiso8601) - 7*86400)'"
    )
    assert scan_check_value("X.yaml", "vs-commit", value) == []


def test_a_clock_against_an_unpinned_ref_passes() -> None:
    """Live state is SUPPOSED to track the calendar.

    A freshness assertion over a branch tip is a real, renewable claim. Only
    the combination with a frozen artifact is the defect.
    """
    value = (
        'gh api "repos/OmniNode-ai/omnibase_infra/contents/deploy/lane-census/'
        "census-snapshot.json?ref=dev\" --jq '.content' | base64 -d | "
        "jq -e --arg now \"$(date -u +%s)\" '.emitted_at > ($now | tonumber) - 7*86400'"
    )
    assert scan_check_value("X.yaml", "live", value) == []


def test_the_clock_vocabulary_is_not_matching_everything() -> None:
    """A signature set that matched ordinary prose would refuse the whole corpus."""
    assert now_relative_reason("gh pr view 3394 --json mergedAt") is None
    assert now_relative_reason("python3 scripts/check_lane_census_age.py") is None
    assert now_relative_reason("date -u +%s") is not None


# ---------------------------------------------------------------------------
# The live corpus, and the wiring that makes the gate enforcement (rule 5).
# ---------------------------------------------------------------------------


def test_the_six_known_entries_are_debt_not_silence() -> None:
    """OMN-13034 still carries the shape, and the gate says so out loud.

    The entries are NOT repaired in this change. A DoD receipt is hash-bound to
    the contract entry it attests, so editing them invalidates ten receipts and
    `check_contract_change_rebinds_receipts.py` refuses it — correctly, because
    a receipt whose expected hash is edited to match a changed check attests to
    a check that never ran. The repair is supersession and is its own change.
    """
    found = {(f.contract, f.check_id) for f in scan_contract(_CONTRACT)}
    assert found == KNOWN_DEBT, (
        "the debt baseline and the contract disagree; the list is shrink-only "
        f"and must be updated in the change that repairs an entry. {found ^ KNOWN_DEBT}"
    )

    result = subprocess.run(
        [sys.executable, str(_GATE), str(_CONTRACT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "debt baseline" in result.stdout, result.stdout


def test_the_whole_corpus_is_clean() -> None:
    """Wiring this as required is only safe if nothing else carries the shape."""
    result = subprocess.run(
        [sys.executable, str(_GATE)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_a_repaired_entry_must_leave_the_debt_baseline() -> None:
    """A shrink-only list that never shrinks is an allowlist wearing a disguise.

    When the supersession pass repairs an entry, the gate FAILS until that entry
    is removed from KNOWN_DEBT. Without this the list would quietly outlive the
    debt it records.
    """
    doc = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    ids = {str(item.get("id", "")) for item in doc["dod_evidence"]}
    for _, check_id in KNOWN_DEBT:
        assert check_id in ids, (
            f"{check_id} is on the debt baseline but no longer exists in the "
            "contract; remove it from KNOWN_DEBT"
        )


def test_the_intended_repair_shape_is_recorded_and_allowed() -> None:
    """The repair the follow-up must make is spelled out and provably accepted.

    The freshness claim must survive the repair -- dropping it would also make
    the gate green and would be the wrong fix, since the item's whole purpose is
    that the census was fresh when it landed. Measuring against the pinned
    COMMIT's own date keeps the claim and removes the clock.
    """
    repaired = (
        f"{_PINNED} --jq '.content' | base64 -d | jq -e "
        f'--arg committed "$(gh api repos/OmniNode-ai/omnibase_infra/commits/{_SHA} '
        f'--jq .commit.committer.date)" '
        f"'.emitted_at > (($committed | fromdateiso8601) - 7*86400)'"
    )
    assert (
        scan_check_value("OMN-13034.yaml", "occ-census-freshness-live-3394", repaired)
        == []
    )
    assert "7*86400" in repaired, "the seven-day claim must survive the repair"


def test_the_gate_is_wired_as_enforcement() -> None:
    """Detection that is not a gate gets ignored; that is how this class survived."""
    ci = _CI.read_text(encoding="utf-8")
    assert "check_expiring_dod_checks.py" in ci, (
        "the gate is not invoked from CI, so it enforces nothing (rule 5)"
    )


def test_an_unreadable_contract_fails_loud() -> None:
    """A gate that cannot read its input has not passed; it has not run."""
    result = subprocess.run(
        [sys.executable, str(_GATE), str(_REPO / "does-not-exist.yaml")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
