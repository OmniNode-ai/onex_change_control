# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15459: supersession may not rebind an item to another item's check.

RED/GREEN controls for the supersession-binding leg of
``scripts/validation/check_receipt_hardening.py``. The detector is proven
against the LIVE defect shape — the eight ``command.supersede.2552.yaml``
files OCC#5534 merged at ``34c8dacc``, byte-for-byte as they landed — not
against a hand-drawn approximation of it. A gate that only passes on
synthetic fixtures is not evidence that it would have caught the thing it
was built for.

Each rule is exercised in both polarities, and every escape route from the
cure mechanism (lower token, self-rebinding repair, wrong target) is proven
to leave the violation standing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_YAML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_RECEIPTS = _REPO_ROOT / "drift" / "dod_receipts"
_CONTRACTS = _REPO_ROOT / "contracts"
_BASELINE = (
    _REPO_ROOT / ".onex_ratchets" / "omn_15459_supersession_binding_baseline.yaml"
)
# Corpus entries are recorded as repo-root-relative paths (what pre-commit and
# the CI corpus step pass), so corpus assertions run with cwd == repo root.
_REL_RECEIPTS = Path("drift/dod_receipts")
_REL_CONTRACTS = Path("contracts")

# The eight items OCC#5534 rebound to one byte-identical grep.
_OCC_5534_ITEMS = (
    "dod-OmniNode-ai-omnibase_infra-pr-2543",
    "dod-OmniNode-ai-omnibase_infra-pr-2546",
    "dod-OmniNode-ai-omnibase_infra-pr-2550",
    "dod-deploy-assessment",
    "dod-occ-evidence-admissibility-validator",
    "occ-self-bind-pr-5488",
    "occ-self-bind-pr-5511",
    "occ-self-bind-pr-5528",
)
# The six OCC#5528 minted one PR earlier, same producer, same defect.
_OCC_5528_ITEMS = (
    "dod-OmniNode-ai-omnibase_infra-pr-2543",
    "dod-OmniNode-ai-omnibase_infra-pr-2546",
    "dod-deploy-assessment",
    "dod-occ-evidence-admissibility-validator",
    "occ-self-bind-pr-5488",
    "occ-self-bind-pr-5511",
)


def _load_gate() -> Any:
    """Load the validator by path (``scripts/validation`` is not a package).

    Matches the loader every other ``tests/unit/scripts`` module uses: a bare
    import type-checks locally and fails ``mypy src/ tests/`` in CI with
    ``import-not-found``.
    """
    script_path = _REPO_ROOT / "scripts" / "validation" / "check_receipt_hardening.py"
    spec = importlib.util.spec_from_file_location(
        "check_receipt_hardening_supersession", script_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate: Any = _load_gate()


# ---------------------------------------------------------------------------
# Fixture helpers — build a corpus on disk, because the rules are about files
# in relation to their siblings, not about a single parsed object.
# ---------------------------------------------------------------------------


def _contract(tmp: Path, ticket: str, items: dict[str, list[str]]) -> Path:
    contracts = tmp / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    (contracts / f"{ticket}.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "ticket_id": ticket,
                "dod_evidence": [
                    {
                        "id": item,
                        "description": f"{item} evidence",
                        "checks": [
                            {"check_type": "command", "check_value": check}
                            for check in checks
                        ],
                    }
                    for item, checks in items.items()
                ],
            },
            sort_keys=False,
        )
    )
    return contracts


