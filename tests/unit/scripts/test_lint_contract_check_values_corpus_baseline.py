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
_RULE_F_BASELINE_PATH = _REPO_ROOT / ".onex_ratchets" / "omn_15540_rule_f_baseline.yaml"

# OMN-15540 Rule F concurrent-repair exemption.
#
# This baseline entry is owned by a lane that was already in flight when Rule F
# landed and that will repair it:
#
#   contracts/OMN-15192.yaml::dod-omn-15192-b3-accepted-deviation-census
#       -> the OMN-15192 acceptance-bullet-3 lane
#
# Rules A-D assert baseline set EQUALITY: a repaired contract whose baseline
# entry was not deleted in the same PR hard-fails as a stale entry. That is the
# right default, and it stays the default for every other Rule F entry. It is
# wrong for this one specifically: the repairing PR belongs to a DIFFERENT lane
# that has no reason to know this baseline exists, so the moment it lands the
# `healed` assertion would go RED on the required `CI Summary` context -- a
# gate turning someone else's REPAIR into a merge block. That is a false-RED
# generator of exactly the kind the Rule E block in the linter warns makes a
# rule something the corpus learns to ignore.
#
# That hazard is not hypothetical here. contracts/OMN-15484.yaml was in the
# original census and was ALSO exempted for the same reason; while Rule F was
# being built its owning lane landed the correct attempt-anchored repair on
# dev, so it left the live scan on its own and is no longer baselined at all.
#
# So the `healed` half tolerates this entry disappearing; the `new violations`
# half stays hard for everything including it. Once the lane lands, delete the
# entry from the baseline AND from this set -- the follow-up is named in
# OMN-15540. This set may only shrink.
#
# THE LANE LANDED (2026-07-30, OCC#5673 / OMN-15192), so the instruction above
# was carried out and this set is now EMPTY. Both OMN-15192 Rule F entries --
# dod-omn-15192-bullet1-first-post-flip-mint and
# dod-omn-15192-b3-accepted-deviation-census -- are superseded append-only by
# `-r34` items that re-ask the identical question over the CLOSED window
# created:2026-07-29T03:01:23Z..2026-07-30T08:00:00Z, and a live corpus rescan
# reproduces neither id. They were deleted from
# .onex_ratchets/omn_15540_rule_f_baseline.yaml in the same PR, which is what
# forced this deletion too: test_rule_f_concurrent_repair_exemptions_are_still_
# baseline_entries requires every exempt id to still be a baseline entry.
# Keeping the exemption while shrinking the baseline is not an option the
# ratchet allows -- by design.
_RULE_F_CONCURRENT_REPAIR_EXEMPT: frozenset[str] = frozenset()

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
    frozenset[str], frozenset[str], frozenset[str], frozenset[str], frozenset[str]
]:
    """Return (rule_a, rule_b, rule_c, rule_d, rule_f) ids for contracts/*.yaml.

    Cached: the corpus is ~7.5k contracts and five ratchet tests consume the
    same scan. Without the cache each test re-parses the whole corpus.

    Rule F rides THIS walk deliberately rather than adding its own. See
    ``test_rule_e_corpus_is_walked_exactly_once_across_all_consumers`` below:
    a second full-corpus walk pushed the contract's own ratchet check past the
    DoD compliance runner's 60s timeout once already.

    Each id is ``"<relative-contract-path>::<dod_id>"`` -- the same shape the
    baseline files use, and the same shape OMN-15382's corpus survey used to
    generate them.
    """
    contracts_dir = _REPO_ROOT / "contracts"
    rule_a: set[str] = set()
    rule_b: set[str] = set()
    rule_c: set[str] = set()
    rule_d: set[str] = set()
    rule_f: set[str] = set()

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
            elif "mutable-state-pin" in label:
                rule_f.add(f"{rel}::{dod_id}")

    return (
        frozenset(rule_a),
        frozenset(rule_b),
        frozenset(rule_c),
        frozenset(rule_d),
        frozenset(rule_f),
    )


_PRODUCER_LABEL_RE = re.compile(r"unbounded producer \(([^)]+)\)")

# Shape: tier1_ratcheted ids, tier1_generated ids, tier2 ids, then the
# per-producer-bucket census for each of the first two, as {bucket: (items, checks)}.
_RuleEScan = tuple[
    frozenset[str],
    frozenset[str],
    frozenset[str],
    dict[str, tuple[int, int]],
    dict[str, tuple[int, int]],
]


