# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-13888: a receipt with neither an entry hash nor a contract entry is refused.

RED/GREEN controls for the ORPHAN_BINDING leg of
``scripts/validation/check_receipt_hardening.py``.

The detector is exercised against the LIVE defect shape, copied byte-for-byte
out of ``drift/dod_receipts/OMN-17530/dod-OmniNode-ai-omnibase_infra-pr-3326/
command.yaml`` as it landed on ``dev`` — a fixture, not the live file, so these
tests never depend on (or mutate) a receipt another lane is repairing. The
contract fixture reproduces the same relationship: the ticket contract declares
the FIRST consumer's item and never the second's, which is exactly what
``node_occ_companion_compute``'s merged path produced for every 2nd-and-later
product PR citing one ticket.

RED before, measured against ``dev`` @ ``51b5a53f86``: the pre-change gate
accepts every orphan below — the string ``orphan`` did not appear anywhere in
the module, so the whole-file branch of ``_contract_hash_violation`` compared
``sha256(contracts/<ticket>.yaml)`` and reported nothing. The control is
reproduced mechanically in ``test_red_control_pre_change_gate_accepts_the_orphan``
against the version of the module recorded at the merge base.

Every rule is exercised in both polarities, and each escape route from the
grandfather mechanism (baseline suppression scope, the ``open_repairs``
carve-out, baseline regeneration) is proven not to be a way to pass a NEW
orphan.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GATE_PATH = _REPO_ROOT / "scripts" / "validation" / "check_receipt_hardening.py"
_BASELINE = _REPO_ROOT / ".onex_ratchets" / "omn_13888_orphan_receipt_baseline.yaml"
_ORPHAN_RULE_INTRODUCING_COMMIT = "8ac57f3e8928691ee489ae628eb2aeb875b7895a"

# The three live OMN-17530 orphans. They are recorded under ``open_repairs:``
# rather than ``violations:`` in the baseline, so the gate still names them.
_OMN_17530_OPEN_REPAIRS = (
    "drift/dod_receipts/OMN-17530/dod-OmniNode-ai-omnibase_infra-pr-3326/command.yaml",
    "drift/dod_receipts/OMN-17530/dod-OmniNode-ai-omnibase_infra-pr-3328/command.yaml",
    "drift/dod_receipts/OMN-17530/dod-OmniNode-ai-omnibase_infra-pr-3332/command.yaml",
)

# Copied from the live receipt; ``commit_sha`` widened to a full 40-hex value is
# unnecessary (the run_timestamp here predates OMN_15461_CUTOFF is FALSE — the
# live value is already a full SHA and is kept verbatim).
_ORPHAN_RECEIPT_YAML = """\
---
schema_version: "1.0.0"
ticket_id: "OMN-17530"
evidence_item_id: "dod-OmniNode-ai-omnibase_infra-pr-3326"
check_type: "command"
check_value: |-
  gh pr view 3326 --repo OmniNode-ai/omnibase_infra --json number,state,headRefName
contract_sha256: "sha256:{contract_sha}"
status: PASS
run_timestamp: "2026-06-13T15:38:54.615438+00:00"
commit_sha: "6dd2dedef6232ce762fb1473b3d8ba5a97a11e59"
runner: "node_occ_companion_compute"
verifier: "occ-evidence-source-autobind"
probe_command: |-
  gh pr view 3326 --repo OmniNode-ai/omnibase_infra --json number,state,headRefName
probe_stdout: |
  {{"headRefName":"jonah/omn-17530-lab-pass-receipt-deps","number":3326,"state":"OPEN"}}
actual_output: |-
  PASS: autobind for OMN-17530 from OmniNode-ai/omnibase_infra#3326.
exit_code: 0
pr_number: 3326
branch: "auto/omninode-ai-omnibase_infra-pr-3326-occ-autobind"
"""

_FIRST_CONSUMER_ITEM = "dod-OmniNode-ai-omnibase_infra-pr-3319"
_SECOND_CONSUMER_ITEM = "dod-OmniNode-ai-omnibase_infra-pr-3326"