def _supersede(  # noqa: PLR0913 - a receipt key IS ticket+item+token+check
    tmp: Path,
    ticket: str,
    item: str,
    token: str,
    check_value: str,
    *,
    supersedes: str | None = None,
) -> Path:
    item_dir = tmp / "drift" / "dod_receipts" / ticket / item
    item_dir.mkdir(parents=True, exist_ok=True)
    target = supersedes or f"drift/dod_receipts/{ticket}/{item}/command.yaml"
    path = item_dir / f"command.supersede.{token}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "ticket_id": ticket,
                "evidence_item_id": item,
                "check_type": "command",
                "supersedes": target,
                "reason": "test fixture",
                "superseder": "test",
                "created_at": "2026-07-30T00:00:00Z",
                "replacement": {
                    "schema_version": "1.0.0",
                    "ticket_id": ticket,
                    "evidence_item_id": item,
                    "check_type": "command",
                    "check_value": check_value,
                    "contract_entry_sha256": "sha256:" + "0" * 64,
                    "status": "PASS",
                    "run_timestamp": "2026-07-30T00:00:00Z",
                    "commit_sha": "0" * 40,
                    "runner": "test",
                    "verifier": "executed-probe",
                    "exit_code": 0,
                },
            },
            sort_keys=False,
        )
    )
    return path


def _rules(path: Path, contracts: Path) -> set[str]:
    return {
        rule
        for rule, _ in gate._supersession_violations(
            path, contracts, baseline=frozenset()
        )
    }


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """Drop the process-wide memoisation between fixtures on disk."""
    for name in (
        "_load_mapping",
        "_contract_entry",
        "_cohort_members",
        "_repaired_targets",
        "_item_anchors",
    ):
        getattr(gate, name).cache_clear()


# ---------------------------------------------------------------------------
# RED against the live OCC#5534 / OCC#5528 defect shape
# ---------------------------------------------------------------------------


def _copy_cohort(tmp: Path, token: str, items: tuple[str, ...]) -> list[Path]:
    """Copy the merged cohort into a tmp tree WITHOUT the repair records.

    Reproduces the tree exactly as it stood between the OCC#5534 merge
    (34c8dacc) and this ticket's repair, which is the state the gate has to
    call RED.
    """
    copied: list[Path] = []
    for item in items:
        src = _RECEIPTS / "OMN-15395" / item / f"command.supersede.{token}.yaml"
        assert src.is_file(), f"live corpus is missing {src}"
        dst = tmp / "drift" / "dod_receipts" / "OMN-15395" / item / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        copied.append(dst)
    contracts = tmp / "contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    (contracts / "OMN-15395.yaml").write_bytes(
        (_CONTRACTS / "OMN-15395.yaml").read_bytes()
    )
    return copied


def test_red_against_live_occ_5534_cohort(tmp_path: Path) -> None:
    """All 8 merged OCC#5534 files fail BOTH rules, on their real bytes."""
    copied = _copy_cohort(tmp_path, "2552", _OCC_5534_ITEMS)
    assert len(copied) == 8
    for path in copied:
        assert _rules(path, tmp_path / "contracts") == {"S1", "S2"}, path


def test_red_against_live_occ_5528_cohort(tmp_path: Path) -> None:
    """OCC#5528's 6 files fail too — the defect predates #5534 by one PR.

    S2 does not fire on every one of the six (one replacement happens to name
    its own item's artifact), so the assertion is per-rule rather than a
    blanket both-rules claim: S1 catches all six, and that is what is proven.
    """
    copied = _copy_cohort(tmp_path, "2550", _OCC_5528_ITEMS)
    assert len(copied) == 6
    for path in copied:
        assert "S1" in _rules(path, tmp_path / "contracts"), path


def test_shared_check_value_is_the_discriminator_not_the_grep_itself(
    tmp_path: Path,
) -> None:
    """The OCC#5534 grep is a well-formed, RED-derivable probe — for ONE item.

    Bound to the item whose contract entry actually names
    ``create_kafka_topics.py``, the identical string is clean. The defect is
    the pairing, not the probe, and the gate must draw the line there or it
    is just banning greps.
    """
    grep = (
        "gh api repos/OmniNode-ai/omnibase_infra/contents/scripts/"
        "create_kafka_topics.py?ref=f420d5fa26f704c75c3854ba2efc788d36175d13 "
        "--jq '.content' | base64 -d | grep -c 'def _build_specs'"
    )
    contracts = _contract(
        tmp_path,
        "OMN-11111",
        {
            "dod-topic-script": [
                "grep -q 'def _build_specs' scripts/create_kafka_topics.py"
            ]
        },
    )
    path = _supersede(tmp_path, "OMN-11111", "dod-topic-script", "2552", grep)
    assert _rules(path, contracts) == set()


