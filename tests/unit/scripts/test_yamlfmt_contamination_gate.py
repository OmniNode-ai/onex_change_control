# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15479: RED/GREEN controls for the yamlfmt contamination ratchet.

These tests prove the gate FIRES on each defect vector rather than asserting
that it exists. Five groups:

* **Detector controls.** The two proven-safe shapes (literal block scalars,
  single-paragraph folded scalars) must stay green, and the two defect shapes
  (the sentinel in a parsed value, a folded scalar carrying an internal
  newline) must go red. A detector that flagged the safe shapes would be a
  7,286-file false-positive wall and would be ignored within a day. Two
  adversarial encodings that assemble the marker at parse time -- a `\\x23`
  hex escape and a `\\` line continuation -- are held RED so the parsed-value
  contract in the gate's docstring stays true of its code.
* **Scope.** The gate's file set is derived from the yamlfmt hook's own
  `files`/`exclude`, never restated. Proven by moving the formatter's
  declaration and watching scope follow it, by failing closed on every
  unresolvable declaration, and by an identify-vs-suffix equivalence check over
  every tracked path.
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

import ast
import copy
import importlib.util
import re
import subprocess
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
def _real_scope() -> Any:
    return gate.load_yamlfmt_scope(_REPO_ROOT)


@lru_cache(maxsize=1)
def _live_corpus() -> tuple[dict[str, int], dict[str, int]]:
    """Scan the whole corpus once; four ratchet tests consume the same scan."""
    scanned: tuple[dict[str, int], dict[str, int]] = gate.scan_corpus(
        _REPO_ROOT, _real_scope()
    )
    return scanned


@lru_cache(maxsize=1)
def _tracked_paths() -> tuple[str, ...]:
    """Every tracked + untracked-not-ignored path, exactly as the gate sees it."""
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(_REPO_ROOT),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return tuple(completed.stdout.splitlines())


