# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Contract substance floor (OMN-14409).

A ticket contract must declare at least one dod_evidence check that could
*fail if the work were wrong*. A check that only proves the PR object exists
(``gh pr view <n> --json number,state``) establishes binding, not correctness:
it passes identically whether the code is right or catastrophically broken.

The live defect this gate closes
--------------------------------
The Evidence-Source autobind (OMN-13317 F1) mints a contract when a product PR
fails the Receipt Gate, and then mints the receipts that satisfy it — so it
authors *both the bar and the proof*. ``contracts/OMN-14400.yaml`` is the live
instance: every one of its autobind-declared checks is a ``gh pr view`` probe.
Every gate passed. The receipt did not lie and the receipt-gate was not broken —
the contract simply set a bar that nothing about the work had to clear.

Nothing in the chain required a ticket's DoD to be *related to the work the
ticket did*. This gate is that requirement.

Policy
------
Each dod_evidence check is mapped to the :class:`EnumProofTier` it can actually
establish (see :func:`derive_proof_tier`). A contract PASSES when at least one
check reaches **L1 or above**. Existence probes derive to **L0** and therefore
can never satisfy the floor on their own.

Binding/stamp items keep working
--------------------------------
This gate does NOT reject existence probes, and does NOT touch receipt
``status``. Evidence-Source binding items (``occ-self-bind-pr-*`` and the
autobind's ``gh pr view`` probes) remain completely valid for the job they
exist to do — stamping the Evidence-Source line. They simply do not *count*
toward the substance floor. A contract carrying a substantive item plus any
number of binding items passes. The autobind stamp path is therefore rejected
at a rate of exactly zero; only a DoD consisting *entirely* of existence probes
fails.

Independence, without touching receipt status
---------------------------------------------
"An automated producer may not supply the substantive proof" is enforced
structurally rather than by identity comparison: the autobind emits only
existence probes, which derive to L0 and can never be substantive. This
deliberately avoids the ``verifier``/``runner`` PASS→ADVISORY downgrade — the
receipt gate treats ADVISORY as FAIL, so that approach would fail every
autobind receipt and kill the stamp path (OMN-14409 analysis, M4).

Reuses the existing OMN-13338 vocabulary (``EnumProofTier``); introduces no
second proof taxonomy.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from omnibase_core.enums.ticket.enum_proof_tier import EnumProofTier

# The floor. A contract needs at least one check at or above this tier.
SUBSTANCE_FLOOR = EnumProofTier.L1

# `gh pr view --json <fields>` restricted to these fields reads pure object
# metadata: it proves the PR record exists and what state it is in. It cannot
# discriminate correct code from broken code, so it derives to L0.
_EXISTENCE_JSON_FIELDS = frozenset(
    {
        "number",
        "state",
        "url",
        "title",
        "body",
        "headrefname",
        "headrefoid",
        "baserefname",
        "author",
        "isdraft",
        "mergedat",
        "createdat",
        "updatedat",
        "closedat",
        "mergeable",
        "mergestatestatus",
        "mergecommit",
    }
)

# Probes that read live runtime state — they establish L2 (the change is live),
# not merely that it merged.
_RUNTIME_PROBE_RE = re.compile(
    r"\b(kubectl|docker\s+(exec|inspect|ps)|psql|rpk|curl|httpx|wget)\b"
)

# Probes that execute the test suite — they establish L1 (the changed behavior
# is exercised).
_TEST_PROBE_RE = re.compile(
    r"\b(pytest|npm\s+test|vitest|jest|go\s+test|cargo\s+test|tox|make\s+test)\b"
)

# Probes that read a real CI verdict for the PR — falsifiable: they fail when CI
# fails. `gh pr view --json statusCheckRollup` carries the same signal.
_CI_OUTCOME_RE = re.compile(
    r"(\bgh\s+(pr\s+checks|run\s+view)\b|statusCheckRollup)", re.IGNORECASE
)

# Static assertions over SOURCE — a grep/rg that pins a symbol or line in the
# tree is falsifiable about the change (it fails if the code is not there).
_STATIC_ASSERT_RE = re.compile(r"\b(grep|rg|ast-grep)\b")

# Type/lint/gate runs. All fail on bad code.
_ANALYSIS_RE = re.compile(
    r"\b(pre-commit|mypy|ruff|pyright|eslint|tsc|import-linter|lint-imports)\b"
)

# Executing a program, script, or assertion — the general falsifiable family.
#
# The test is FALSIFIABILITY, not membership in a hand-curated list: a check is
# substantive if it CAN FAIL when the work is wrong. `diff -u expected actual`
# fails when the output is wrong. `jq -e` fails when the assertion is false.
# `make verify` fails when the target fails. `uv run validate-yaml` — OCC's own
# canonical validator — fails when the contract is malformed. These are exactly
# the checks we want authors writing, so this matcher is deliberately GENEROUS.
#
# A too-narrow allowlist here is not friction, it is a PERVERSE INCENTIVE:
# rejecting `diff` while accepting a self-referential `grep 'status: PASS'`
# (which GATE_SELF_REFERENTIAL currently permits) would push authors away from
# real evidence and toward the circular pattern this gate exists to eliminate —
# manufacturing the very debt OMN-14417 tracks. When in doubt, ACCEPT: a false
# accept costs one weak receipt; a false reject teaches authors that writing
# honest evidence does not pay.
_EXECUTABLE_RE = re.compile(
    r"""(
          ^\s*\.{0,2}/\S+                        # ./verify.sh, /usr/bin/x, ../y
        | \b(bash|sh|zsh|make|just|task)\b       # shells + task runners
        | \b(python3?|node|deno|ruby|perl)\b     # interpreters
        | \b(uv\s+run|poetry\s+run|npx|npm\s+run|pnpm|yarn)\b  # package runners
        # ONEX/OCC validators. `onex` is anchored to COMMAND position: an
        # unanchored \bonex\b matches the path segment in
        # `gh api .../contents/plugins/onex/skills/...` — which is a file-exists
        # probe over the API, not a validator run. Nearly every OmniNode path
        # contains "onex", so the loose form silently accepted content-free
        # probes (caught by the ratchet on OMN-11220).
        | (?:^|[|;&]\s*|\brun\s+)onex\b
        | \b(validate-[\w-]+|check-[\w-]+|scan-[\w-]+|verify-[\w-]+)\b
        | \bjq\s+-\w*e\b                         # `jq -e`: the -e flag IS the assert
        | \b(diff|cmp)\b                         # output comparison
    )""",
    re.VERBOSE,
)

# A diff assertion: `gh pr view --json files --jq '[.files[].path]'` names the
# files the PR must touch. It is falsifiable about the change (it fails if the
# diff is not what was claimed), so it is substantive — NOT an existence probe.
_DIFF_ASSERT_RE = re.compile(r"(--json[=\s]+[^|]*\bfiles\b|\.files\[)")

_GH_PR_VIEW_RE = re.compile(r"\bgh\s+pr\s+view\b")
_JSON_FLAG_RE = re.compile(r"--json[=\s]+([A-Za-z0-9_,]+)")

# ---------------------------------------------------------------------------
# Content-free probe families — all derive L0 (cannot satisfy the floor).
# ---------------------------------------------------------------------------

# The no-op family: commands that pass unconditionally. `check_value: "true"`
# is not evidence of anything; it is an unconditional PASS with extra steps.
_NO_OP_RE = re.compile(
    r"""^(
          true | : | exit\s+0 | echo(\s|$).* | printf(\s|$).* |
          ls(\s+[^|;&]*)? | pwd | test\s+-[fed]\s+\S+ | \[\s+-[fed]\s+\S+\s+\]
        )$""",
    re.VERBOSE,
)

# Self-referential: the probe reads the receipt corpus — i.e. its OWN evidence.
#     grep -q '^status: PASS$' drift/dod_receipts/OMN-XXXX/dod-001/command.yaml
# The receipt says PASS because the agent wrote PASS; the check confirms the
# agent wrote PASS. Circular by construction; it establishes nothing about the
# work. This is the OMN-14417 class.
_SELF_REFERENTIAL_RE = re.compile(r"drift/dod_receipts/")

# OMN-14417 KILL SWITCH — deliberately OFF.
#
# Deriving the self-referential class to L0 is correct, and it is a one-line
# flip. It is off because the pattern is not merely legacy debt — it is the
# CURRENT house style. Re-measured 2026-07-12 in a clean env against the pinned
# omnibase-core 0.46.7 wheel, with the deriver as it ships here:
#
#   flag OFF (shipped) ->   120 / 6,916 rejected (1.74%) — ALL grandfathered,
#                           0 un-grandfathered, and 0 / 187 contracts created in
#                           the last 7 days blocked (0.0% forward).
#   flag ON            -> 2,277 / 6,916 rejected (32.9%), of which 2,157 are NOT
#                           grandfathered, and 184 / 187 new contracts blocked
#                           (98.4% forward; an independent sweep over a slightly
#                           wider window measured 190/190 = 100%).
#
# Grandfathering does NOT rescue the ON case: at a ~100% forward rate the gate
# would reject essentially all new contract traffic, which is the
# reject-everything trap this ticket exists to avoid. The generator must be
# fixed first (OMN-14417 asks exactly that); then flip this to True and the
# substance floor gates the circular class with no further code change.
GATE_SELF_REFERENTIAL = False


def _is_existence_probe(command: str) -> bool:
    """Return True when ``command`` can only prove that the PR object exists.

    A ``gh pr view`` that requests *only* metadata fields is an existence probe.
    The same command requesting ``statusCheckRollup`` (CI outcome) or reviews is
    NOT — those fields say something falsifiable about the change, so they are
    left to derive a higher tier.
    """
    if not _GH_PR_VIEW_RE.search(command):
        return False

    fields: set[str] = set()
    for match in _JSON_FLAG_RE.finditer(command):
        fields.update(f.strip().lower() for f in match.group(1).split(",") if f.strip())

    # `gh pr view <n>` with no --json at all prints the PR body/metadata: still
    # pure existence.
    if not fields:
        return True

    # Any field outside the metadata set (e.g. statusCheckRollup) carries real
    # signal about the change — not an existence probe.
    return fields.issubset(_EXISTENCE_JSON_FIELDS)


def derive_proof_tier(check_type: str, check_value: str) -> EnumProofTier:
    """Map a dod_evidence check to the :class:`EnumProofTier` it can establish.

    This is the input the OMN-13338 tier apparatus never had. ``proof_packet``
    is populated on 0 of 10,164 live receipts, so a gate requiring a
    hand-authored packet is unsatisfiable. ``check_value`` is present on every
    check, so the tier can be *derived* rather than authored — no new authoring
    burden, and the existing tier vocabulary becomes operable.
    """
    command = (check_value or "").strip()

    # --- Content-free families: reject first, unconditionally. ---------------
    if not command:
        # No probe at all cannot establish anything.
        return EnumProofTier.L0

    if _NO_OP_RE.match(command):
        # `true`, `:`, `echo ok`, `exit 0`, bare `ls`, `test -f <path>`.
        # A command that cannot fail cannot be evidence.
        return EnumProofTier.L0

    if GATE_SELF_REFERENTIAL and _SELF_REFERENTIAL_RE.search(command):
        # Reads its own receipt. Circular (OMN-14417). Off by default — see the
        # kill-switch comment: ON rejects 98.4% of new contract traffic.
        return EnumProofTier.L0

    if _is_existence_probe(command):
        # Proves the PR object exists; passes whether the code is right or not.
        return EnumProofTier.L0

    # --- Substantive families: an explicit allowlist. ------------------------
    if _RUNTIME_PROBE_RE.search(command):
        return EnumProofTier.L2

    if (
        check_type == "test_passes"
        or _TEST_PROBE_RE.search(command)
        or _CI_OUTCOME_RE.search(command)
        or _DIFF_ASSERT_RE.search(command)
        or _STATIC_ASSERT_RE.search(command)
        or _ANALYSIS_RE.search(command)
        or _EXECUTABLE_RE.search(command)
    ):
        return EnumProofTier.L1

    # --- Default: REJECT. ----------------------------------------------------
    # The polarity is deliberately inverted (OMN-14409 review): an unrecognized
    # command is NOT assumed substantive. Defaulting to L1 meant the floor read
    # as "any check that is not literally `gh pr view`" — which `true` satisfies.
    # A probe must be recognizably falsifiable to count as proof.
    return EnumProofTier.L0


@dataclass
class CheckFinding:
    """One dod_evidence check and the tier it derives to."""

    item_id: str
    check_type: str
    check_value: str
    tier: EnumProofTier

    @property
    def is_substantive(self) -> bool:
        return self.tier.satisfies(SUBSTANCE_FLOOR)


@dataclass
class ContractResult:
    """Substance-floor verdict for a single contract."""

    path: Path
    ticket_id: str
    findings: list[CheckFinding] = field(default_factory=list)

    @property
    def has_checks(self) -> bool:
        return bool(self.findings)

    @property
    def substantive(self) -> list[CheckFinding]:
        return [f for f in self.findings if f.is_substantive]

    @property
    def passed(self) -> bool:
        # A contract with no dod_evidence checks is out of scope for this gate
        # (the Receipt Gate governs whether a contract is required at all).
        if not self.has_checks:
            return True
        return bool(self.substantive)

    def failure_message(self) -> str:
        probes = "\n".join(
            f"      - [{f.tier.value}] {f.item_id}: {f.check_value[:100]}"
            for f in self.findings
        )
        return (
            f"{self.path}: SUBSTANCE FLOOR FAILED — {self.ticket_id} declares "
            f"{len(self.findings)} dod_evidence check(s), and every one of them is "
            f"an existence probe (tier L0). An existence probe proves the PR object "
            f"exists; it passes identically whether the code is correct or broken.\n"
            f"    Declared checks:\n{probes}\n"
            f"    Fix: add at least one check at tier {SUBSTANCE_FLOOR.value}+ that "
            f"could fail if the work were wrong (a test, a behavioral assertion, a "
            f"guard/gate outcome, or a runtime readback). Existence/binding probes "
            f"stay valid for Evidence-Source stamping — they just cannot stand in "
            f"for proof of correctness."
        )


def evaluate_contract(path: Path) -> ContractResult:
    """Evaluate one contract YAML against the substance floor."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result = ContractResult(path=path, ticket_id=str(raw.get("ticket_id") or path.stem))

    for item in raw.get("dod_evidence") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "<unnamed>")
        for check in item.get("checks") or []:
            if not isinstance(check, dict):
                continue
            check_type = str(check.get("check_type") or "")
            check_value = str(check.get("check_value") or "")
            result.findings.append(
                CheckFinding(
                    item_id=item_id,
                    check_type=check_type,
                    check_value=check_value,
                    tier=derive_proof_tier(check_type, check_value),
                )
            )

    return result


_ALLOWLIST_PATH = Path(__file__).parent / "substance_floor_legacy_allowlist.yaml"


def load_legacy_allowlist(path: Path = _ALLOWLIST_PATH) -> set[str]:
    """Return the grandfathered ticket ids (OMN-14419 backfill).

    These contracts predate the floor and declare only existence probes. They are
    exempt so the gate does not wedge CI on pre-existing debt — the gate stays
    fail-closed for every NEW contract. The list is a ratchet: it may only shrink
    (see :func:`main`).
    """
    if not path.exists():
        return set()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(t) for t in (raw.get("legacy_contracts") or [])}


@dataclass
class SweepReport:
    """Partitioned verdicts over a set of contracts."""

    failures: list[ContractResult] = field(default_factory=list)
    grandfathered: list[str] = field(default_factory=list)
    # Ratchet: a listed contract that now PASSES must be delisted in the same PR,
    # otherwise the allowlist silently goes stale and hides future regressions.
    stale_allowlist: list[str] = field(default_factory=list)


def sweep(paths: list[Path], allowlist: set[str]) -> SweepReport:
    """Partition ``paths`` into new failures, grandfathered legacy, and stale
    allowlist entries."""
    report = SweepReport()
    for path in paths:
        if not path.exists():
            continue
        result = evaluate_contract(path)
        listed = result.ticket_id in allowlist

        if result.passed:
            if listed:
                report.stale_allowlist.append(result.ticket_id)
        elif listed:
            report.grandfathered.append(result.ticket_id)
        else:
            report.failures.append(result)
    return report


def _resolve_paths(args: argparse.Namespace) -> list[Path]:
    if args.all or not args.paths:
        return sorted(Path("contracts").glob("*.yaml"))
    # CI feeds the changed-file list, which can include non-contract paths.
    return [
        p for p in args.paths if p.suffix == ".yaml" and p.parent.name == "contracts"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Contract YAML files to check. Defaults to all of contracts/.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check the entire contracts/ corpus (used by the audit, not by CI).",
    )
    args = parser.parse_args(argv)

    paths = _resolve_paths(args)
    report = sweep(paths, load_legacy_allowlist())

    if report.grandfathered:
        print(
            f"substance floor: {len(report.grandfathered)} grandfathered legacy "
            f"contract(s) skipped (OMN-14419 backfill)"
        )

    exit_code = 0

    if report.failures:
        print(
            f"\n{len(report.failures)} contract(s) failed the substance floor "
            f"(OMN-14409):\n"
        )
        for result in report.failures:
            print(f"  {result.failure_message()}\n")
        exit_code = 1

    if report.stale_allowlist:
        listed = ", ".join(sorted(report.stale_allowlist))
        print(
            f"\n{len(report.stale_allowlist)} contract(s) now PASS the substance "
            f"floor but are still grandfathered. The allowlist is a ratchet — it "
            f"may only shrink.\n    Remove from "
            f"scripts/validation/substance_floor_legacy_allowlist.yaml: {listed}\n"
        )
        exit_code = 1

    if exit_code == 0:
        print(f"substance floor OK: {len(paths)} contract(s) checked")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