@lru_cache(maxsize=1)
def _scan_corpus_rule_e_full() -> _RuleEScan:
    """One pass over every contract producing BOTH the id sets and the buckets.

    Rule E findings come from ``lint_contract_warnings`` rather than
    ``lint_contract``: they are warning tier and must never contribute to the
    linter's exit code (see the Rule E block in the linter). The corpus ratchet
    below is where the class is actually stopped from growing.

    SINGLE PASS, deliberately. A first draft added the bucket census as a
    SECOND ``@lru_cache``'d full-corpus scan, which roughly doubled the Rule E
    path over ~7.5k contracts and pushed the contract's own
    ``dod-omn15411-rule-e-corpus-ratchet`` check past the DoD compliance
    runner's **60-second per-check timeout** ("Command timed out after 60s"),
    turning a passing gate red. Both consumers below derive from this one scan;
    do not reintroduce a second walk of ``contracts/``.
    """
    contracts_dir = _REPO_ROOT / "contracts"
    tier1_ratcheted: set[str] = set()
    tier1_generated: set[str] = set()
    tier2: set[str] = set()
    ratcheted_items: dict[str, set[str]] = {}
    ratcheted_checks: dict[str, int] = {}
    generated_items: dict[str, set[str]] = {}
    generated_checks: dict[str, int] = {}

    for path in sorted(contracts_dir.glob("*.yaml")):
        rel = f"contracts/{path.name}"
        for _path_str, label, _fragment in linter.lint_contract_warnings(path):
            dod_id = label.split(":", 1)[0]
            entry = f"{rel}::{dod_id}"
            is_generated = _is_generated_sigpipe_item(dod_id)

            if "sigpipe-fragile" in label:
                if is_generated:
                    tier1_generated.add(entry)
                else:
                    tier1_ratcheted.add(entry)

                match = _PRODUCER_LABEL_RE.search(label)
                if match is None:  # pragma: no cover - detector always labels
                    continue
                bucket = match.group(1)
                items, checks = (
                    (generated_items, generated_checks)
                    if is_generated
                    else (ratcheted_items, ratcheted_checks)
                )
                items.setdefault(bucket, set()).add(entry)
                checks[bucket] = checks.get(bucket, 0) + 1
            elif "sigpipe-possible" in label:
                tier2.add(entry)

    return (
        frozenset(tier1_ratcheted),
        frozenset(tier1_generated),
        frozenset(tier2),
        {b: (len(v), ratcheted_checks[b]) for b, v in ratcheted_items.items()},
        {b: (len(v), generated_checks[b]) for b, v in generated_items.items()},
    )


