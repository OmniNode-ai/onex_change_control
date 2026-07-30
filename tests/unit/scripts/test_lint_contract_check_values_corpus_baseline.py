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

import re
import sys
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import lint_contract_check_values as linter  # noqa: E402

_RULE_A_BASELINE_PATH = _REPO_ROOT / ".onex_ratchets" / "omn_15382_rule_a_baseline.yaml"
_RULE_B_BASELINE_PATH = _REPO_ROOT / ".onex_ratchets" / "omn_15382_rule_b_baseline.yaml"
_RULE_C_BASELINE_PATH = _REPO_ROOT / ".onex_ratchets" / "omn_15391_rule_c_baseline.yaml"
_RULE_D_BASELINE_PATH = _REPO_ROOT / ".onex_ratchets" / "omn_15391_rule_d_baseline.yaml"
_RULE_E_BASELINE_PATH = _REPO_ROOT / ".onex_ratchets" / "omn_15411_rule_e_baseline.yaml"

# OMN-15411 Rule E generated-item carve-out. The live OCC companion producer
# mints `dod-deploy-assessment` with
# `gh pr diff ${PR_NUMBER} --repo ${REPO} --name-only | grep -qiE '...'` on
# EVERY companion contract, and older producer revisions minted the same shape
# in `dod-<repo>-pr-<n>-ci` and `*self-bind-pr-<n>` items. Those ids are
# authored by a machine, not by a human or an agent editing a contract, so a
# consumer-side ratchet cannot stop them entering -- it can only hard-fail
# every future autobind PR. They are excluded from the ratchet and tracked for
# repair at the producer (omnimarket node_occ_companion_compute).
_GENERATED_SIGPIPE_ITEM_RE = re.compile(
    r"^(?:dod-deploy-assessment|dod-[A-Za-z].*-pr-\d+-ci|.*self-bind-pr-\d+)$"
)


def _is_generated_sigpipe_item(dod_id: str) -> bool:
    return bool(_GENERATED_SIGPIPE_ITEM_RE.match(dod_id))


