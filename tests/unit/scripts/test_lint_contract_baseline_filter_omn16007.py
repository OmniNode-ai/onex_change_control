# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-16007: the linter's exit code honours the frozen shrink-only baselines.

OMN-16007 removes ci.yml's ``github.base_ref != 'dev'`` carve-out from the
"Run full pre-commit --all-files" step. That carve-out meant the step never ran
on ``dev`` -- this repo's default branch, where every real merge lands -- so
``lint_contract_check_values.py`` had never once been executed over the whole
corpus on the merge path. Its first real execution surfaced 1175 blocking
findings across 7823 contracts, every one byte-identical to ``origin/dev``.

Rules A/B/C/D/F already had frozen censuses under ``.onex_ratchets/``; they were
simply never read by the linter itself, only by the corpus ratchet in pytest.
The repair reads them here too (plus a new Rule G census for the one class that
had none), so inherited debt passes and anything NEW hard-fails.

These tests pin the mechanism, not the census. The census lives in the baseline
files and is asserted for set-equality by
``test_lint_contract_check_values_corpus_baseline.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import lint_contract_check_values as linter  # noqa: E402

# An OMN-14431 inert-token-prefix value (Rule G): the PR_NUMBER=1721 prefix is
# dead because ${PR_NUMBER} is pre-substituted with the runner's own PR before
# the shell applies the assignment.
_INERT_VALUE = (
    "PR_NUMBER=1721; REPO=OmniNode-ai/omnimarket; "
    "gh pr view ${PR_NUMBER} --repo ${REPO} --json number,state"
)

# A fail-open pattern (`|| true`) that has NO baseline file and therefore can
# never be suppressed.
_UNBASELINEABLE_VALUE = "gh pr checks 1721 --repo OmniNode-ai/omnimarket || true"