# The scope the gate was hardcoded to before 2026-07-30. Kept as a live control:
# `test_widened_scope_catches_what_the_hardcoded_prefixes_missed` proves each of
# the newly-admitted baseline entries was invisible under it.
_PRE_WIDENING_PREFIXES = ("contracts/", "drift/dod_receipts/")


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
    widened corpus: raw bytes find 521 files / 808 occurrences, parsed values
    find 518 / 805, and all three raw-only files are this gate's own
    documentation (the two baselines and the pre-commit config comment). The
    parsed-only set is empty, so the precision costs no coverage.
    """
    text = f"# this gate rejects {SENTINEL} in a value\nactual_output: 'clean text'\n"
    assert gate.count_sentinel_in_values(text) == 0


@pytest.mark.unit
def test_double_quoted_hex_escape_encoding_is_detected() -> None:
    """D3 vector 1: the marker is assembled by the parser, never by the bytes.

    A double-quoted YAML scalar resolves `\\x23` to `#`, so the sentinel exists
    in the parsed value while the raw file does not contain it anywhere. The
    original gate short-circuited on a raw-byte `SENTINEL not in text` check and
    returned 0 here -- parsed-value detection in the docstring, raw-byte
    detection in the code.
    """
    text = 'actual_output: "before \\x23magic___^_^___line after"\n'
    assert SENTINEL not in text, "the raw bytes must NOT spell the sentinel"
    assert yaml.safe_load(text)["actual_output"].count(SENTINEL) == 1
    assert gate.count_sentinel_in_values(text) == 1


@pytest.mark.unit
def test_line_continuation_split_encoding_is_detected() -> None:
    """D3 vector 2: a `\\`-continuation splits the marker across source lines.

    Double-quoted scalars fold a backslash-terminated line into the next with no
    separator, so the value is contiguous even though no single raw line holds
    the whole token.
    """
    text = 'actual_output: "before #magic___^_^_\\\n  __line after"\n'
    assert SENTINEL not in text, "the raw bytes must NOT spell the sentinel"
    assert yaml.safe_load(text)["actual_output"].count(SENTINEL) == 1
    assert gate.count_sentinel_in_values(text) == 1


@pytest.mark.unit
def test_detection_is_not_gated_on_a_raw_byte_prefilter() -> None:
    """The general property, not the two specific encodings.

    Stated as a source-level assertion as well as a behavioural one: a future
    performance patch that reinstates a raw-byte fast path reopens both vectors
    above, and the behavioural tests alone would not say why.

    Asserted over the function's EXECUTABLE body with its docstring stripped --
    the docstring names the removed short-circuit verbatim, so a raw text search
    would match the prose that explains the fix and be permanently red.
    """
    module = ast.parse(
        (
            _REPO_ROOT / "scripts" / "validation" / "check_yamlfmt_contamination.py"
        ).read_text(encoding="utf-8")
    )
    functions = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "count_sentinel_in_values"
    ]
    assert len(functions) == 1, "count_sentinel_in_values is the Rule S entrypoint"
    body = list(functions[0].body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)
    source = "\n".join(ast.unparse(stmt) for stmt in body)
    # Non-vacuity: the stripped body is real code, not an empty stub.
    assert "yaml.safe_load_all" in source
    assert "if SENTINEL not in text" not in source, (
        "a raw-byte short-circuit was reinstated in the Rule S path; it makes "
        "the parsed-value contract false again (see the two encoding tests "
        'above). Rule F\'s `">" not in text` check is the sound one -- a folded '
        "scalar needs a literal indicator byte."
    )


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
# Scope -- derived from the yamlfmt hook, provably not restated
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("contracts/OMN-15479.yaml", True),
        ("drift/dod_receipts/OMN-11599/dod-x/command.yaml", True),
        ("drift/dod_receipts/OMN-1/d/command.supersede.0001.yaml", True),
        ("contracts/nested/thing.yml", True),
        # Formatter-exposed and previously ungated -- the D1 gap.
        ("src/onex_change_control/handlers/contract.yaml", True),
        ("schemas/dogfood_scorecard.yaml", True),
        ("tests/fixtures/evidence_admissibility_cases.yaml", True),
        ("workflows/stale-todo-gate.yml", True),
        ("docs/runbooks/thing.yaml", True),
        (".onex_ratchets/omn_15479_yamlfmt_sentinel_baseline.yaml", True),
        (".pre-commit-config.yaml", True),
        # Under yamlfmt's own exclude, therefore out of scope.
        (".github/workflows/ci.yml", False),
        ("templates/contract.template.yaml", False),
        # Not YAML at all.
        ("src/onex_change_control/models/model_x.py", False),
        ("contracts/README.md", False),
    ],
)
def test_scope_predicate(rel: str, *, expected: bool) -> None:
    assert gate.is_in_scope(rel, _real_scope()) is expected


def _scope_from_config(tmp_path: Path, hook_yaml: str) -> Any:
    (tmp_path / gate.PRE_COMMIT_CONFIG_REL).write_text(hook_yaml, encoding="utf-8")
    return gate.load_yamlfmt_scope(tmp_path)


_YAMLFMT_REPO_HEADER = (
    "repos:\n"
    "  - repo: https://github.com/google/yamlfmt\n"
    "    rev: v0.21.0\n"
    "    hooks:\n"
    "      - id: yamlfmt\n"
)


@pytest.mark.unit
def test_scope_follows_the_yamlfmt_exclude_rather_than_a_hardcoded_list(
    tmp_path: Path,
) -> None:
    """The anti-drift property, proven by moving the formatter's declaration.

    This is the whole mechanism behind D1. If the gate restated its own scope,
    editing the yamlfmt exclude would silently un-gate files. Here the same
    path flips in and out of scope purely from the config edit.
    """
    wide = _scope_from_config(tmp_path, _YAMLFMT_REPO_HEADER)
    assert gate.is_in_scope("src/a/contract.yaml", wide) is True
    assert gate.is_in_scope(".github/workflows/ci.yml", wide) is True

    narrowed = _scope_from_config(
        tmp_path, _YAMLFMT_REPO_HEADER + "        exclude: ^src/\n"
    )
    assert gate.is_in_scope("src/a/contract.yaml", narrowed) is False
    assert gate.is_in_scope("contracts/OMN-1.yaml", narrowed) is True

    with_files = _scope_from_config(
        tmp_path, _YAMLFMT_REPO_HEADER + "        files: ^contracts/\n"
    )
    assert gate.is_in_scope("src/a/contract.yaml", with_files) is False
    assert gate.is_in_scope("contracts/OMN-1.yaml", with_files) is True


@pytest.mark.unit
def test_repo_level_filters_are_honoured(tmp_path: Path) -> None:
    """pre-commit applies top-level files/exclude to every hook; so must this."""
    scope = _scope_from_config(tmp_path, "exclude: ^vendor/\n" + _YAMLFMT_REPO_HEADER)
    assert gate.is_in_scope("vendor/thing.yaml", scope) is False
    assert gate.is_in_scope("contracts/OMN-1.yaml", scope) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "because"),
    [
        ("repos: []\n", "yamlfmt hook removed entirely"),
        (
            "repos:\n  - repo: local\n    hooks:\n      - id: yamlfmt\n",
            "hook id kept but moved off the google/yamlfmt repo",
        ),
        (
            _YAMLFMT_REPO_HEADER + _YAMLFMT_REPO_HEADER.replace("repos:\n", ""),
            "two yamlfmt hooks -- union of scopes is ambiguous",
        ),
        (
            _YAMLFMT_REPO_HEADER + "        types: [yaml, json]\n",
            "types overridden away from the suffix test's assumption",
        ),
        (
            _YAMLFMT_REPO_HEADER + "        exclude: '['\n",
            "exclude is not a compilable regex",
        ),
        ("not_repos: true\n", "config has no repos list"),
    ],
)
def test_scope_resolution_fails_closed(tmp_path: Path, body: str, because: str) -> None:
    """Every unresolvable scope raises; none degrades to a narrower guess.

    A gate that quietly scans nothing reports PASSED, which is the failure this
    whole ticket exists to stop (OMN-14666/14668).
    """
    (tmp_path / gate.PRE_COMMIT_CONFIG_REL).write_text(body, encoding="utf-8")
    try:
        scope = gate.load_yamlfmt_scope(tmp_path)
    except gate.YamlfmtScopeError:
        return
    pytest.fail(
        f"scope resolution silently succeeded ({scope!r}) when it should have "
        f"failed closed: {because}"
    )


@pytest.mark.unit
def test_scope_resolution_fails_closed_on_a_missing_config(tmp_path: Path) -> None:
    with pytest.raises(gate.YamlfmtScopeError):
        gate.load_yamlfmt_scope(tmp_path)


@pytest.mark.unit
def test_suffix_filter_equals_identify_yaml_tag() -> None:
    """The suffix test stands in for pre-commit's `types: [yaml]`; prove it.

    pre-commit selects yamlfmt's files with identify's `yaml` tag, not with a
    suffix match. If those two ever disagree on a real path, this gate's scope
    silently stops being the formatter's scope. Checked against every tracked
    and untracked file in the repo, not a sample.
    """
    from identify import identify as identify_mod

    disagreements = []
    for rel in _tracked_paths():
        path = _REPO_ROOT / rel
        if not path.is_file():
            continue
        try:
            tagged = "yaml" in identify_mod.tags_from_path(str(path))
        except (OSError, ValueError):  # pragma: no cover - unreadable path
            continue
        if tagged is not rel.endswith(gate.YAML_SUFFIXES):
            disagreements.append(rel)
    assert not disagreements, (
        "identify's `yaml` tag and the gate's suffix test disagree on "
        f"{len(disagreements)} path(s): {disagreements[:10]}. pre-commit hands "
        "yamlfmt the identify set, so the gate is now scanning a different set "
        "than the formatter formats."
    )


@pytest.mark.unit
def test_widened_scope_catches_what_the_hardcoded_prefixes_missed() -> None:
    """RED-before/GREEN-after for D1, held permanently as a control.

    Every path the widening admitted is asserted (a) outside the old hardcoded
    prefixes -- i.e. genuinely invisible to the previous gate -- and (b) in
    scope now. If someone re-narrows the scope to the old prefixes, this test
    goes red instead of the coverage silently disappearing.
    """
    baselines = [
        gate.load_baseline(_REPO_ROOT / gate.SENTINEL_BASELINE_REL),
        gate.load_baseline(_REPO_ROOT / gate.FOLDED_BASELINE_REL),
    ]
    newly = sorted(
        {
            rel
            for baseline in baselines
            for rel in baseline
            if not rel.startswith(_PRE_WIDENING_PREFIXES)
        }
    )
    assert len(newly) >= 9, (
        "the baselines no longer hold any entry outside contracts/ + "
        f"drift/dod_receipts/ (found {newly}). Either the scope was re-narrowed "
        "or the widened-scope debt was dropped without being repaired."
    )
    for rel in newly:
        assert not rel.startswith(_PRE_WIDENING_PREFIXES)
        assert gate.is_in_scope(rel, _real_scope()), rel
        assert (_REPO_ROOT / rel).is_file(), rel

    # The five live node/module contract.yaml descriptions are the reason this
    # gap mattered: the corruption had reached shipped metadata, not evidence.
    assert sum(1 for rel in newly if rel.endswith("/contract.yaml")) >= 5


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
    # 518/15 as of the 2026-07-30 scope widening (510/14 plus exactly the
    # pre-existing debt the hardcoded prefixes could not see). Ceilings only --
    # these may fall as the debt is repaired, never rise.
    assert len(sentinel_baseline) <= 518
    assert len(folded_baseline) <= 15
    assert all(rel.endswith((".yaml", ".yml")) for rel in sentinel_baseline)
    assert all(gate.is_in_scope(rel, _real_scope()) for rel in sentinel_baseline)
    assert all(gate.is_in_scope(rel, _real_scope()) for rel in folded_baseline)


@pytest.mark.unit
def test_corpus_mode_is_green_on_the_real_tree() -> None:
    """The 518 baselined files must NOT fail; a permanently-red gate is not a
    gate, it is an outage.
    """
    assert gate.check_corpus(_REPO_ROOT) == []


# ---------------------------------------------------------------------------
# Per-file (pre-commit) mode
# ---------------------------------------------------------------------------


def _fake_repo(tmp_path: Path, sentinel_base: str = "", folded_base: str = "") -> Path:
    ratchets = tmp_path / ".onex_ratchets"
    ratchets.mkdir()
    # Per-file mode resolves scope from the yamlfmt hook like every other mode;
    # a fixture repo without one must (and does) fail closed, so give it one.
    (tmp_path / gate.PRE_COMMIT_CONFIG_REL).write_text(
        _YAMLFMT_REPO_HEADER + "        exclude: ^(\\.github/|templates/)\n",
        encoding="utf-8",
    )
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
def test_wiring_fails_when_ci_summary_declares_needs() -> None:
    """OMN-15768: ci-summary must be the no-needs poller; a `needs:` here is
    a REGRESSION to the needs-graph-omission bug class (OCC#6346), the
    opposite of what this anchor used to require."""

    def add_needs(jobs: dict[str, Any]) -> None:
        jobs["ci-summary"]["needs"] = ["yamlfmt-contamination-ratchet"]

    failures = _wiring_failures(add_needs)
    assert any("declares `needs:`" in f for f in failures)


_GATE_MODULE = _REPO_ROOT / "scripts" / "ci" / "ci_summary_gate.py"


@pytest.mark.unit
def test_wiring_fails_when_job_not_registered_in_strict_gate_jobs(
    tmp_path: Path,
) -> None:
    """A skipped ratchet job that is NOT registered in STRICT_GATE_JOBS is
    invisible to the poller's explicit strict check (the generic default-deny
    sweep alone is not sufficient proof of intent)."""
    mutated = _GATE_MODULE.read_text(encoding="utf-8").replace(
        '"yamlfmt Contamination Ratchet (OMN-15479)",\n', ""
    )
    assert mutated != _GATE_MODULE.read_text(encoding="utf-8")
    gate_path = tmp_path / "ci_summary_gate.py"
    gate_path.write_text(mutated, encoding="utf-8")
    failures = gate.check_wiring(_CI_YAML, gate_module_path=gate_path)
    assert any("STRICT_GATE_JOBS" in f for f in failures)


@pytest.mark.unit
def test_wiring_passes_when_job_registered_in_strict_gate_jobs(
    tmp_path: Path,
) -> None:
    """GREEN-after control for the STRICT_GATE_JOBS registration check."""
    gate_path = tmp_path / "ci_summary_gate.py"
    gate_path.write_text(_GATE_MODULE.read_text(encoding="utf-8"), encoding="utf-8")
    assert gate.check_wiring(_CI_YAML, gate_module_path=gate_path) == []


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
def test_hook_files_regex_reaches_every_in_scope_path() -> None:
    """The hook must never be NARROWER than the script's derived scope.

    pre-commit filters filenames before the script ever sees them, so a hook
    regex tighter than the yamlfmt scope silently re-creates the D1 gap on the
    local half of the gate. Compiled against real baselined paths -- including
    the ones the widening admitted -- not invented ones.
    """
    pattern = re.compile(_hook(_HOOK_ID)["files"])
    baseline = gate.load_baseline(_REPO_ROOT / gate.SENTINEL_BASELINE_REL)
    receipts = [p for p in baseline if p.startswith("drift/dod_receipts/")]
    contracts = [p for p in baseline if p.startswith("contracts/")]
    widened = [p for p in baseline if not p.startswith(_PRE_WIDENING_PREFIXES)]
    assert receipts, "baseline must cover drift/dod_receipts/"
    assert contracts, "baseline must cover contracts/"
    assert widened, "baseline must cover the formatter-exposed files outside them"
    for real_path in (
        *sorted(receipts)[:5],
        *sorted(contracts)[:5],
        *sorted(widened),
    ):
        assert pattern.search(real_path), f"hook would never fire on {real_path}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "non_yaml",
    [
        "src/onex_change_control/models/model_x.py",
        "docs/RECEIPT_LOCATIONS.md",
        "pyproject.toml",
    ],
)
def test_hook_files_regex_does_not_over_match(non_yaml: str) -> None:
    """Non-YAML never reaches the gate.

    The hook deliberately matches every `.yaml`/`.yml` path and lets the script
    apply the yamlfmt-derived exclude, so that scope lives in exactly one place.
    Out-of-scope YAML (`.github/`, `templates/`) is skipped by the script with a
    NOTE rather than by a second regex that could drift from the first.
    """
    assert not re.compile(_hook(_HOOK_ID)["files"]).search(non_yaml)


@pytest.mark.unit
def test_hook_scope_is_delegated_to_the_script_not_duplicated() -> None:
    excluded_yaml = ".github/workflows/ci.yml"
    assert re.compile(_hook(_HOOK_ID)["files"]).search(excluded_yaml)
    assert gate.is_in_scope(excluded_yaml, _real_scope()) is False


@pytest.mark.unit
def test_scanning_hook_declares_fail_fast() -> None:
    """D2: ordering alone does NOT protect the working tree; fail_fast does.

    With the repo-level `fail_fast: false`, pre-commit runs the remaining hooks
    after an earlier one fails -- so yamlfmt executed in the same `git commit`
    and rewrote the file the gate had just rejected. Declaring `fail_fast: true`
    on the scanning hook stops the run at the failure, which is the only reason
    the author's original text is still on disk to repair. Order still matters:
    fail_fast only helps if this hook is reached first, which
    `test_contamination_hook_runs_before_yamlfmt` holds.
    """
    assert _hook(_HOOK_ID).get("fail_fast") is True, (
        "check-yamlfmt-contamination must declare fail_fast: true. Without it "
        "the yamlfmt hook still runs in the same invocation and corrupts the "
        "working tree even though this gate already rejected the file."
    )
    config = yaml.safe_load(_PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    assert config.get("fail_fast") is not True, (
        "this test is only meaningful while the repo-level fail_fast is off; if "
        "it were on, the per-hook declaration would be redundant and the "
        "rationale comment above the hook needs rewriting."
    )


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