# ---------------------------------------------------------------------------
# GREEN: the shape a compliant producer must emit
# ---------------------------------------------------------------------------


def test_per_item_discriminating_checks_are_clean(tmp_path: Path) -> None:
    contracts = _contract(
        tmp_path,
        "OMN-11111",
        {
            "dod-a-pr-101": ["gh pr view 101 --repo OmniNode-ai/x --json state"],
            "dod-b-pr-202": ["gh pr view 202 --repo OmniNode-ai/x --json state"],
        },
    )
    a = _supersede(
        tmp_path,
        "OMN-11111",
        "dod-a-pr-101",
        "900",
        "gh api repos/OmniNode-ai/x/pulls/101 --jq .merged | grep -qx true",
    )
    b = _supersede(
        tmp_path,
        "OMN-11111",
        "dod-b-pr-202",
        "900",
        "gh api repos/OmniNode-ai/x/pulls/202 --jq .merged | grep -qx true",
    )
    assert _rules(a, contracts) == set()
    assert _rules(b, contracts) == set()


def test_same_item_may_keep_its_check_across_cohorts(tmp_path: Path) -> None:
    """A relabel that changes prose and keeps the probe is legitimate.

    OCC#5582 is the standing example: same item, same check_value, corrected
    admissibility sentence. S1 compares DIFFERENT items only, so this must not
    be caught.
    """
    contracts = _contract(
        tmp_path, "OMN-11111", {"dod-a-pr-101": ["gh pr view 101 --json state"]}
    )
    check = "gh api repos/OmniNode-ai/x/pulls/101 --jq .merged | grep -qx true"
    first = _supersede(tmp_path, "OMN-11111", "dod-a-pr-101", "900", check)
    second = _supersede(tmp_path, "OMN-11111", "dod-a-pr-101", "901", check)
    assert _rules(first, contracts) == set()
    assert _rules(second, contracts) == set()


def test_whitespace_folding_cannot_launder_a_duplicate(tmp_path: Path) -> None:
    """yamlfmt reflows long scalars; the comparison must be whitespace-normal.

    Without normalisation a producer could evade S1 by letting the formatter
    wrap one of the two copies differently.
    """
    contracts = _contract(
        tmp_path,
        "OMN-11111",
        {"dod-a-pr-101": ["gh pr view 101"], "dod-b-pr-202": ["gh pr view 202"]},
    )
    a = _supersede(tmp_path, "OMN-11111", "dod-a-pr-101", "900", "echo  one   two")
    b = _supersede(tmp_path, "OMN-11111", "dod-b-pr-202", "900", "echo one two")
    assert "S1" in _rules(a, contracts)
    assert "S1" in _rules(b, contracts)


def test_generic_receipt_paths_are_not_anchors(tmp_path: Path) -> None:
    """``command.yaml`` identifies no item; it must not satisfy family binding."""
    contracts = _contract(
        tmp_path,
        "OMN-11111",
        {
            "dod-a": [
                "grep -q '^status: PASS$' "
                "drift/dod_receipts/OMN-11111/dod-a/command.yaml"
            ]
        },
    )
    path = _supersede(
        tmp_path,
        "OMN-11111",
        "dod-a",
        "900",
        "grep -q 'anything' some/other/command.yaml",
    )
    assert "S2" in _rules(path, contracts)


# ---------------------------------------------------------------------------
# The append-only cure, and every way of faking it
# ---------------------------------------------------------------------------