def _load_gate(path: Path = _GATE_PATH, name: str = "crh_orphan") -> Any:
    """Load the validator by path (``scripts/validation`` is not a package)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate: Any = _load_gate()


def _contract_data(*item_ids: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-17530",
        "title": "Autobind OCC evidence for OMN-17530",
        "dod_evidence": [
            {
                "id": item_id,
                "description": f"{item_id} — Evidence-Source autobind.",
                "source": "generated",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": (
                            f"gh pr view {item_id.rsplit('-', 1)[-1]} "
                            "--repo OmniNode-ai/omnibase_infra --json number,state"
                        ),
                    }
                ],
            }
            for item_id in item_ids
        ],
    }


def _write_tree(
    tmp_path: Path, *, declared_items: tuple[str, ...], entry_bound: bool
) -> tuple[Path, Path]:
    """Write a contract + the second consumer's receipt; return both paths."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    contract = contracts_dir / "OMN-17530.yaml"
    contract.write_text(
        yaml.safe_dump(_contract_data(*declared_items), sort_keys=False)
    )

    receipt_dir = (
        tmp_path / "drift" / "dod_receipts" / "OMN-17530" / _SECOND_CONSUMER_ITEM
    )
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt = receipt_dir / "command.yaml"
    text = _ORPHAN_RECEIPT_YAML.format(
        contract_sha=gate.compute_contract_sha256(contract)
    )
    if entry_bound:
        entry_hash = gate.compute_contract_entry_sha256(
            yaml.safe_load(contract.read_text()), _SECOND_CONSUMER_ITEM
        )
        text = text.replace(
            "status: PASS", f'contract_entry_sha256: "{entry_hash}"\nstatus: PASS', 1
        )
    receipt.write_text(text)
    return contract, receipt


def _check(receipt: Path, contracts_dir: Path, **kwargs: Any) -> list[str]:
    violations: list[str] = gate.check_receipt_file(receipt, contracts_dir, **kwargs)
    return violations


def test_orphan_receipt_is_refused(tmp_path: Path) -> None:
    """The live shape: whole-file bound, item never declared."""
    contract, receipt = _write_tree(
        tmp_path, declared_items=(_FIRST_CONSUMER_ITEM,), entry_bound=False
    )
    violations = _check(receipt, contract.parent)
    assert len(violations) == 1
    assert gate.ORPHAN_RULE in violations[0]
    assert _SECOND_CONSUMER_ITEM in violations[0]
    # The message must name the repair, not just the symptom.
    assert "contract_entry_sha256" in violations[0]
    assert "do not pad the baseline" in violations[0]


def test_whole_file_bound_receipt_with_a_declared_item_still_passes(
    tmp_path: Path,
) -> None:
    """The 9,908-receipt legacy population is NOT retro-blocked by this rule."""
    contract, receipt = _write_tree(
        tmp_path,
        declared_items=(_FIRST_CONSUMER_ITEM, _SECOND_CONSUMER_ITEM),
        entry_bound=False,
    )
    assert _check(receipt, contract.parent) == []


def test_entry_bound_receipt_survives_a_peer_append_to_an_unrelated_entry(
    tmp_path: Path,
) -> None:
    """The whole point of per-entry binding, in one assertion."""
    contract, receipt = _write_tree(
        tmp_path,
        declared_items=(_FIRST_CONSUMER_ITEM, _SECOND_CONSUMER_ITEM),
        entry_bound=True,
    )
    assert _check(receipt, contract.parent) == []

    data = yaml.safe_load(contract.read_text())
    data["dod_evidence"].append(
        {
            "id": "dod-a-peer-lanes-unrelated-entry",
            "description": "Appended by a lane that never touched this receipt.",
            "source": "generated",
            "checks": [{"check_type": "command", "check_value": "true"}],
        }
    )
    contract.write_text(yaml.safe_dump(data, sort_keys=False))
    assert _check(receipt, contract.parent) == []


def test_whole_file_bound_receipt_is_restaled_by_the_same_peer_append(
    tmp_path: Path,
) -> None:
    """The control for the test above: whole-file binding is what breaks."""
    contract, receipt = _write_tree(
        tmp_path,
        declared_items=(_FIRST_CONSUMER_ITEM, _SECOND_CONSUMER_ITEM),
        entry_bound=False,
    )
    assert _check(receipt, contract.parent) == []

    data = yaml.safe_load(contract.read_text())
    data["dod_evidence"].append(
        {
            "id": "dod-a-peer-lanes-unrelated-entry",
            "description": "Appended by a lane that never touched this receipt.",
            "source": "generated",
            "checks": [{"check_type": "command", "check_value": "true"}],
        }
    )
    contract.write_text(yaml.safe_dump(data, sort_keys=False))
    violations = _check(receipt, contract.parent)
    assert len(violations) == 1
    assert "contract_sha256 mismatch" in violations[0]