def _scan_corpus_rule_e() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return (tier1_ratcheted, tier1_generated, tier2_advisory) Rule E ids."""
    tier1, generated, tier2, _rb, _gb = _scan_corpus_rule_e_full()
    return tier1, generated, tier2


def _scan_corpus_rule_e_buckets() -> tuple[
    dict[str, tuple[int, int]], dict[str, tuple[int, int]]
]:
    """Return ({bucket: (items, check_values)}, same-for-generated).

    The per-bucket split is what a repair lane batches on, so the numbers get
    quoted in ticket comments and PR bodies -- and get quoted wrong. Derived
    from the shipped detector so the assertion below is a measurement, not a
    restatement.
    """
    _t1, _gen, _t2, ratcheted, generated = _scan_corpus_rule_e_full()
    return ratcheted, generated


@pytest.mark.unit
def test_rule_a_corpus_matches_frozen_baseline_exactly() -> None:
    baseline = _load_baseline(_RULE_A_BASELINE_PATH)
    live_a, _live_b, _live_c, _live_d, _live_f = _scan_corpus()

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
    _live_a, live_b, _live_c, _live_d, _live_f = _scan_corpus()

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
    _a, _b, live_c, _d, _f = _scan_corpus()

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
    _a, _b, _c, live_d, _f = _scan_corpus()

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
    # 2026-07-30, OCC#5673: 160 -> 159 items / 177 -> 176 checks. The
    # concurrent codex-merge-sweep append on this PR superseded
    # contracts/OMN-15192.yaml::dod-omn-15192-mutate-leg-app-credentialed
    # with dod-15192-mut-r34-stable, which buffers the producer
    # (body="$(...)" && printf '%s' "$body" | grep -qF) instead of piping
    # base64 -d straight into grep -q. The lint skips superseded ids, so the
    # instance leaves the live scan and this shrink-only pin must follow it
    # in the SAME PR.
    #
    # 2026-08-16: 159 -> 158 items / 176 -> 175 checks. This PR's append-only
    # supersede of contracts/OMN-14498.yaml::dod-omnibase-infra-pr-2436-deploy-scope
    # replaces the `base64 -d | grep -q` producer with the buffered-read idiom.
    # The lint skips superseded ids, so the instance leaves the live scan and
    # this shrink-only pin follows it in the SAME PR.
    "base64-decoded file body": (158, 175),
    "gh pr diff": (143, 143),
    "paginated REST list": (5, 5),
    "git history walk": (4, 4),
}
# The GENERATED side is deliberately NOT count-pinned.
#
# A first draft of this test pinned it exactly, at 58 distinct items. CI caught
# that within 40 minutes: OCC#5545 (the OMN-15441 companion for omnimarket#1963)
# landed on dev mid-review and the producer minted one more
# `dod-deploy-assessment`, taking `gh pr diff` from 47 to 48 and the total from
# 58 to 59. An exact pin on a machine-authored, monotonically-growing set would
# hard-fail EVERY future autobind PR -- the precise failure mode the carve-out
# exists to avoid, reintroduced one layer up.
#
# What IS asserted is the bucket SHAPE: the producer may mint more instances of
# the shapes it already mints, but if it starts emitting a NEW fragile producer
# kind that is a change in the producer, not routine growth, and it should fail.
# The count is recorded here as an observation with its as-of date, not enforced.
_RULE_E_GENERATED_BUCKETS: frozenset[str] = frozenset(
    {
        "gh pr diff",
        "paginated REST list",
        "iterating jq projection",
        "base64-decoded file body",
    }
)
# Observed 2026-07-30T03:30Z: 59 distinct items / 60 check_values. Growth is
# expected and tracked at the producer (OMN-15407), not here.


@pytest.mark.unit
def test_rule_e_per_bucket_census_is_pinned() -> None:
    """The human-authored per-bucket census matches a live detector scan.

    Shrink-only in spirit like the baselines: repairing a bucket must update
    this dict in the same PR, which is exactly the moment the remaining count
    posted on the ticket should change too.
    """
    ratcheted, _generated = _scan_corpus_rule_e_buckets()
    assert ratcheted == _RULE_E_RATCHETED_CENSUS, (
        "Rule E ratcheted per-bucket census drifted from the live scan. "
        f"live={ratcheted} pinned={_RULE_E_RATCHETED_CENSUS}. Update the pin in "
        "the SAME PR that repairs (or adds) instances, and update the "
        "remaining-count comment on OMN-15411 to match."
    )


@pytest.mark.unit
def test_rule_e_generated_producer_emits_no_new_fragile_shape() -> None:
    """The producer may mint MORE of what it mints; it may not mint a NEW shape.

    Counts on the generated side grow by one on most autobind PRs, so pinning
    them wedges the fleet. A new *bucket*, by contrast, means the producer's
    emitted command shape changed and a fresh class of false RED is being
    manufactured -- that is worth failing on.
    """
    _ratcheted, generated = _scan_corpus_rule_e_buckets()
    unexpected = set(generated) - _RULE_E_GENERATED_BUCKETS
    assert not unexpected, (
        f"The OCC companion producer began emitting NEW SIGPIPE-fragile producer "
        f"shape(s) {sorted(unexpected)} (live buckets {sorted(generated)}). This "
        "is not routine growth of the carved-out set -- it is a change in what "
        "omnimarket node_occ_companion_compute generates. Fix it at the producer "
        "(OMN-15407) rather than widening this set."
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

    # Only the RATCHETED side gets an exact count -- it is the shrink-only
    # baseline. `live_gen` grows on most autobind PRs (see
    # _RULE_E_GENERATED_BUCKETS above), so it is asserted non-empty rather than
    # equal; a zero would mean the detector stopped seeing the producer's output
    # entirely, which is a detector regression, not a repair.
    # 312 -> 311 (2026-07-30, OCC#5673), 311 -> 310 (2026-08-16, this PR): see
    # the shrink notes on _RULE_E_RATCHETED_CENSUS["base64-decoded file body"].
    assert len(live_t1) == len(baseline) == 310
    assert len(live_gen) >= 58

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


@pytest.mark.unit
def test_rule_e_corpus_is_walked_exactly_once_across_all_consumers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression pin for the 60s DoD-compliance-runner timeout.

    The contract's own ``dod-omn15411-rule-e-corpus-ratchet`` check shells out to
    ``uv run pytest <this module> -q -k rule_e``, and the runner kills any check
    at 60 seconds ("Command timed out after 60s"). Adding a SECOND
    ``@lru_cache``'d walk of ``contracts/`` for the bucket census doubled that
    path over ~7.5k contracts and turned the check red twice on CI before it was
    caught.

    Walking the corpus once is therefore load-bearing, not a micro-optimisation.
    This asserts it structurally: with the shared cache primed, neither public
    accessor may re-enter the detector.
    """
    _scan_corpus_rule_e_full()  # prime the shared cache

    calls = 0
    real = linter.lint_contract_warnings

    def counting(path: Path) -> list[tuple[str, str, str]]:
        nonlocal calls
        calls += 1
        result: list[tuple[str, str, str]] = real(path)
        return result

    monkeypatch.setattr(linter, "lint_contract_warnings", counting)
    _scan_corpus_rule_e()
    _scan_corpus_rule_e_buckets()

    assert calls == 0, (
        f"The Rule E consumers re-walked contracts/ {calls} time(s) after the "
        "shared scan was cached. Both accessors must derive from "
        "_scan_corpus_rule_e_full(); a second full-corpus walk pushes the "
        "contract's own ratchet check past the compliance runner's 60s timeout."
    )