def _mispaired_pair(tmp_path: Path) -> tuple[Path, Path]:
    contracts = _contract(
        tmp_path,
        "OMN-11111",
        {
            "dod-a-pr-101": ["gh pr view 101 --json state"],
            "dod-b-pr-202": ["gh pr view 202 --json state"],
        },
    )
    shared = "gh api repos/OmniNode-ai/x/contents/unrelated.py | grep -c def"
    bad_a = _supersede(tmp_path, "OMN-11111", "dod-a-pr-101", "2552", shared)
    _supersede(tmp_path, "OMN-11111", "dod-b-pr-202", "2552", shared)
    return contracts, bad_a


def test_higher_token_clean_repair_cures_the_target(tmp_path: Path) -> None:
    contracts, bad_a = _mispaired_pair(tmp_path)
    assert _rules(bad_a, contracts) == {"S1", "S2"}
    _supersede(
        tmp_path,
        "OMN-11111",
        "dod-a-pr-101",
        "15459",
        "gh api repos/OmniNode-ai/x/pulls/101 --jq .merged | grep -qx true",
        supersedes=bad_a.relative_to(tmp_path).as_posix(),
    )
    gate._repaired_targets.cache_clear()
    gate._load_mapping.cache_clear()
    gate._cohort_members.cache_clear()
    assert _rules(bad_a, contracts) == set()


def test_lower_token_repair_does_not_cure(tmp_path: Path) -> None:
    """A lower token never becomes the active receipt, so it cures nothing.

    ``validator_receipt_supersession`` resolves the highest ``NNNN``; a
    lower-numbered "repair" leaves the wrong-item record authoritative. The
    gate must not accept a cosmetic cure.
    """
    contracts, bad_a = _mispaired_pair(tmp_path)
    _supersede(
        tmp_path,
        "OMN-11111",
        "dod-a-pr-101",
        "100",
        "gh api repos/OmniNode-ai/x/pulls/101 --jq .merged | grep -qx true",
        supersedes=bad_a.relative_to(tmp_path).as_posix(),
    )
    gate._repaired_targets.cache_clear()
    gate._load_mapping.cache_clear()
    gate._cohort_members.cache_clear()
    assert _rules(bad_a, contracts) == {"S1", "S2"}


def test_repair_that_is_itself_a_rebind_does_not_cure(tmp_path: Path) -> None:
    """Otherwise "repair" is a second laundering hop with a nicer filename."""
    contracts, bad_a = _mispaired_pair(tmp_path)
    _supersede(
        tmp_path,
        "OMN-11111",
        "dod-a-pr-101",
        "15459",
        "gh api repos/OmniNode-ai/x/contents/still-unrelated.py | grep -c def",
        supersedes=bad_a.relative_to(tmp_path).as_posix(),
    )
    gate._repaired_targets.cache_clear()
    gate._load_mapping.cache_clear()
    gate._cohort_members.cache_clear()
    assert "S2" in _rules(bad_a, contracts)


def test_repair_pointing_at_the_base_receipt_does_not_cure(tmp_path: Path) -> None:
    """A plain rebind is not a repair: it must name the mis-paired RECORD."""
    contracts, bad_a = _mispaired_pair(tmp_path)
    _supersede(
        tmp_path,
        "OMN-11111",
        "dod-a-pr-101",
        "15459",
        "gh api repos/OmniNode-ai/x/pulls/101 --jq .merged | grep -qx true",
    )
    gate._repaired_targets.cache_clear()
    gate._load_mapping.cache_clear()
    gate._cohort_members.cache_clear()
    assert _rules(bad_a, contracts) == {"S1", "S2"}


# ---------------------------------------------------------------------------
# Baseline ratchet
# ---------------------------------------------------------------------------


def test_baseline_suppresses_only_listed_paths(tmp_path: Path) -> None:
    contracts, bad_a = _mispaired_pair(tmp_path)
    listed = frozenset({bad_a.as_posix()})
    assert gate.check_supersession_file(bad_a, contracts, listed) == []
    assert gate.check_supersession_file(bad_a, contracts, frozenset()) != []