def test_repairing_the_orphan_clears_the_violation(tmp_path: Path) -> None:
    """Declare the item and re-mint per entry — the sanctioned repair works."""
    contract, receipt = _write_tree(
        tmp_path,
        declared_items=(_FIRST_CONSUMER_ITEM, _SECOND_CONSUMER_ITEM),
        entry_bound=True,
    )
    assert _check(receipt, contract.parent) == []


def test_pre_cutoff_orphan_is_exempt(tmp_path: Path) -> None:
    """Same legacy exemption every other rule in this file honours."""
    contract, receipt = _write_tree(
        tmp_path, declared_items=(_FIRST_CONSUMER_ITEM,), entry_bound=False
    )
    receipt.write_text(
        receipt.read_text().replace(
            'run_timestamp: "2026-06-13T15:38:54.615438+00:00"',
            'run_timestamp: "2026-06-11T23:59:59+00:00"',
        )
    )
    assert _check(receipt, contract.parent) == []


def test_baseline_suppresses_the_orphan_rule_only(tmp_path: Path) -> None:
    """A baselined path is exempt from ORPHAN_BINDING and from nothing else."""
    contract, receipt = _write_tree(
        tmp_path, declared_items=(_FIRST_CONSUMER_ITEM,), entry_bound=False
    )
    baseline = frozenset({receipt.as_posix()})
    assert _check(receipt, contract.parent, orphan_baseline=baseline) == []

    # Same file, same baseline entry, now also carrying a denylisted verifier:
    # the other rule still fires. Suppression is per-rule, not per-file.
    receipt.write_text(
        receipt.read_text().replace(
            'verifier: "occ-evidence-source-autobind"', 'verifier: "agent"'
        )
    )
    violations = _check(receipt, contract.parent, orphan_baseline=baseline)
    assert len(violations) == 1
    assert gate.ORPHAN_RULE not in violations[0]
    assert "session-local verifier" in violations[0]


def test_a_path_not_in_the_baseline_is_never_suppressed(tmp_path: Path) -> None:
    contract, receipt = _write_tree(
        tmp_path, declared_items=(_FIRST_CONSUMER_ITEM,), entry_bound=False
    )
    baseline = frozenset({"drift/dod_receipts/OMN-00000/dod-other/command.yaml"})
    violations = _check(receipt, contract.parent, orphan_baseline=baseline)
    assert len(violations) == 1
    assert gate.ORPHAN_RULE in violations[0]


def test_open_repairs_are_corpus_members_but_not_suppressed() -> None:
    """The carve-out that keeps a recorded, in-flight repair visible."""
    suppressed = gate.load_orphan_baseline(_BASELINE)
    open_repairs = gate.load_orphan_open_repairs(_BASELINE)
    expected = gate.load_orphan_corpus_expected(_BASELINE)

    assert open_repairs, "the open_repairs list must not be silently emptied"
    for entry in _OMN_17530_OPEN_REPAIRS:
        assert entry in open_repairs
        # The whole point: recorded, but NOT suppressed.
        assert entry not in suppressed
        assert entry in expected


def test_baseline_and_open_repairs_are_disjoint() -> None:
    assert not (
        gate.load_orphan_baseline(_BASELINE) & gate.load_orphan_open_repairs(_BASELINE)
    )


def test_write_orphan_baseline_preserves_open_repairs(tmp_path: Path) -> None:
    """Regeneration may not quietly absorb an open repair into the suppressed set."""
    contract, receipt = _write_tree(
        tmp_path, declared_items=(_FIRST_CONSUMER_ITEM,), entry_bound=False
    )
    baseline_path = tmp_path / ".onex_ratchets" / "baseline.yaml"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        "violations: []\nopen_repairs:\n  - " + receipt.as_posix() + "\n"
    )
    rc = gate.write_orphan_baseline(
        tmp_path / "drift" / "dod_receipts", contract.parent, baseline_path
    )
    assert rc == 0
    assert gate.load_orphan_open_repairs(baseline_path) == frozenset(
        {receipt.as_posix()}
    )
    assert gate.load_orphan_baseline(baseline_path) == frozenset()