def _load_baseline(path: Path) -> frozenset[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("baseline", []) or []
    return frozenset(str(e) for e in entries)


@lru_cache(maxsize=1)
def _scan_corpus() -> tuple[
    frozenset[str], frozenset[str], frozenset[str], frozenset[str]
]:
    """Return (rule_a, rule_b, rule_c, rule_d) ids for every contracts/*.yaml.

    Cached: the corpus is ~7.5k contracts and four ratchet tests consume the
    same scan. Without the cache each test re-parses the whole corpus.

    Each id is ``"<relative-contract-path>::<dod_id>"`` -- the same shape the
    baseline files use, and the same shape OMN-15382's corpus survey used to
    generate them.
    """
    contracts_dir = _REPO_ROOT / "contracts"
    rule_a: set[str] = set()
    rule_b: set[str] = set()
    rule_c: set[str] = set()
    rule_d: set[str] = set()

    for path in sorted(contracts_dir.glob("*.yaml")):
        rel = f"contracts/{path.name}"
        findings = linter.lint_contract(path)
        for _path_str, label, fragment in findings:
            dod_id = label.split(":", 1)[0]
            if "executable-command-shape" in label:
                rule_a.add(f"{rel}::{dod_id}")
            elif "pr-binding" in label:
                # fragment carries the dod_id for pr-binding findings.
                rule_b.add(f"{rel}::{fragment}")
            elif "tautological-self-comparison" in label:
                rule_c.add(f"{rel}::{dod_id}")
            elif "fail-open-zero-count" in label:
                rule_d.add(f"{rel}::{dod_id}")

    return frozenset(rule_a), frozenset(rule_b), frozenset(rule_c), frozenset(rule_d)


@lru_cache(maxsize=1)
def _scan_corpus_rule_e() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return (tier1_ratcheted, tier1_generated, tier2_advisory) Rule E ids.

    Rule E findings come from ``lint_contract_warnings`` rather than
    ``lint_contract``: they are warning tier and must never contribute to the
    linter's exit code (see the Rule E block in the linter). The corpus ratchet
    below is where the class is actually stopped from growing.
    """
    contracts_dir = _REPO_ROOT / "contracts"
    tier1_ratcheted: set[str] = set()
    tier1_generated: set[str] = set()
    tier2: set[str] = set()

    for path in sorted(contracts_dir.glob("*.yaml")):
        rel = f"contracts/{path.name}"
        for _path_str, label, _fragment in linter.lint_contract_warnings(path):
            dod_id = label.split(":", 1)[0]
            entry = f"{rel}::{dod_id}"
            if "sigpipe-fragile" in label:
                if _is_generated_sigpipe_item(dod_id):
                    tier1_generated.add(entry)
                else:
                    tier1_ratcheted.add(entry)
            elif "sigpipe-possible" in label:
                tier2.add(entry)

    return frozenset(tier1_ratcheted), frozenset(tier1_generated), frozenset(tier2)


_PRODUCER_LABEL_RE = re.compile(r"unbounded producer \(([^)]+)\)")


@lru_cache(maxsize=1)
def _scan_corpus_rule_e_buckets() -> tuple[
    dict[str, tuple[int, int]], dict[str, tuple[int, int]]
]:
    """Return ({bucket: (items, check_values)}, same-for-generated).

    The per-bucket split is what a repair lane batches on, so the numbers get
    quoted in ticket comments and PR bodies -- and get quoted wrong. This
    derives them from the shipped detector so the assertion below is a
    measurement, not a restatement.
    """
    contracts_dir = _REPO_ROOT / "contracts"
    ratcheted: dict[str, set[str]] = {}
    ratcheted_checks: dict[str, int] = {}
    generated: dict[str, set[str]] = {}
    generated_checks: dict[str, int] = {}

    for path in sorted(contracts_dir.glob("*.yaml")):
        rel = f"contracts/{path.name}"
        for _path_str, label, _fragment in linter.lint_contract_warnings(path):
            if "sigpipe-fragile" not in label:
                continue
            match = _PRODUCER_LABEL_RE.search(label)
            if match is None:  # pragma: no cover - detector always labels
                continue
            bucket = match.group(1)
            dod_id = label.split(":", 1)[0]
            entry = f"{rel}::{dod_id}"
            if _is_generated_sigpipe_item(dod_id):
                generated.setdefault(bucket, set()).add(entry)
                generated_checks[bucket] = generated_checks.get(bucket, 0) + 1
            else:
                ratcheted.setdefault(bucket, set()).add(entry)
                ratcheted_checks[bucket] = ratcheted_checks.get(bucket, 0) + 1

    return (
        {b: (len(v), ratcheted_checks[b]) for b, v in ratcheted.items()},
        {b: (len(v), generated_checks[b]) for b, v in generated.items()},
    )


@pytest.mark.unit
def test_rule_a_corpus_matches_frozen_baseline_exactly() -> None:
    baseline = _load_baseline(_RULE_A_BASELINE_PATH)
    live_a, _live_b, _live_c, _live_d = _scan_corpus()

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
    _live_a, live_b, _live_c, _live_d = _scan_corpus()

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


# ---------------------------------------------------------------------------
# OMN-15391 Rule C / Rule D corpus ratchets.
#
# These two rules were added in OMN-15391 round 2, after a review of OCC#5481
# found that PR had itself introduced 8 Rule C violations (tautological
# `N == N` PR-existence stamps) and 12 Rule D violations (absence legs that
# pass GREEN without reading anything). Round 2 repairs all of those plus the
# 2 in contracts/OMN-15391.yaml and 1 in contracts/OMN-15376.yaml.
#
# The two baselines end up in very different states, and both are honest:
#
#   Rule C -> EMPTY. The only pure tautologies in the whole corpus were the 8
#   OCC#5481 created, and all 8 are retired. (A looser first draft reported 32;
#   24 were false positives on the `occ-self-bind-pr-<n>` idiom, which ANDs the
#   identity comparison with falsifiable `.state`/`.headRefName` predicates.
#   The rule was narrowed and a negative control pins it.)
#
#   Rule D -> 11 entries, all `dod-00N`-era items in contracts this PR does not
#   otherwise touch. Each needs its own anchor chosen against the real file at
#   the real ref, which is per-item research rather than a mechanical rewrite.
#
# The corpus scan is the enforcement surface that matters: the pre-commit hook
# only sees CHANGED files, so a rule wired there alone would never have caught
# the 23 checks OCC#5481 appended to contracts CI never resolved.
#
# CORRECTION (OMN-15411 round 2). A previous revision of this comment claimed
# these tests "run under pytest over EVERY contract, on every PR". That was
# FALSE on the dev merge path, and it is the reason this module's ratchets had
# no merge-blocking force for months:
#
#   * ci.yml's `test` job carries
#     `if: ... (github.event_name != 'pull_request' || github.base_ref != 'dev')`
#     -- it is SKIPPED on every PR targeting `dev`, and ci-summary's generic
#     `contains(needs.*.result, 'failure')` rollup passes on a skipped need.
#   * The only other runner was `tests+coverage (shadow)` in
#     product-readiness-shadow.yml, which that file's own comment describes as
#     "deliberately kept OUT of `required_status_checks`".
#   * OCC dev's required contexts are exactly
#     ["CI Summary", "required-check-skip-guard / check-skip-vectors"].
#
# They DO now, via the unconditional `contract-corpus-ratchets` job in ci.yml,
# which is in ci-summary's `needs:` AND has a strict success-only check there
# (a plain `needs:` entry is not enough -- the generic rollup tolerates
# `skipped`). scripts/validation/check_corpus_ratchet_wiring.py is the
# anti-removal anchor, wired as a pre-commit hook on ci.yml and re-run inside
# the job itself.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rule_c_corpus_matches_frozen_baseline_exactly() -> None:
    baseline = _load_baseline(_RULE_C_BASELINE_PATH)
    _a, _b, live_c, _d = _scan_corpus()

    new_violations = live_c - baseline
    healed = baseline - live_c

    assert not new_violations, (
        f"{len(new_violations)} NEW Rule C (tautological self-comparison) "
        "violation(s) found that are not in the frozen shrink-only baseline "
        f"({_RULE_C_BASELINE_PATH}): {sorted(new_violations)[:20]}. A check "
        "that hands PR #N to the API and then asserts the result equals N is "
        "an `N == N` tautology -- it is true by construction for every PR "
        "that exists and cannot go RED for any product reason. If no "
        "admissible binding check exists for the claim (see "
        "tests/unit/test_occ_self_bind_unprovable_on_record_omn15391.py), "
        "record it as unprovable-on-record instead of shipping a vacuous "
        "command with status: verified."
    )
    assert not healed, (
        f"{len(healed)} baseline entr{'y is' if len(healed) == 1 else 'ies are'} "
        "no longer reproduced by a live corpus scan, but the baseline file "
        f"({_RULE_C_BASELINE_PATH}) was not updated to remove them: "
        f"{sorted(healed)[:20]}. Shrink the baseline when you repair a "
        "contract -- do not leave stale entries."
    )


@pytest.mark.unit
def test_rule_d_corpus_matches_frozen_baseline_exactly() -> None:
    baseline = _load_baseline(_RULE_D_BASELINE_PATH)
    _a, _b, _c, live_d = _scan_corpus()

    new_violations = live_d - baseline
    healed = baseline - live_d

    assert not new_violations, (
        f"{len(new_violations)} NEW Rule D (fail-open zero-count pipe) "
        "violation(s) found that are not in the frozen shrink-only baseline "
        f"({_RULE_D_BASELINE_PATH}): {sorted(new_violations)[:20]}. Absence "
        "asserted as `... | grep -c 'X' | grep -qx 0` passes GREEN when the "
        "producer fails, because check_values run under `sh -c` without "
        "pipefail. Read once into a variable, prove the read with a positive "
        "anchor, then assert absence with `! ... grep -qF`."
    )
    assert not healed, (
        f"{len(healed)} baseline entr{'y is' if len(healed) == 1 else 'ies are'} "
        "no longer reproduced by a live corpus scan, but the baseline file "
        f"({_RULE_D_BASELINE_PATH}) was not updated to remove them: "
        f"{sorted(healed)[:20]}. Shrink the baseline when you repair a "
        "contract -- do not leave stale entries."
    )


@pytest.mark.unit
def test_rule_e_corpus_matches_frozen_baseline_exactly() -> None:
    """Tier-1 SIGPIPE-fragile instances must match the frozen baseline exactly.

    This is the assertion that stops the class growing. The linter itself only
    WARNS on Rule E (per OMN-15411: a SIGPIPE false RED fails closed -- it
    blocks a Done flip, it never passes something that should fail -- so it does
    not warrant failing a pre-commit hook on an unrelated edit). A warning
    nobody must act on does not stop growth, so growth is stopped here instead:
    a new non-generated instance is not in the baseline and hard-fails on CI.
    """
    baseline = _load_baseline(_RULE_E_BASELINE_PATH)
    live, _generated, _tier2 = _scan_corpus_rule_e()

    new_violations = live - baseline
    healed = baseline - live

    assert not new_violations, (
        f"{len(new_violations)} NEW Rule E (sigpipe-fragile early-exit "
        "consumer) instance(s) found that are not in the frozen shrink-only "
        f"baseline ({_RULE_E_BASELINE_PATH}): {sorted(new_violations)[:20]}. "
        "An unbounded producer piped straight into `grep -q` is killed by "
        "SIGPIPE when grep exits at the first match, and the dod_verify "
        "runner's `bash -o pipefail` turns that 141 into a false RED on "
        "evidence that is actually present. Buffer the producer instead: "
        "body=\"$(<producer>)\" && printf '%s' \"$body\" | grep -qF 'MARKER'."
    )
    assert not healed, (
        f"{len(healed)} baseline entr{'y is' if len(healed) == 1 else 'ies are'} "
        "no longer reproduced by a live corpus scan, but the baseline file "
        f"({_RULE_E_BASELINE_PATH}) was not updated to remove them: "
        f"{sorted(healed)[:20]}. Shrink the baseline when you repair a "
        "contract -- do not leave stale entries."
    )


@pytest.mark.unit
def test_rule_e_baseline_holds_no_generated_producer_items() -> None:
    """The generated carve-out must stay OUT of the ratcheted baseline.

    If a `dod-deploy-assessment` / `dod-*-pr-<n>-ci` / `*self-bind-pr-<n>` entry
    ever lands in this baseline, the next producer-authored companion contract
    will carry an instance that is not in the frozen set and every autobind PR
    will hard-fail. The producer emits the shape unconditionally, so the repair
    belongs at the producer (omnimarket node_occ_companion_compute), not here.
    """
    baseline = _load_baseline(_RULE_E_BASELINE_PATH)
    offenders = sorted(
        entry
        for entry in baseline
        if _is_generated_sigpipe_item(entry.split("::", 1)[-1])
    )
    assert not offenders, (
        "Generated-producer item id(s) present in the Rule E baseline: "
        f"{offenders[:20]}. Ratcheting a machine-authored shape wedges every "
        "future autobind PR -- fix the producer instead."
    )


@pytest.mark.unit
def test_rule_e_tier2_advisory_set_is_not_ratcheted() -> None:
    """Tier-2 (volume-dependent) instances must not leak into the baseline.

    `find <one-receipt-dir> -type f | grep -q .` measured 0/5 exits under
    `bash -o pipefail -c` -- it is not exposed, because `find` finishes writing
    before grep reads. Ratcheting the 80-odd instances of that shape would
    freeze non-debt into a debt list and train readers to ignore the ratchet.
    """
    baseline = _load_baseline(_RULE_E_BASELINE_PATH)
    _live, _generated, tier2 = _scan_corpus_rule_e()
    overlap = sorted(baseline & tier2)
    assert not overlap, (
        f"Tier-2 advisory entries leaked into the ratcheted Rule E baseline: "
        f"{overlap[:20]}."
    )


@pytest.mark.unit
def test_omn_15391_round2_repairs_contribute_nothing_to_rule_c_or_d() -> None:
    """Every item this round authored must be clean under both new rules.

    The failure this guards against is the one the round-2 review found in
    OCC#5481: a repair PR that retires old violations while introducing new
    ones of a class no ratchet yet tracked.
    """
    baseline_c = _load_baseline(_RULE_C_BASELINE_PATH)
    baseline_d = _load_baseline(_RULE_D_BASELINE_PATH)
    for entry in (*baseline_c, *baseline_d):
        assert not entry.endswith("-rb2"), (
            f"An OMN-15391 round-2 item is in a violation baseline: {entry}. "
            "Round-2 items must be clean, not baselined."
        )


# OMN-15411 round-2 numeric pin.
#
# PR #5541's body stated "the `gh pr diff ${PR_NUMBER}` bucket (283 checks)".
# That number is wrong, and it is the number a repair lane would batch on. An
# independent scan with the shipped detector measures 143 items / 143
# check_values in that bucket. The builder's own OMN-15411 comment said 143, so
# the PR body contradicted its own evidence.
#
# The remaining-work census is now derived from the detector by this test rather
# than restated in prose, so a future correction lands as a test diff with the
# real numbers attached.
_RULE_E_RATCHETED_CENSUS: dict[str, tuple[int, int]] = {
    "base64-decoded file body": (160, 177),
    "gh pr diff": (143, 143),
    "paginated REST list": (5, 5),
    "git history walk": (4, 4),
}
_RULE_E_GENERATED_CENSUS: dict[str, tuple[int, int]] = {
    "gh pr diff": (47, 47),
    "paginated REST list": (5, 5),
    "iterating jq projection": (4, 4),
    "base64-decoded file body": (2, 3),
}


@pytest.mark.unit
def test_rule_e_per_bucket_census_is_pinned() -> None:
    """The per-bucket remaining-work census matches a live detector scan.

    Shrink-only in spirit like the baselines: repairing a bucket must update
    this dict in the same PR, which is exactly the moment the remaining count
    posted on the ticket should change too.
    """
    ratcheted, generated = _scan_corpus_rule_e_buckets()
    assert ratcheted == _RULE_E_RATCHETED_CENSUS, (
        "Rule E ratcheted per-bucket census drifted from the live scan. "
        f"live={ratcheted} pinned={_RULE_E_RATCHETED_CENSUS}. Update the pin in "
        "the SAME PR that repairs (or adds) instances, and update the "
        "remaining-count comment on OMN-15411 to match."
    )
    assert generated == _RULE_E_GENERATED_CENSUS, (
        "Rule E generated (carved-out) per-bucket census drifted from the live "
        f"scan. live={generated} pinned={_RULE_E_GENERATED_CENSUS}. The "
        "carve-out is producer-side debt tracked at omnimarket "
        "node_occ_companion_compute (OMN-15407); growth here means the producer "
        "minted more instances, not that a human authored them."
    )


@pytest.mark.unit
def test_rule_e_census_totals_agree_with_the_baseline_and_scan() -> None:
    """Cross-foot the per-bucket census against the two set-based scans.

    Guards the arithmetic error class directly: an item can hit more than one
    producer bucket, so per-bucket item counts do NOT sum to the distinct-item
    total, and check_value counts are >= item counts. Asserting the relation
    rather than a hand-added total is what makes the pinned numbers safe to
    quote.
    """
    ratcheted, generated = _scan_corpus_rule_e_buckets()
    live_t1, live_gen, _tier2 = _scan_corpus_rule_e()
    baseline = _load_baseline(_RULE_E_BASELINE_PATH)

    assert len(live_t1) == len(baseline) == 312
    assert len(live_gen) == 58

    for label, census, distinct in (
        ("ratcheted", ratcheted, len(live_t1)),
        ("generated", generated, len(live_gen)),
    ):
        summed_items = sum(items for items, _checks in census.values())
        assert summed_items >= distinct, (
            f"{label}: per-bucket item counts ({summed_items}) cannot be below "
            f"the distinct-item total ({distinct})."
        )
        for bucket, (items, checks) in census.items():
            assert checks >= items, (
                f"{label}/{bucket}: {checks} check_values < {items} items -- "
                "impossible, each item contributes at least one check_value."
            )