def test_live_corpus_matches_the_frozen_baseline_both_ways(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set equality in both directions — new debt fails, stale entries fail.

    This is the ratchet: padding the baseline to make a new supersession pass
    and forgetting to shrink it after a repair are both hard failures.
    """
    monkeypatch.chdir(_REPO_ROOT)
    findings = gate.scan_supersession_corpus(_REL_RECEIPTS, _REL_CONTRACTS)
    observed = set(gate._baseline_entries(findings))
    baseline = set(gate.load_supersession_baseline(_BASELINE))
    assert observed - baseline == set(), "new violations absent from the baseline"
    assert baseline - observed == set(), "baseline entries that no longer violate"


def test_the_eight_repaired_files_are_absent_from_the_baseline() -> None:
    """GREEN half of the RED/GREEN pair, asserted on the committed artefacts."""
    baseline = gate.load_supersession_baseline(_BASELINE)
    for item in _OCC_5534_ITEMS:
        entry = f"drift/dod_receipts/OMN-15395/{item}/command.supersede.2552.yaml"
        assert not any(line.startswith(entry) for line in baseline), entry
        repair = _RECEIPTS / "OMN-15395" / item / "command.supersede.15459.yaml"
        assert repair.is_file(), repair
        assert gate.check_supersession_file(repair, _CONTRACTS, frozenset()) == []


# ---------------------------------------------------------------------------
# Anti-removal anchor
# ---------------------------------------------------------------------------


def test_wiring_anchor_passes_on_the_live_workflow() -> None:
    assert gate.check_supersession_wiring(_CI_YAML) == []


@pytest.mark.parametrize(
    "mutation",
    ["drop_job", "declares_needs", "add_if", "drop_corpus_step"],
)
def test_wiring_anchor_fires_on_each_removal_vector(
    tmp_path: Path, mutation: str
) -> None:
    """OMN-15768: ``declares_needs`` replaces the retired ``drop_needs``
    vector -- ci-summary must carry NO ``needs:`` now (a no-needs poller), so
    ADDING one is the regression, not removing one."""
    data = yaml.safe_load(_CI_YAML.read_text())
    jobs = data["jobs"]
    job_id = "supersession-binding-ratchet"
    if mutation == "drop_job":
        del jobs[job_id]
    elif mutation == "declares_needs":
        jobs["ci-summary"]["needs"] = [job_id]
    elif mutation == "add_if":
        jobs[job_id]["if"] = "github.event_name == 'push'"
    elif mutation == "drop_corpus_step":
        jobs[job_id]["steps"] = [
            step
            for step in jobs[job_id]["steps"]
            if "--supersession-corpus" not in str(step.get("run", ""))
        ]
    mutated = tmp_path / "ci.yml"
    mutated.write_text(yaml.safe_dump(data, sort_keys=False))
    assert gate.check_supersession_wiring(mutated) != []


def test_wiring_anchor_fires_when_job_not_registered_in_strict_gate_jobs(
    tmp_path: Path,
) -> None:
    """Replaces the retired ``drop_strict_check`` vector: the strict check is
    no longer a grep-able bash line in ci.yml -- it is registration in
    scripts/ci/ci_summary_gate.py's STRICT_GATE_JOBS."""
    gate_module = _REPO_ROOT / "scripts" / "ci" / "ci_summary_gate.py"
    gate_source = gate_module.read_text(encoding="utf-8")
    mutated_gate = gate_source.replace(
        '"Supersession Binding Ratchet (OMN-15459)",\n', ""
    )
    assert mutated_gate != gate_source, "the registration line was not found to strip"
    gate_path = tmp_path / "ci_summary_gate.py"
    gate_path.write_text(mutated_gate, encoding="utf-8")
    failures = gate.check_supersession_wiring(_CI_YAML, gate_module_path=gate_path)
    assert any("STRICT_GATE_JOBS" in f for f in failures), failures