def test_corpus_mode_fails_on_a_new_orphan(tmp_path: Path) -> None:
    contract, _receipt = _write_tree(
        tmp_path, declared_items=(_FIRST_CONSUMER_ITEM,), entry_bound=False
    )
    baseline_path = tmp_path / ".onex_ratchets" / "baseline.yaml"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text("violations: []\nopen_repairs: []\n")
    assert (
        gate.run_orphan_corpus(
            tmp_path / "drift" / "dod_receipts", contract.parent, baseline_path
        )
        == 1
    )


def test_corpus_mode_fails_on_a_stale_baseline_entry(tmp_path: Path) -> None:
    """Shrink-only in both directions: a repaired entry must be removed."""
    contract, receipt = _write_tree(
        tmp_path,
        declared_items=(_FIRST_CONSUMER_ITEM, _SECOND_CONSUMER_ITEM),
        entry_bound=True,
    )
    baseline_path = tmp_path / ".onex_ratchets" / "baseline.yaml"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        "violations:\n  - " + receipt.as_posix() + "\nopen_repairs: []\n"
    )
    assert (
        gate.run_orphan_corpus(
            tmp_path / "drift" / "dod_receipts", contract.parent, baseline_path
        )
        == 1
    )


def test_no_baseline_entry_has_stopped_being_an_orphan(monkeypatch: Any) -> None:
    """The shrink-only half, in the only direction that is stable to assert.

    Runs with cwd == repo root and REPO-ROOT-RELATIVE roots, which is what
    pre-commit and the corpus tool pass. That is not cosmetic: a supersession
    record's ``supersedes:`` field holds a repo-root-relative path, and
    ``_active_supersession_candidate`` compares it literally — so an
    absolute-path scan resolves no supersessions at all and reports 19 already
    superseded base receipts as live orphans.

    SUBSET, not set equality, and the asymmetry is measured rather than
    conceded: the producer that mints these is still deployed, so ``dev`` gains
    roughly one new orphan per merged second-consumer companion (439 -> 441 ->
    442 in seventy minutes on the day this landed, each from a lane unrelated to
    this one). A two-way assertion would therefore be red on every PR in the
    repo within minutes of being written, which trains people to pad the
    baseline — the exact behaviour the file's own header forbids. The direction
    asserted here cannot be reddened by a peer merge and still fails the case
    that matters for a ratchet: a baselined entry that has been repaired and not
    removed. The full two-way check remains available as
    ``--orphan-corpus`` for an operator, and wiring it as a required job is a
    follow-up for after the producer fix (omnimarket#2414) is deployed and the
    mint rate is zero.
    """
    monkeypatch.chdir(_REPO_ROOT)
    observed = set(
        gate._orphan_receipt_findings(Path("drift/dod_receipts"), Path("contracts"))
    )
    expected = {
        entry.split("::", 1)[0] for entry in gate.load_orphan_corpus_expected(_BASELINE)
    }
    stale = sorted(expected - observed)
    assert not stale, (
        "baseline entries that are no longer orphans must be removed in the "
        f"PR that repaired them: {stale}"
    )


def test_red_control_pre_change_gate_accepts_the_orphan(tmp_path: Path) -> None:
    """The control: the version of this gate at the merge base reports nothing.

    Loads ``scripts/validation/check_receipt_hardening.py`` from the parent of
    the commit that introduced ORPHAN_BINDING and runs the SAME orphan fixture
    through it. PR merge-base is intentionally not used here: unrelated append
    PRs based after OMN-13888 landed would otherwise compare against the fixed
    validator and turn this red-control into a permanent repo-wide failure.
    """
    pre_change = subprocess.run(
        ["git", "rev-parse", f"{_ORPHAN_RULE_INTRODUCING_COMMIT}^"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if pre_change.returncode != 0:
        pytest.skip("the pre-change ORPHAN_BINDING commit is not available")
    blob = subprocess.run(
        [
            "git",
            "show",
            f"{pre_change.stdout.strip()}:scripts/validation/check_receipt_hardening.py",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if blob.returncode != 0:
        pytest.skip("the pre-change gate module is not retrievable")
    old_path = tmp_path / "check_receipt_hardening_pre_change.py"
    old_path.write_text(blob.stdout)
    old_gate = _load_gate(old_path, "crh_orphan_pre_change")

    contract, receipt = _write_tree(
        tmp_path, declared_items=(_FIRST_CONSUMER_ITEM,), entry_bound=False
    )
    assert old_gate.check_receipt_file(receipt, contract.parent) == []
    assert _check(receipt, contract.parent) != []