def _write_contract(contracts_dir: Path, name: str, dod_id: str, value: str) -> Path:
    contracts_dir.mkdir(parents=True, exist_ok=True)
    path = contracts_dir / name
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "ticket_id": name.removesuffix(".yaml"),
                "dod_evidence": [
                    {
                        "id": dod_id,
                        "description": "fixture",
                        "source": "generated",
                        "checks": [{"check_type": "command", "check_value": value}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_baselines(ratchets: Path, rule_g_entries: list[str]) -> Path:
    """Write a full set of baseline files, only Rule G non-empty."""
    ratchets.mkdir(parents=True, exist_ok=True)
    for rule, filename in linter.BASELINE_FILES.items():
        entries = rule_g_entries if rule == "inert-token-prefix" else []
        (ratchets / filename).write_text(
            yaml.safe_dump({"baseline": entries}), encoding="utf-8"
        )
    return ratchets


@pytest.mark.unit
def test_baselined_finding_is_suppressed_and_a_new_one_is_not(tmp_path: Path) -> None:
    """The whole point: inherited debt passes, a NEW instance still blocks.

    Both contracts carry the IDENTICAL defect. The only thing separating them is
    presence in the frozen census, which is exactly the shrink-only contract.
    """
    contracts = tmp_path / "contracts"
    old = _write_contract(contracts, "OMN-1000.yaml", "dod-legacy", _INERT_VALUE)
    new = _write_contract(contracts, "OMN-2000.yaml", "dod-fresh", _INERT_VALUE)
    baselines = linter.load_baselines(
        _write_baselines(
            tmp_path / ".onex_ratchets", ["contracts/OMN-1000.yaml::dod-legacy"]
        )
    )

    findings = linter.lint_contract(old) + linter.lint_contract(new)
    blocking, baselined = linter.partition_findings(findings, baselines)

    assert [f[0] for f in baselined] == [str(old)]
    assert [f[0] for f in blocking] == [str(new)]


@pytest.mark.unit
def test_regression_in_a_repaired_file_hard_fails(tmp_path: Path) -> None:
    """Shrink-only means shrinking is PERMANENT.

    Once a contract is repaired and its entry deleted from the baseline, the
    same defect reappearing later is indistinguishable from a brand-new one --
    and must block. Without this the ratchet would be a one-way amnesty per
    file rather than per instance.
    """
    contracts = tmp_path / "contracts"
    path = _write_contract(contracts, "OMN-3000.yaml", "dod-repaired", _INERT_VALUE)
    # Baseline is EMPTY for this id -- it was repaired earlier and shrunk out.
    baselines = linter.load_baselines(_write_baselines(tmp_path / ".onex_ratchets", []))

    blocking, baselined = linter.partition_findings(
        linter.lint_contract(path), baselines
    )
    assert not baselined
    assert len(blocking) == 1


@pytest.mark.unit
def test_baseline_is_keyed_per_file_and_per_dod_id(tmp_path: Path) -> None:
    """A suppression must not leak to a same-named id in a different contract,
    nor to a different id in the same contract."""
    contracts = tmp_path / "contracts"
    same_id_other_file = _write_contract(
        contracts, "OMN-4001.yaml", "dod-shared", _INERT_VALUE
    )
    other_id_same_file = _write_contract(
        contracts, "OMN-4000.yaml", "dod-other", _INERT_VALUE
    )
    baselines = linter.load_baselines(
        _write_baselines(
            tmp_path / ".onex_ratchets", ["contracts/OMN-4000.yaml::dod-shared"]
        )
    )

    for path in (same_id_other_file, other_id_same_file):
        blocking, baselined = linter.partition_findings(
            linter.lint_contract(path), baselines
        )
        assert not baselined, path
        assert len(blocking) == 1, path


@pytest.mark.unit
def test_rule_b_pr_binding_keys_off_the_fragment_not_the_label() -> None:
    """Rule B's finding carries its dod_id in the FRAGMENT slot, not the label.

    ``_pr_binding_violation`` is appended as ``(path, "pr-binding: ...",
    dod_id)`` while every other detector appends ``(path, f"{dod_id}: {rule}",
    <source fragment>)``. Keying Rule B off the label head would collapse all
    834 baselined entries onto the single key ``contracts/X.yaml::pr-binding``
    and thereby suppress every FUTURE Rule B violation in the corpus -- a
    silent, total defeat of the rule that no other test would notice, because
    the corpus ratchet reads ``lint_contract`` directly and never exercises
    this mapping.
    """
    rule_token, key = linter.baseline_key(
        "contracts/OMN-5000.yaml",
        "pr-binding: id embeds PR #1721 but ...",
        "dod-x-pr-1721",
    )
    assert (rule_token, key) == ("pr-binding", "contracts/OMN-5000.yaml::dod-x-pr-1721")


@pytest.mark.unit
def test_rules_without_a_baseline_file_can_never_be_suppressed(tmp_path: Path) -> None:
    """`|| true` and friends have no census and must stay unconditionally fatal.

    Guards against the failure mode where a future red build is made green by
    adding a key to BASELINE_FILES: a rule with no entry there has no path to
    suppression at all, whatever a baseline file happens to contain.
    """
    contracts = tmp_path / "contracts"
    path = _write_contract(
        contracts, "OMN-6000.yaml", "dod-failopen", _UNBASELINEABLE_VALUE
    )
    baselines = linter.load_baselines(
        _write_baselines(
            tmp_path / ".onex_ratchets", ["contracts/OMN-6000.yaml::dod-failopen"]
        )
    )

    blocking, baselined = linter.partition_findings(
        linter.lint_contract(path), baselines
    )
    assert not baselined
    assert blocking
    assert all("trailing || true" in label for _p, label, _f in blocking)


@pytest.mark.unit
def test_a_file_outside_contracts_is_never_baselineable() -> None:
    """Suppression keys are ``contracts/<name>::<id>``, so a same-named file
    elsewhere on disk must not inherit another contract's amnesty."""
    rule_token, key = linter.baseline_key(
        "templates/OMN-1000.yaml", "dod-legacy: inert-token-prefix: ...", "frag"
    )
    assert (rule_token, key) == (None, None)


@pytest.mark.unit
def test_parse_and_read_errors_are_never_baselineable() -> None:
    """``read-error`` / ``yaml-parse-error`` have no ``<dod_id>: <rule>`` shape.

    They must fall through to the fatal path -- a contract the linter cannot
    parse is the one case where suppressing the finding would hide an unknown
    quantity rather than a measured one.
    """
    for label in ("read-error", "yaml-parse-error"):
        assert linter.baseline_key("contracts/OMN-1.yaml", label, "boom") == (
            None,
            None,
        )


@pytest.mark.unit
def test_a_missing_baseline_file_raises_rather_than_failing_open() -> None:
    """An unreadable census must NOT be read as "nothing is baselined".

    Failing open would flip this filter from suppressing 1175 known findings to
    blocking on all of them -- which reads as a mass regression and trains the
    next reader to delete the filter rather than restore the file.
    """
    with pytest.raises(FileNotFoundError, match=r"omn_15382_rule_a_baseline\.yaml"):
        linter.load_baselines(Path(__file__).parent / "no_such_ratchets_dir")


@pytest.mark.unit
def test_every_declared_baseline_file_exists_and_parses() -> None:
    """The shipped BASELINE_FILES map resolves against the real repo."""
    baselines = linter.load_baselines()
    assert set(baselines) == set(linter.BASELINE_FILES)
    for rule, entries in baselines.items():
        for entry in entries:
            assert entry.startswith("contracts/"), (rule, entry)
            assert "::" in entry, (rule, entry)