# ---------------------------------------------------------------------------
# OMN-15540 Rule F corpus ratchet: predicates pinned to a MUTABLE external
# state.
#
# The class this stops: a dod_evidence predicate that can only ever be
# satisfied by an immutable-past state. Two of the six baseline entries were
# ALREADY deterministically RED when the rule landed -- contracts/OMN-10765's
# two checks 404 against a branch deleted on merge, and contracts/OMN-15484's
# second check asserts `failure` on a job that has since been re-run to
# `success`. An unsatisfiable check does not merely fail; it produces an
# unresolvable red that escalates to the operator as a "red-but-accepted"
# adjudication. This ratchet is what stops the corpus minting new ones.
#
# Rule F is HARD tier (it changes the linter's exit code), unlike warning-tier
# Rule E. The pre-commit hook catches it on CHANGED contracts; this corpus
# ratchet is the only surface that sees contracts a PR does not touch, and it
# runs in ci.yml's unconditional `contract-corpus-ratchets` job which is wired
# into the required `CI Summary` context.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rule_f_corpus_matches_frozen_baseline() -> None:
    baseline = _load_baseline(_RULE_F_BASELINE_PATH)
    _a, _b, _c, _d, live_f = _scan_corpus()

    new_violations = live_f - baseline
    healed = (baseline - live_f) - _RULE_F_CONCURRENT_REPAIR_EXEMPT

    assert not new_violations, (
        f"{len(new_violations)} NEW Rule F (mutable-state-pin) violation(s) "
        "found that are not in the frozen shrink-only baseline "
        f"({_RULE_F_BASELINE_PATH}): {sorted(new_violations)[:20]}. A "
        "dod_evidence predicate must not pin a state that moves underneath it: "
        "a specific `actions/runs/<id>` conclusion is rewritten by a re-run, a "
        "feature-branch ref 404s once the branch is deleted on merge, and an "
        "exact/upper bound on an unanchored `search/issues` count is falsified "
        "by the next re-mint. Bind a receipt, pin the squash commit on the "
        "mainline, or anchor the query to a closed window."
    )
    assert not healed, (
        f"{len(healed)} baseline entr"
        f"{'y is' if len(healed) == 1 else 'ies are'} no longer reproduced by "
        f"a live corpus scan, but the baseline file ({_RULE_F_BASELINE_PATH}) "
        f"was not updated to remove them: {sorted(healed)[:20]}. Update the "
        "baseline file to match (shrink it) when you repair a contract -- do "
        "not leave stale entries."
    )


@pytest.mark.unit
def test_rule_f_concurrent_repair_exemptions_are_still_baseline_entries() -> None:
    """The exemption set may only name entries the baseline actually carries.

    Without this, the exemption set is a place where an arbitrary id can be
    parked to silence the ratchet for a contract that was never in the census.
    """
    baseline = _load_baseline(_RULE_F_BASELINE_PATH)
    stray = _RULE_F_CONCURRENT_REPAIR_EXEMPT - baseline
    assert not stray, (
        f"_RULE_F_CONCURRENT_REPAIR_EXEMPT names {sorted(stray)}, which "
        f"{'is' if len(stray) == 1 else 'are'} not in "
        f"{_RULE_F_BASELINE_PATH}. The exemption only suppresses the `healed` "
        "assertion for entries that were in the frozen census; it is not a "
        "general-purpose allowlist. Remove the stray entr"
        f"{'y' if len(stray) == 1 else 'ies'}."
    )
