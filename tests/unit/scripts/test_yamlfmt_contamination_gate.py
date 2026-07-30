# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15479: RED/GREEN controls for the yamlfmt contamination ratchet.

These tests prove the gate FIRES on each defect vector rather than asserting
that it exists. Four groups:

* **Detector controls.** The two proven-safe shapes (literal block scalars,
  single-paragraph folded scalars) must stay green, and the two defect shapes
  (the sentinel in a parsed value, a folded scalar carrying an internal
  newline) must go red. A detector that flagged the safe shapes would be a
  7,286-file false-positive wall and would be ignored within a day.
* **Corpus ratchets.** Shrink-only set equality against the frozen baselines, in
  all three directions -- new path, grown count on a baselined path, stale entry
  a live scan no longer reproduces.
* **Wiring.** ``check_wiring`` must fail on every removal vector, not merely
  pass on the real ci.yml.
* **Hook actually fires.** A hook whose ``files:`` regex matches nothing is
  byte-indistinguishable from a passing hook (OMN-15070), so the regex is
  compiled and exercised against real corpus paths, and the declared hook order
  relative to yamlfmt is asserted -- the gate is only useful if it runs BEFORE
  the formatter that does the damage.
"""

from __future__ import annotations

import copy
import importlib.util
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_YAML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_PRECOMMIT_CONFIG = _REPO_ROOT / ".pre-commit-config.yaml"
_HOOK_ID = "check-yamlfmt-contamination"
_WIRING_HOOK_ID = "check-yamlfmt-contamination-wiring"


def _load_gate() -> Any:
    """Load the validator by path.

    ``scripts`` is on ``mypy_path`` but ``scripts/validation`` is not, so a bare
    import type-checks locally and fails ``mypy src/ tests/`` in CI with
    ``import-not-found``. Every other test exercising a ``scripts/validation``
    module uses this loader for the same reason.
    """
    script_path = (
        _REPO_ROOT / "scripts" / "validation" / "check_yamlfmt_contamination.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_yamlfmt_contamination", script_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()

# Built by concatenation so this test file never itself contains the literal
# sentinel in a scannable position -- the gate's own corpus does not include
# tests/, but keeping the marker synthetic makes the intent unambiguous.
SENTINEL = gate.SENTINEL


@lru_cache(maxsize=1)
def _live_corpus() -> tuple[dict[str, int], dict[str, int]]:
    """Scan the whole corpus once; four ratchet tests consume the same scan."""
    scanned: tuple[dict[str, int], dict[str, int]] = gate.scan_corpus(_REPO_ROOT)
    return scanned


# ---------------------------------------------------------------------------
# Detector controls
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sentinel_in_a_parsed_value_is_detected() -> None:
    text = f"actual_output: 'first paragraph. {SENTINEL} second paragraph.'\n"
    assert gate.count_sentinel_in_values(text) == 1


@pytest.mark.unit
def test_sentinel_occurrences_are_counted_not_merely_flagged() -> None:
    """Count granularity is what makes the ratchet catch a GROWN baselined file."""
    text = f"a: 'x {SENTINEL} y {SENTINEL} z'\nb: 'q {SENTINEL} r'\n"
    assert gate.count_sentinel_in_values(text) == 3


@pytest.mark.unit
def test_sentinel_in_a_comment_only_is_not_a_violation() -> None:
    """AC3 is about a committed *value*.

    A ticket contract, baseline header, or runbook that DISCUSSES the marker in
    a comment is documentation, not corrupted evidence. Measured on the live
    corpus at authoring time, raw-byte and parsed-value detection agree exactly
    (510 files / 784 occurrences either way, zero raw-only hits), so choosing
    the precise one costs no coverage and buys the ability to write about the
    defect.
    """
    text = f"# this gate rejects {SENTINEL} in a value\nactual_output: 'clean text'\n"
    assert gate.count_sentinel_in_values(text) == 0


@pytest.mark.unit
def test_unparseable_file_carrying_the_marker_fails_closed() -> None:
    """ "Could not parse it" must never read as "it is clean"."""
    text = f"actual_output: 'unterminated {SENTINEL}\n  - [broken: {{\n"
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(text)
    assert gate.count_sentinel_in_values(text) >= 1


@pytest.mark.unit
def test_multi_paragraph_folded_scalar_is_detected() -> None:
    """The reproduced corruption precondition from the ticket."""
    text = (
        "actual_output: >\n"
        "  PASS (exit 0 on the gate host). The file carries the PROVENANCE stamp.\n"
        "\n"
        "  INDEPENDENT VERIFICATION BY THIS EVIDENCE AUTHOR: both changed files\n"
        "  parse to an identical AST.\n"
    )
    findings = gate.find_folded_internal_newline_scalars(text)
    assert len(findings) == 1
    line, preview = findings[0]
    assert line == 1
    assert "PASS (exit 0" in preview


@pytest.mark.unit
@pytest.mark.parametrize("chomp", [">", ">-", ">+"])
def test_every_folded_chomping_variant_is_covered(chomp: str) -> None:
    text = f"v: {chomp}\n  para one\n\n  para two\n"
    assert len(gate.find_folded_internal_newline_scalars(text)) == 1


@pytest.mark.unit
def test_single_paragraph_folded_scalar_is_not_flagged() -> None:
    """Proven-safe control 1 from the ticket: survives yamlfmt byte-identical.

    7,286 corpus files carry this shape. Flagging it would make the gate a
    false-positive wall rather than a signal.
    """
    text = (
        "probe_stdout: >\n"
        '  {"baseRefName":"dev","headRefOid":"712ccb07","number":2565,"state":"OPEN"}\n'
    )
    assert gate.find_folded_internal_newline_scalars(text) == []


@pytest.mark.unit
def test_literal_block_scalar_is_not_flagged_even_over_max_line_length() -> None:
    """Proven-safe control 2: the prescribed fix must not itself be a violation.

    Includes a line longer than the repo's ``max_line_length: 100`` -- the
    ticket measured that a literal scalar survives yamlfmt byte-identical even
    then, which is exactly why literal is the recommended style.
    """
    long_line = "  - bullet one that is quite long and " + ("x" * 90)
    assert len(long_line) > 100
    text = f"actual_output: |-\n  para one\n\n  para two\n{long_line}\n"
    assert gate.find_folded_internal_newline_scalars(text) == []
    assert gate.count_sentinel_in_values(text) == 0


@pytest.mark.unit
def test_folded_scalar_with_a_more_indented_line_is_flagged() -> None:
    """More-indented lines inside a folded scalar keep their newlines too.

    "multi-paragraph" is the dominant instance of the defect, but the general
    precondition is a folded scalar whose PARSED VALUE contains an internal
    newline -- which a more-indented continuation also produces.
    """
    text = "v: >\n  normal text here\n    more indented, newline preserved\n"
    assert len(gate.find_folded_internal_newline_scalars(text)) == 1


@pytest.mark.unit
def test_plain_and_quoted_multiline_scalars_are_not_rule_f_violations() -> None:
    """Rule F is scoped to folded scalars on measured evidence, not by guess.

    10,086 corpus files carry a multi-line quoted scalar while only 510 carry
    the sentinel, so quoted multi-line is not a reliable precondition and
    baselining it would freeze ~10k files of non-debt.
    """
    text = 'v: "first line\n  second line"\nw: plain scalar\n'
    assert gate.find_folded_internal_newline_scalars(text) == []


@pytest.mark.unit
def test_gate_is_read_only_on_the_files_it_scans(tmp_path: Path) -> None:
    """Formatter mutation is the disease; an auto-fixing gate repeats it."""
    target = tmp_path / "command.yaml"
    original = "actual_output: >\n  para one\n\n  para two\n"
    target.write_text(original, encoding="utf-8")
    before = target.read_bytes()
    gate.scan_file(target)
    assert target.read_bytes() == before


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("contracts/OMN-15479.yaml", True),
        ("drift/dod_receipts/OMN-11599/dod-x/command.yaml", True),
        ("drift/dod_receipts/OMN-1/d/command.supersede.0001.yaml", True),
        ("contracts/nested/thing.yml", True),
        ("src/onex_change_control/models/model_x.py", False),
        ("docs/runbooks/thing.yaml", False),
        (".onex_ratchets/omn_15479_yamlfmt_sentinel_baseline.yaml", False),
        ("contracts/README.md", False),
    ],
)
def test_scope_predicate(rel: str, *, expected: bool) -> None:
    assert gate.is_in_scope(rel) is expected


# ---------------------------------------------------------------------------
# Baseline loading -- an absent/malformed baseline must never read as "clean"
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_baseline_raises_rather_than_reading_as_empty(tmp_path: Path) -> None:
    with pytest.raises(gate.CorpusUnreadableError):
        gate.load_baseline(tmp_path / "nope.yaml")


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [
        "just a string\n",
        "other_key: {}\n",
        "baseline:\n  - a-list-not-a-mapping\n",
        "baseline:\n  contracts/x.yaml: 0\n",
        "baseline:\n  contracts/x.yaml: not-an-int\n",
    ],
)
def test_malformed_baseline_raises(tmp_path: Path, body: str) -> None:
    path = tmp_path / "baseline.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(gate.CorpusUnreadableError):
        gate.load_baseline(path)


# ---------------------------------------------------------------------------
# Ratchet direction controls
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ratchet_flags_a_new_path() -> None:
    new, grown, stale = gate.diff_against_baseline(
        {"contracts/A.yaml": 1, "contracts/B.yaml": 2}, {"contracts/A.yaml": 1}
    )
    assert new == {"contracts/B.yaml": 2}
    assert not grown
    assert not stale


@pytest.mark.unit
def test_ratchet_flags_a_grown_count_on_a_baselined_path() -> None:
    new, grown, stale = gate.diff_against_baseline(
        {"contracts/A.yaml": 3}, {"contracts/A.yaml": 1}
    )
    assert not new
    assert grown == {"contracts/A.yaml": (1, 3)}
    assert not stale


@pytest.mark.unit
def test_ratchet_flags_a_stale_baseline_entry() -> None:
    """Removing debt without shrinking the baseline is as loud as padding it."""
    new, grown, stale = gate.diff_against_baseline({}, {"contracts/A.yaml": 1})
    assert not new
    assert not grown
    assert stale == {"contracts/A.yaml": 1}


@pytest.mark.unit
def test_ratchet_is_clean_on_exact_match() -> None:
    live = {"contracts/A.yaml": 2, "contracts/B.yaml": 1}
    assert gate.diff_against_baseline(live, dict(live)) == ({}, {}, {})


# ---------------------------------------------------------------------------
# Live corpus ratchets -- shrink-only set equality against the frozen baselines
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sentinel_corpus_matches_frozen_baseline_exactly() -> None:
    live_sentinel, _live_folded = _live_corpus()
    baseline = gate.load_baseline(_REPO_ROOT / gate.SENTINEL_BASELINE_REL)
    new, grown, stale = gate.diff_against_baseline(live_sentinel, baseline)

    assert not new, (
        f"{len(new)} file(s) carry the yamlfmt sentinel in a parsed value but "
        f"are not in the frozen shrink-only baseline "
        f"({gate.SENTINEL_BASELINE_REL}): {sorted(new)[:20]}. The marker in a "
        "committed evidence value is proof the formatter rewrote content a "
        "verifier authored. Repair the receipt; do not extend the baseline."
    )
    assert not grown, (
        f"{len(grown)} baselined file(s) gained NEW sentinel occurrences: "
        f"{sorted(grown)[:20]}. A baselined path is frozen debt at a frozen "
        "count, not a licence to add more corruption to an already-corrupt file."
    )
    assert not stale, (
        f"{len(stale)} baseline entr{'y is' if len(stale) == 1 else 'ies are'} "
        f"no longer reproduced by a live scan, but {gate.SENTINEL_BASELINE_REL} "
        f"was not shrunk to match: {sorted(stale)[:20]}. Shrink the baseline in "
        "the same commit that repairs the file."
    )


@pytest.mark.unit
def test_folded_corpus_matches_frozen_baseline_exactly() -> None:
    _live_sentinel, live_folded = _live_corpus()
    baseline = gate.load_baseline(_REPO_ROOT / gate.FOLDED_BASELINE_REL)
    new, grown, stale = gate.diff_against_baseline(live_folded, baseline)

    assert not new, (
        f"{len(new)} file(s) carry a NEW folded (`>`) block scalar with an "
        f"internal newline: {sorted(new)[:20]}. This is the shape yamlfmt "
        "corrupts on the very next commit that touches the file. Re-author it "
        "as a literal block scalar (`|` / `|-`), which survives yamlfmt "
        "byte-identical even above max_line_length."
    )
    assert not grown, (
        f"{len(grown)} baselined file(s) gained NEW folded scalars: "
        f"{sorted(grown)[:20]}."
    )
    assert not stale, (
        f"{len(stale)} baseline entr{'y is' if len(stale) == 1 else 'ies are'} "
        f"no longer reproduced by a live scan, but {gate.FOLDED_BASELINE_REL} "
        f"was not shrunk to match: {sorted(stale)[:20]}."
    )


@pytest.mark.unit
def test_baselines_are_frozen_debt_not_an_empty_formality() -> None:
    """A silently-emptied baseline plus a silently-emptied scan both pass set
    equality. Pinning the authoring-time magnitudes makes that visible.
    """
    sentinel_baseline = gate.load_baseline(_REPO_ROOT / gate.SENTINEL_BASELINE_REL)
    folded_baseline = gate.load_baseline(_REPO_ROOT / gate.FOLDED_BASELINE_REL)
    assert sentinel_baseline, "the Rule S baseline may only SHRINK, never vanish"
    assert folded_baseline, "the Rule F baseline may only SHRINK, never vanish"
    assert len(sentinel_baseline) <= 510
    assert len(folded_baseline) <= 14
    assert all(rel.endswith((".yaml", ".yml")) for rel in sentinel_baseline)
    assert all(gate.is_in_scope(rel) for rel in sentinel_baseline)
    assert all(gate.is_in_scope(rel) for rel in folded_baseline)


@pytest.mark.unit
def test_corpus_mode_is_green_on_the_real_tree() -> None:
    """The 510 baselined files must NOT fail; a permanently-red gate is not a
    gate, it is an outage.
    """
    assert gate.check_corpus(_REPO_ROOT) == []


# ---------------------------------------------------------------------------
# Per-file (pre-commit) mode
# ---------------------------------------------------------------------------


def _fake_repo(tmp_path: Path, sentinel_base: str = "", folded_base: str = "") -> Path:
    ratchets = tmp_path / ".onex_ratchets"
    ratchets.mkdir()
    (tmp_path / gate.SENTINEL_BASELINE_REL).write_text(
        f"baseline:\n{sentinel_base}" if sentinel_base else "baseline: {}\n",
        encoding="utf-8",
    )
    (tmp_path / gate.FOLDED_BASELINE_REL).write_text(
        f"baseline:\n{folded_base}" if folded_base else "baseline: {}\n",
        encoding="utf-8",
    )
    (tmp_path / "contracts").mkdir()
    return tmp_path


@pytest.mark.unit
def test_per_file_mode_rejects_a_newly_contaminated_file(tmp_path: Path) -> None:
    root = _fake_repo(tmp_path, sentinel_base="  contracts/OTHER.yaml: 1\n")
    target = root / "contracts" / "OMN-99999.yaml"
    target.write_text(f"actual_output: 'a {SENTINEL} b'\n", encoding="utf-8")

    failures = gate.check_paths(root, [target])
    assert len(failures) == 1
    assert "contracts/OMN-99999.yaml" in failures[0]
    assert "baseline allows 0" in failures[0]


@pytest.mark.unit
def test_per_file_mode_rejects_a_new_folded_multi_paragraph_scalar(
    tmp_path: Path,
) -> None:
    root = _fake_repo(tmp_path)
    target = root / "contracts" / "OMN-99999.yaml"
    target.write_text("actual_output: >\n  para one\n\n  para two\n", encoding="utf-8")

    failures = gate.check_paths(root, [target])
    assert len(failures) == 1
    assert "contracts/OMN-99999.yaml" in failures[0]
    assert "literal block scalar" in failures[0]


@pytest.mark.unit
def test_per_file_mode_does_not_fail_a_baselined_file(tmp_path: Path) -> None:
    """The 510 must not red every PR that touches one of them."""
    root = _fake_repo(tmp_path, sentinel_base="  contracts/OMN-99999.yaml: 1\n")
    target = root / "contracts" / "OMN-99999.yaml"
    target.write_text(f"actual_output: 'a {SENTINEL} b'\n", encoding="utf-8")
    assert gate.check_paths(root, [target]) == []


@pytest.mark.unit
def test_per_file_mode_rejects_growth_inside_a_baselined_file(tmp_path: Path) -> None:
    root = _fake_repo(tmp_path, sentinel_base="  contracts/OMN-99999.yaml: 1\n")
    target = root / "contracts" / "OMN-99999.yaml"
    target.write_text(f"a: 'x {SENTINEL} y'\nb: 'p {SENTINEL} q'\n", encoding="utf-8")
    failures = gate.check_paths(root, [target])
    assert len(failures) == 1
    assert "baseline allows 1" in failures[0]


@pytest.mark.unit
def test_per_file_mode_fails_loudly_on_a_named_but_missing_path(
    tmp_path: Path,
) -> None:
    """An exit-0-on-missing-path gate is byte-indistinguishable from a passing
    one (OMN-14666/14668).
    """
    root = _fake_repo(tmp_path)
    failures = gate.check_paths(root, [root / "contracts" / "ghost.yaml"])
    assert len(failures) == 1
    assert "is not a file" in failures[0]


@pytest.mark.unit
def test_per_file_mode_is_green_on_a_clean_literal_receipt(tmp_path: Path) -> None:
    root = _fake_repo(tmp_path)
    target = root / "contracts" / "OMN-99999.yaml"
    target.write_text(
        "actual_output: |-\n  para one\n\n  para two\nexit_code: 0\n", encoding="utf-8"
    )
    assert gate.check_paths(root, [target]) == []


# ---------------------------------------------------------------------------
# Wiring -- prove the anchor fires on each removal vector
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _real_ci() -> dict[str, Any]:
    data = yaml.safe_load(_CI_YAML.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _wiring_failures(mutate: Any) -> list[str]:
    """Apply ``mutate`` to a deep copy of the real ci.yml and re-check wiring."""
    data = copy.deepcopy(_real_ci())
    mutate(data["jobs"])
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as handle:
        yaml.safe_dump(data, handle)
        path = Path(handle.name)
    try:
        failures: list[str] = gate.check_wiring(path)
    finally:
        path.unlink(missing_ok=True)
    return failures


@pytest.mark.unit
def test_wiring_is_intact_on_the_real_ci_yaml() -> None:
    assert gate.check_wiring(_CI_YAML) == []


@pytest.mark.unit
def test_wiring_fails_when_the_job_is_deleted() -> None:
    failures = _wiring_failures(lambda jobs: jobs.pop("yamlfmt-contamination-ratchet"))
    assert any("is absent" in f for f in failures)


@pytest.mark.unit
def test_wiring_fails_when_the_job_becomes_conditional() -> None:
    failures = _wiring_failures(
        lambda jobs: jobs["yamlfmt-contamination-ratchet"].update(
            {"if": "github.base_ref != 'dev'"}
        )
    )
    assert any("declares `if:`" in f for f in failures)


@pytest.mark.unit
def test_wiring_fails_when_the_job_gains_a_needs_chain() -> None:
    failures = _wiring_failures(
        lambda jobs: jobs["yamlfmt-contamination-ratchet"].update(
            {"needs": ["zone-filter"]}
        )
    )
    assert any("declares `needs:`" in f for f in failures)


@pytest.mark.unit
def test_wiring_fails_when_the_corpus_step_is_gutted() -> None:
    """The job NAME is not the gate; executing the corpus ratchet is."""

    def gut(jobs: dict[str, Any]) -> None:
        jobs["yamlfmt-contamination-ratchet"]["steps"] = [
            {"name": "nothing", "run": "echo ok"}
        ]

    failures = _wiring_failures(gut)
    assert any("--corpus" in f for f in failures)
    assert any("test_yamlfmt_contamination_gate.py" in f for f in failures)
    assert any("--check-wiring" in f for f in failures)


@pytest.mark.unit
def test_wiring_fails_when_ci_summary_stops_needing_the_job() -> None:
    def drop(jobs: dict[str, Any]) -> None:
        jobs["ci-summary"]["needs"] = [
            n
            for n in jobs["ci-summary"]["needs"]
            if n != "yamlfmt-contamination-ratchet"
        ]

    failures = _wiring_failures(drop)
    assert any("in `needs:`" in f for f in failures)


@pytest.mark.unit
def test_wiring_fails_when_the_strict_success_only_check_is_removed() -> None:
    """A plain ``needs:`` entry is not enforcement.

    ci-summary's generic ``contains(needs.*.result, 'failure')`` rollup passes
    on a SKIPPED need, so a skipped ratchet job would report green.
    """

    def strip(jobs: dict[str, Any]) -> None:
        for step in jobs["ci-summary"]["steps"]:
            if isinstance(step.get("run"), str):
                step["run"] = step["run"].replace(
                    'needs.yamlfmt-contamination-ratchet.result }}" != "success"',
                    'needs.some-other-job.result }}" != "success"',
                )

    failures = _wiring_failures(strip)
    assert any("strict success-only check" in f for f in failures)


@pytest.mark.unit
def test_wiring_fails_loudly_on_a_missing_workflow_file(tmp_path: Path) -> None:
    with pytest.raises(gate.CiWorkflowUnreadableError):
        gate.check_wiring(tmp_path / "no-such-ci.yml")


# ---------------------------------------------------------------------------
# The hook actually fires (OMN-15070: a files: regex matching nothing is
# byte-indistinguishable from a passing hook)
# ---------------------------------------------------------------------------


def _hooks_in_order() -> list[tuple[str, str]]:
    """``(repo, hook id)`` in declared execution order."""
    config = yaml.safe_load(_PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    return [
        (repo.get("repo", ""), hook["id"])
        for repo in config["repos"]
        for hook in repo.get("hooks", [])
    ]


def _hook(hook_id: str) -> dict[str, Any]:
    config = yaml.safe_load(_PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook["id"] == hook_id:
                assert isinstance(hook, dict)
                return hook
    pytest.fail(f"hook {hook_id} is not declared in .pre-commit-config.yaml")


@pytest.mark.unit
def test_hook_files_regex_matches_real_corpus_paths() -> None:
    """Compiled against paths taken from the live baseline, not invented ones."""
    pattern = re.compile(_hook(_HOOK_ID)["files"])
    baseline = gate.load_baseline(_REPO_ROOT / gate.SENTINEL_BASELINE_REL)
    receipts = [p for p in baseline if p.startswith("drift/dod_receipts/")]
    contracts = [p for p in baseline if p.startswith("contracts/")]
    assert receipts, "baseline must cover drift/dod_receipts/"
    assert contracts, "baseline must cover contracts/"
    for real_path in (*sorted(receipts)[:5], *sorted(contracts)[:5]):
        assert pattern.search(real_path), f"hook would never fire on {real_path}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "out_of_scope",
    [
        "src/onex_change_control/models/model_x.py",
        "docs/runbooks/thing.yaml",
        ".onex_ratchets/omn_15479_folded_scalar_baseline.yaml",
        ".github/workflows/ci.yml",
    ],
)
def test_hook_files_regex_does_not_over_match(out_of_scope: str) -> None:
    assert not re.compile(_hook(_HOOK_ID)["files"]).search(out_of_scope)


@pytest.mark.unit
def test_wiring_hook_is_scoped_to_ci_yaml() -> None:
    pattern = re.compile(_hook(_WIRING_HOOK_ID)["files"])
    assert pattern.search(".github/workflows/ci.yml")
    assert not pattern.search(".github/workflows/deploy-gate.yml")


@pytest.mark.unit
def test_contamination_hook_runs_before_yamlfmt() -> None:
    """Order is the whole point of the pre-commit half of this gate.

    yamlfmt's corruption is a fixed point -- a second pass is a no-op, so it
    never self-heals. If this gate ran after yamlfmt, the author's original text
    would already be destroyed in the working tree by the time the gate
    objected. Running first rejects the precondition while the original still
    exists.
    """
    order = _hooks_in_order()
    ids = [hook_id for _repo, hook_id in order]
    assert _HOOK_ID in ids, "the contamination hook is not installed at all"
    assert "yamlfmt" in ids, "yamlfmt hook disappeared; re-derive this ordering"
    assert ids.index(_HOOK_ID) < ids.index("yamlfmt"), (
        "check-yamlfmt-contamination must be declared BEFORE the yamlfmt hook. "
        f"Declared order: {ids[:6]}"
    )


@pytest.mark.unit
def test_hooks_do_not_auto_fix() -> None:
    """A gate that mutates files to fix formatter corruption repeats the defect."""
    for hook_id in (_HOOK_ID, _WIRING_HOOK_ID):
        entry = _hook(hook_id)["entry"]
        assert "--fix" not in entry
        assert "--write" not in entry
        assert "check_yamlfmt_contamination.py" in entry


@pytest.mark.unit
def test_scripts_exception_is_registered_for_the_gate() -> None:
    """scripts/** is DEFAULT-DENY (OMN-14475); an unregistered gate cannot land."""
    registry = yaml.safe_load(
        (_REPO_ROOT / "allowlists" / "scripts_exceptions.yaml").read_text(
            encoding="utf-8"
        )
    )
    entries = registry["entries"]
    paths = {entry.get("path") for entry in entries if isinstance(entry, dict)}
    assert "scripts/validation/check_yamlfmt_contamination.py" in paths
