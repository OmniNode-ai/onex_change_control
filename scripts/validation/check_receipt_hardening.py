# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Receipt hardening gate (OMN-13060, retro item A-5; per-entry hash OMN-14411).

Enforces three invariants on DoD receipts produced on/after the gate
introduction date (``run_timestamp >= 2026-06-12T00:00:00Z``):

1. A contract hash binding must be present: ``contract_entry_sha256``
   (preferred, OMN-13888) or ``contract_sha256`` (legacy, whole-file).
   Hand-authored sha-less receipts (the 2530/2533/2534 failure mode) are
   rejected at commit time instead of wedging the OCC PR at the merge gate.
2. The binding must match the pinned contract at the staged state
   (OMN-14411):
   - ``contract_entry_sha256``, when present, is authoritative and is
     checked against ``compute_contract_entry_sha256`` of the receipt's own
     ``dod_evidence[evidence_item_id]`` — the same per-entry hash the
     append-only gate (``validator_occ_append_only``) already enforces.
     Because this hash only folds in the receipt's own entry plus the
     immutable header, appending a *new* dod_evidence item to the contract
     does not change it, so prior receipts stay valid across appends.
   - ``contract_sha256`` (legacy, no ``contract_entry_sha256`` present) is
     checked against the whole-file ``sha256(contracts/<ticket_id>.yaml)``.
     This binding is inherently incompatible with the append-only contract
     model — it goes stale on *every* append to the contract, regardless of
     which entry changed — and exists only for receipts minted before
     OMN-13888. Do not mint new receipts against it.
   A mismatch means the bound entry (or, for legacy receipts, the whole
   file) mutated after the receipt was produced — rerun probes, regenerate
   the receipt.
3. PASS receipts may not use a session-local / generic verifier alias
   (``agent``, ``automated``, bare container ids, ...). A PASS receipt
   must name an identifiable independent verifier.

Receipts with ``run_timestamp`` before the introduction date are exempt:
2,870 legacy receipts on dev lack the field (907 more carry stale hashes,
382 carry denylisted verifiers) and are migration debt owned by their own
tickets — this gate is a ratchet on NEW receipts, not a retro-block.
``pre-commit run --all-files`` (which OCC CI runs) must stay green on the
legacy corpus.

SUPERSESSION BINDING (OMN-15459), the fourth invariant
------------------------------------------------------
Supersession is the *repair* primitive of an append-only receipt store: a
net-new ``<check_type>.supersede.<NNNN>.yaml`` carries the authoritative
``replacement`` for a frozen base receipt. Until OMN-15459 this gate
validated the replacement's schema, hashes and identity and **never asked
whether the replacement executes the check the superseded item declares**
— the string ``check_value`` did not appear anywhere in this file. That
made the repair path a laundering channel: any item, however specific its
declared bar, could be silently rebound to any check that exits 0.

The live instance: ``onex_change_control#5534`` (merged 2026-07-30,
``34c8dacc``) added **8** ``command.supersede.2552.yaml`` files against 8
DISTINCT ``dod_evidence`` items, every one carrying a **byte-identical**
``replacement.check_value`` — one ``grep -c 'def _build_specs'`` of
``scripts/create_kafka_topics.py``. So
``dod-occ-evidence-admissibility-validator`` (declared check: ``uv run
pytest tests/test_evidence_admissibility.py -q``) now authoritatively
attests that a Kafka-topic script defines a function, while the
admissibility suite it names goes unrun. ``OCC#5528`` did the same with 6
files one PR earlier. Machine-producer behaviour, not a hand-authoring
slip — it scales.

Two rules, both required, evaluated only on ``*.supersede.*.yaml`` files:

``S1`` — **distinctness.** Two supersessions in the same cohort (same
ticket directory, same ``.supersede.<TOKEN>.`` suffix — i.e. minted
together for one consuming PR) may not carry a byte-identical normalized
``replacement.check_value`` while naming DIFFERENT ``evidence_item_id``s.
One probe cannot be the authoritative proof of N distinct bars.

``S2`` — **family binding.** A replacement must reference the artifact
family of the item it replaces: at least one anchor derived from the
superseded item's OWN declared ``checks[*].check_value`` in
``contracts/<ticket>.yaml`` (file paths, grep symbols, test targets, PR
numbers), from a ``pr-<N>`` fragment in the item id, or the item id
itself. An arbitrary file is not that item's bar.

Neither rule is satisfied by falsifiability auditing: the OCC#5534 grep is
*genuinely* RED-derivable (1 at the fix ref, 0 at the parent). It is a
well-formed probe. It just falsifies a different item — which is why the
OMN-14505 predicate, ``lint_contract_check_values`` and
``contract_compliance_check`` all pass it. They reason about a check in
isolation; these two rules reason about the item↔replacement *pairing*.

REPAIR IS APPEND-ONLY, and the gate recognises it. A merged mis-paired
supersession is immutable — it is never rewritten. A net-new sibling
supersession marks it REPAIRED, and drops it from the violation set, when
all three hold: (a) the sibling's ``supersedes:`` names the mis-paired
*record* (``ModelReceiptSupersession`` sanctions superseding "the receipt
**or record** this one replaces"; the model is ``extra="forbid"``, so a
bespoke ``corrects:`` key would invalidate the record itself); (b) the
sibling's ``.supersede.<NNNN>.`` token is HIGHER, since
``validator_receipt_supersession`` resolves the active receipt as the
highest token — a lower-numbered "repair" would leave the wrong-item
record authoritative and be purely cosmetic; and (c) the sibling is
itself clean under S1+S2. The cure is strictly stronger than the disease:
it must carry a discriminating per-item check of its own, so "repair"
cannot be another rebind.

Pre-existing violations are carried in the frozen, shrink-only baseline
``.onex_ratchets/omn_15459_supersession_binding_baseline.yaml`` and are
skipped. Corpus mode (``--supersession-corpus``) asserts set equality
against that baseline in BOTH directions — a new violation fails, and a
repaired entry that was not removed from the baseline also fails. The
baseline may only shrink; never pad it to make a new supersession pass.

ABSOLUTE-PATH + STDOUT-EMITTABILITY (OMN-15710), the fifth and sixth
invariants
------------------------------------------------------------------
``onex_change_control#6084`` (merged 2026-08-05, ``3e24d9bd9``) landed three
receipts that passed every existing gate — Receipt Honesty Gate (rules A-E,
``omnibase_core.validation.validator_receipt_honesty``), this file's
contract-hash/verifier checks, ``verify / verify``, and
``Contract Compliance Check`` — while being demonstrably dishonest:

* ``occ6080-attribution-fix-no-live-mutation`` hardcoded
  ``/Users/jonah/Code/omni_home/docs/tracking/ROLLING_WORK_LEDGER.md`` into
  ``probe_command``/``check_value`` (unreproducible on any other host or a CI
  checkout), AND its recorded ``probe_stdout`` for the trailing ``grep -c``
  command was hand-written prose, not the integer ``grep -c`` can only emit.
* Two ``occ6080-grammar-repair-note`` receipts recorded a ``probe_stdout``
  that WAS byte-exact for their declared ``printf '...'`` command, but an
  ``actual_output`` that was hand-written prose the ``printf`` literal cannot
  produce.

None of A-E check whether recorded output is *structurally reproducible*
from the declared command, and none reject a machine-specific absolute path
in a probe body. Two new rules close both gaps, applied to the same
post-cutoff, PASS-receipt population as the checks above (same
``HARDENING_CUTOFF`` exemption — see ``check_receipt_file``):

``ABS_PATH`` — **no machine-specific absolute paths in probe bodies.**
``check_value``/``probe_command`` (receipts) and ``dod_evidence[*].checks[*
].check_value`` (contract files) may not contain ``/Users/``, ``/Volumes/``,
or ``/home/<user>/``. Unlike source-code lint (``aislop_sweep``'s
hardcoded-path rule), **no ``# local-path-ok`` allowlist is honored here**: a
receipt/contract check body is re-executed by CI on a fresh checkout as the
proof a claim is true, and there is no legitimate case for "this proof only
runs on my machine" — that is precisely the OCC#6084(a) defect. Source code
can have a justified machine-only branch; a probe cannot.

``STDOUT_EMIT`` — **bounded, honest shape-consistency check.** For a small,
explicitly enumerated registry of terminal-command shapes this file can
reason about structurally, assert the recorded ``probe_stdout`` — and
**only** ``probe_stdout``, see below — is consistent with what that command
can emit:

* ``printf '<literal>'`` / ``echo '<literal>'`` (no ``%`` format specifier)
  as the terminal command → ``probe_stdout`` must equal the literal,
  byte-for-byte (after ``\\n``/``\\t`` un-escaping for ``printf``).
* ``grep -c ...`` as the terminal command → the last non-empty output line
  must be a bare integer. Catches the OCC#6080(a) defect directly.
* ``wc -l`` as the terminal command → the last non-empty line must be an
  integer, optionally followed by a filename.
* ``--jq '<simple-scalar-path>'`` where the path's final segment matches
  ``sha``/``oid`` (e.g. ``.sha``, ``.mergeCommit.oid``) → the output must be
  a hex string.

Every class first skips (undetectable, not a violation) when ``probe_stdout``
parses as JSON — a ``{"evidence_ref": ..., "green_exit": 0, "red_exit": 1,
"red_ref": ...}`` bundle is a distinct, structured RED/GREEN differential
proof format used across dozens of receipts in this corpus, not the bare
scalar shape any registry entry describes.

**``actual_output`` is deliberately OUT OF SCOPE for every class**, including
the printf/echo literal one — this is a narrowing made during OMN-15710's own
verification, not the original design. ``ModelDodReceipt.actual_output``'s
own docstring (omnibase_core) states it is "distinct from probe_stdout... may
be a structured / truncated rendering" — schema-sanctioned to diverge from
the literal captured stream. A corpus-wide dry run of the original
(``probe_stdout``-and-``actual_output``) design against live ``dev`` found
dozens of pre-existing, legitimate PASS receipts across many tickets
(OMN-15571, OMN-15651, OMN-7334, and others) using ``actual_output`` for
exactly that documented purpose — narrative summaries, in at least one case
describing a second (RED) probe leg never shown in ``probe_command`` at all.
Checking ``actual_output`` for literal equality was a systemic false
positive against schema-sanctioned usage, not a defect signal, so the rule
was narrowed to ``probe_stdout`` only. **Documented residual**: the
occ6080-grammar-repair-note defect shape itself — ``probe_stdout`` correct,
``actual_output`` paraphrased — is therefore NOT caught by this mechanism
going forward; OMN-15710 AC1 tracks that specific pair's closure via
independent verification instead, and this residual is called out explicitly
in the OMN-15710 PR body/ticket comment per the ticket's own "false positive
→ narrow the check" instruction.

This is deliberately **not** general command execution or emulation — it is
a documented, closed registry (``_EMITTABILITY_REGISTRY`` plus the
printf/echo special case) of command shapes whose *only* possible stdout
shape can be derived without running anything. What it does **not** catch,
by design (residual, OMN-15710 PR body/ticket comment carries this too):
compound/piped ``jq`` filters (``[.a,.b] | @tsv``, the exact shape of the
OCC#6080(a) ``--jq`` clause — that defect is still caught, but via the
``grep -c`` half of the same command, not via this class), any command not
in the registry (``curl``, ``psql``, ``docker exec``, arbitrary scripts),
JSON-shaped ``probe_stdout`` of any kind (see above), multi-line/tabular
output, ``actual_output`` (see above), and quoting edge cases in the naive
top-level ``;``/``|``/``&`` command-segment splitter. A defect outside this
bounded registry is a false negative here, not a false pass — partial
mechanical coverage, not full command-emulation confidence.

Both rules apply only to ``EnumReceiptStatus.PASS`` receipts (consistent
with the existing denylisted-verifier check above): a PENDING/FAIL/ADVISORY
receipt is not asserting the check ran cleanly, so shape-consistency of its
output is not this gate's concern.

**Residual: a superseded base receipt's own dirty content is never
re-scanned by ABS_PATH/STDOUT_EMIT.** ``check_receipt_file`` resolves a
base receipt with a valid ``*.supersede.*.yaml`` sibling and returns ``[]``
for the base *before* either rule (or any rule in this file) runs against
the base's own fields — only the sibling's ``replacement`` is validated
(via ``_valid_supersession_replacement`` -> ``_receipt_binding_violations``,
which does run both new rules). This is the same append-only design the
four pre-existing rules already rely on (a merged receipt is immutable;
supersession is the repair primitive, not a rewrite), so ABS_PATH/STDOUT_EMIT
inherit it rather than introduce it. The practical effect: an immutable base
receipt that itself contains a machine-specific absolute path or a
non-reproducible ``probe_stdout`` is never independently flagged once a
clean replacement sibling exists, even though the dirty original stays on
disk. OCC autobind mints these supersede siblings routinely — it is what
produced the still-open ``command.supersede.6087.yaml`` finding this same
PR reports — so this is a live, not theoretical, channel. Closing it would
mean re-scanning every historically-superseded base for the two new rules,
which is a corpus-wide retro-audit outside this PR's "gate extensions only"
scope (operator ruling R-c); it is recorded here, not silently, per that
same scope decision.

Exit codes: 0 = all enforced receipts clean; 1 = violations found.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from functools import cache, lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import yaml
from omnibase_core.enums.ticket.enum_receipt_status import EnumReceiptStatus
from omnibase_core.models.contracts.ticket.model_dod_receipt import ModelDodReceipt
from omnibase_core.validation.validator_receipt_gate import (
    ContractEntryNotFoundError,
    compute_contract_entry_sha256,
    compute_contract_sha256,
)

if TYPE_CHECKING:
    from collections.abc import Callable
from pydantic import ValidationError

# Receipts produced on/after this UTC instant are subject to the gate.
# Earlier receipts are legacy migration debt (see module docstring).
HARDENING_CUTOFF = datetime(2026, 6, 12, 0, 0, 0, tzinfo=UTC)

# OMN-15710: ABS_PATH + STDOUT_EMIT are subject to their OWN, LATER cutoff,
# separate from HARDENING_CUTOFF. A corpus-wide dry run against every
# post-HARDENING_CUTOFF receipt on dev (OMN-15710 verification) found ~200
# pre-existing receipts, spanning 2026-06-12 through 2026-07-30, that predate
# these two rules and do not comply with them — largely two established,
# legitimate corpus conventions these rules did not anticipate: OCC
# self-binding receipts routinely embed the authoring worktree's absolute
# path, and "content-bound differential probe" receipts routinely record a
# narrative/JSON summary in probe_stdout rather than a literal single-command
# capture. Retro-blocking ~200 already-merged receipts is exactly what this
# file's own migration-debt philosophy exists to avoid (see module
# docstring: "a ratchet on NEW receipts, not a retro-block"). The latest
# such pre-existing receipt is timestamped 2026-07-30T23:05:00Z; the three
# receipts OMN-15710 was filed to fix (and the propagated-defect supersede
# file AC1 discusses) are all timestamped 2026-08-04/05 — a clean gap.
OMN_15710_CUTOFF = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)


def _after_omn_15710_cutoff(run_timestamp: datetime) -> bool:
    ts = run_timestamp if run_timestamp.tzinfo else run_timestamp.replace(tzinfo=UTC)
    return ts >= OMN_15710_CUTOFF


# Session-local / generic verifier aliases that cannot satisfy independent
# verification for a PASS receipt. Exact match after strip().lower().
# Seeded from the 2026-06-12 verifier survey on OCC dev (retro A-5(c)).
DENYLISTED_VERIFIERS = frozenset(
    {
        "agent",
        "automated",
        "self",
        "local",
        "session",
        "manual",
        "human",
        "foreground-orchestrator",
        "local-pytest",
        "local-pre-push",
        "runner-session",
    }
)

# Pattern-based denials: bare docker container ids and runner-session-scoped
# aliases are container names, not verifier identities.
DENYLISTED_VERIFIER_PATTERNS = (
    re.compile(r"^[0-9a-f]{12}$"),  # bare docker container id
    re.compile(r"^runner-session\b"),
    re.compile(r"^session-"),
)


def _is_denylisted_verifier(verifier: str) -> bool:
    normalized = verifier.strip().lower()
    if normalized in DENYLISTED_VERIFIERS:
        return True
    return any(p.match(normalized) for p in DENYLISTED_VERIFIER_PATTERNS)


# ---------------------------------------------------------------------------
# OMN-15710 rule 1 — ABS_PATH: no machine-specific absolute paths in probes.
# ---------------------------------------------------------------------------

# Matches the path root plus at least one path segment, so a bare mention of
# the word "Users" or "home" in prose does not false-positive. No allowlist
# is honored here — see the module docstring for why receipt/contract probe
# bodies are held to a stricter bar than source code.
_ABS_PATH_RE = re.compile(
    r"(/Users/[^\s'\"]+|/Volumes/[^\s'\"]+|/home/[^/\s'\"]+/[^\s'\"]*)"
)


def _absolute_path_violations(receipt: ModelDodReceipt) -> list[str]:
    """Return ABS_PATH violation fragments for a receipt's probe fields.

    Gated on ``OMN_15710_CUTOFF``, not ``HARDENING_CUTOFF`` — see that
    constant's comment.
    """
    if not _after_omn_15710_cutoff(receipt.run_timestamp):
        return []
    violations: list[str] = []
    for field_name in ("check_value", "probe_command"):
        value = getattr(receipt, field_name, None)
        if not isinstance(value, str):
            continue
        match = _ABS_PATH_RE.search(value)
        if match is None:
            continue
        violations.append(
            f"[ABS_PATH] {field_name} contains a machine-specific absolute "
            f"path ({match.group(1)!r}) — a receipt probe must be "
            "re-executable by CI on a fresh checkout of any host; no "
            "allowlist is honored for receipt/contract check bodies "
            "(OMN-15710). Use a repo-relative path or an env-var-resolved "
            "path instead."
        )
    return violations


CONTRACT_ABS_PATH_BASELINE_PATH = Path(
    ".onex_ratchets/omn_15710_contract_abs_path_baseline.yaml"
)


def load_contract_abs_path_baseline(baseline_path: Path) -> frozenset[str]:
    """Load the frozen, shrink-only baseline of pre-existing contract ABS_PATH hits.

    Mirrors ``load_supersession_baseline``'s shape/semantics for the same
    reason: contract files (unlike receipts) carry no ``run_timestamp``, so
    ``OMN_15710_CUTOFF`` cannot gate them — a frozen baseline is this file's
    only precedented mechanism for "pre-existing, not a retro-block" when a
    time-based cutoff isn't available.
    """
    data = _load_mapping(baseline_path)
    if data is None:
        return frozenset()
    entries = data.get("violations")
    if not isinstance(entries, list):
        return frozenset()
    return frozenset(str(entry) for entry in entries if isinstance(entry, str))


def _contract_dod_evidence_abs_path_violations(
    contract_path: Path, data: dict[str, object], baseline: frozenset[str]
) -> list[str]:
    """Return ABS_PATH violation fragments for a contract's dod_evidence checks."""
    entries = data.get("dod_evidence")
    if not isinstance(entries, list):
        return []
    violations: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item_id = entry.get("id", "<unknown>")
        if f"{contract_path.as_posix()}::{item_id}" in baseline:
            continue
        checks = entry.get("checks")
        if not isinstance(checks, list):
            continue
        for check in checks:
            if not isinstance(check, dict):
                continue
            check_value = check.get("check_value")
            if not isinstance(check_value, str):
                continue
            match = _ABS_PATH_RE.search(check_value)
            if match is None:
                continue
            violations.append(
                f"{contract_path}: [ABS_PATH] dod_evidence[{item_id!r}]."
                f"check_value contains a machine-specific absolute path "
                f"({match.group(1)!r}) — no allowlist is honored for "
                "contract check bodies (OMN-15710). Use a repo-relative "
                "path or an env-var-resolved path instead."
            )
    return violations


def check_contract_file(
    contract_path: Path, baseline: frozenset[str] | None = None
) -> list[str]:
    """Return ABS_PATH violations for one contract YAML (empty = clean).

    ``baseline`` suppresses pre-existing ``<path>::<item_id>`` entries
    frozen in ``CONTRACT_ABS_PATH_BASELINE_PATH`` — see
    ``load_contract_abs_path_baseline``.
    """
    if not contract_path.is_file():
        return []
    try:
        data = yaml.safe_load(contract_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        return [f"{contract_path}: unreadable contract YAML: {exc}"]
    if not isinstance(data, dict):
        return []
    return _contract_dod_evidence_abs_path_violations(
        contract_path, data, baseline if baseline is not None else frozenset()
    )


# ---------------------------------------------------------------------------
# OMN-15710 rule 2 — STDOUT_EMIT: bounded shape-consistency of recorded output
# against a closed, documented registry of terminal-command shapes.
# ---------------------------------------------------------------------------


def _split_top_level(command: str, seps: str) -> list[str]:
    """Split ``command`` on unquoted characters in ``seps``.

    Quote-aware so a separator character inside ``'...'``/``"..."`` does not
    split. Does not handle nested subshells or backslash-escaped quotes —
    documented residual scope, see module docstring.
    """
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in command:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch in seps:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def _trusted_terminal_segment(probe_command: str) -> str | None:
    """Return the terminal command segment IF this file trusts its context.

    Returns ``None`` (undetectable — not a violation) when:

    * ``probe_command`` spans multiple physical lines (heredocs/multi-line
      scripts routinely carry visible intermediate output this splitter
      cannot reason about);
    * the terminal segment carries an unquoted ``#`` — this corpus uses
      trailing ``# ...`` prose to describe ADDITIONAL steps/refs not shown
      as real shell syntax (a narrated methodology, not a literal
      one-command probe); a ``#`` here means the field is not what it looks
      like to this naive splitter.

    This does NOT additionally require every ``;``/``&``-joined prefix
    statement to be provably silent (an earlier design in this ticket's own
    verification history did, and it silently excluded the exact OCC#6084(a)
    shape — a non-silent ``gh pr view ...`` prefix before the terminal
    ``grep -c`` — from detection at all). A ``;``-chain's LAST statement
    still contributes only its own lines to the tail of combined stdout
    regardless of what an earlier statement wrote, so "last non-empty line"
    validation on the terminal command's own class remains sound even with a
    non-silent prefix. Residual noise from receipts whose ``probe_stdout``
    is a narrative/summary rather than a literal capture (a real,
    widespread convention in this corpus — see the frozen baseline below)
    is absorbed by ``ABS_PATH_STDOUT_EMIT_BASELINE_PATH``, not by
    over-narrowing detection here.
    """
    if "\n" in probe_command:
        return None

    parts = [p.strip() for p in _split_top_level(probe_command, ";|&") if p.strip()]
    terminal_segment = parts[-1] if parts else probe_command.strip()

    if len(_split_top_level(terminal_segment, "#")) > 1:
        return None

    return terminal_segment


_PRINTF_LITERAL_RE = re.compile(r"printf\s+(['\"])((?:(?!\1).)*)\1\s*$")
_ECHO_LITERAL_RE = re.compile(r"echo\s+(['\"])((?:(?!\1).)*)\1\s*$")


def _decode_printf_literal(raw: str) -> str:
    return raw.replace("\\n", "\n").replace("\\t", "\t")


_JQ_SCALAR_ARG_RE = re.compile(r"--jq\s+(['\"])(?P<expr>[^'\"]+)\1")
_JQ_SIMPLE_PATH_RE = re.compile(r"\.[A-Za-z_][A-Za-z0-9_.]*$")


def _is_jq_sha_segment(segment: str) -> bool:
    """True when ``segment`` ends in ``--jq '<simple scalar path>'`` naming a sha/oid.

    Deliberately excludes any expression containing ``|``, ``,``, or ``[`` —
    a compound/array jq filter (e.g. ``[.state,.mergeCommit.oid] | @tsv``) is
    out of scope for this closed registry; see module docstring.
    """
    match = _JQ_SCALAR_ARG_RE.search(segment)
    if match is None:
        return False
    expr = match.group("expr").strip()
    if any(c in expr for c in "|,[]"):
        return False
    if _JQ_SIMPLE_PATH_RE.fullmatch(expr) is None:
        return False
    last_field = expr.rsplit(".", maxsplit=1)[-1]
    return re.search(r"sha|oid", last_field, re.IGNORECASE) is not None


class EmittabilitySpec(NamedTuple):
    """One entry in the closed, documented terminal-command shape registry."""

    name: str
    matches: Callable[[str], bool]
    validate: Callable[[str], bool]
    expectation: str


_EMITTABILITY_REGISTRY: tuple[EmittabilitySpec, ...] = (
    EmittabilitySpec(
        name="GREP_COUNT",
        matches=lambda seg: (
            bool(re.match(r"^grep\b", seg))
            and re.search(r"(?:^|\s)-c(?:\s|$)", seg) is not None
        ),
        validate=lambda line: re.fullmatch(r"\d+", line) is not None,
        expectation="a bare non-negative integer (grep -c line count)",
    ),
    EmittabilitySpec(
        name="WC_LINES",
        matches=lambda seg: (
            bool(re.match(r"^wc\b", seg))
            and re.search(r"(?:^|\s)-l(?:\s|$)", seg) is not None
        ),
        validate=lambda line: re.fullmatch(r"\d+(?:\s+\S+)?", line) is not None,
        expectation="a bare integer, optionally followed by a filename (wc -l)",
    ),
    EmittabilitySpec(
        name="JQ_SHA",
        matches=_is_jq_sha_segment,
        validate=lambda line: re.fullmatch(r"[0-9a-fA-F]{7,64}", line) is not None,
        expectation="a hex SHA (--jq path whose final segment is sha/oid)",
    ),
)


def _last_nonempty_line(text: str) -> str:
    for line in reversed(text.strip().splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _is_json_shaped(value: str) -> bool:
    """True when ``value`` parses as a JSON object/array.

    A JSON evidence bundle (e.g. ``{"evidence_ref": ..., "green_exit": 0,
    "red_exit": 1, "red_ref": ...}``) is a distinct, structured proof format
    used across this corpus for RED/GREEN differential probes — legitimately
    not the bare scalar shape any entry in ``_EMITTABILITY_REGISTRY``
    describes. Out of scope for this closed registry; see module docstring.
    """
    stripped = value.strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        json.loads(stripped)
    except ValueError:
        return False
    return True


def _literal_emit_match(segment: str) -> tuple[str, str] | None:
    """Return ``(kind, raw_literal)`` for a terminal printf/echo literal, else None."""
    match = _PRINTF_LITERAL_RE.search(segment)
    if match is not None:
        return "printf", match.group(2)
    match = _ECHO_LITERAL_RE.search(segment)
    if match is not None:
        return "echo", match.group(2)
    return None


def _literal_emit_violations(segment: str, receipt: ModelDodReceipt) -> list[str]:
    """STDOUT_EMIT violations for a terminal printf/echo literal command.

    ``probe_stdout`` ONLY — see module docstring for why ``actual_output`` is
    out of scope for every STDOUT_EMIT class, not just this one.
    """
    matched = _literal_emit_match(segment)
    if matched is None:
        return []
    literal_kind, raw_literal = matched
    if "%" in raw_literal:
        # Contains a format specifier this file does not evaluate —
        # undetectable, not clean. Documented residual.
        return []
    literal = (
        _decode_printf_literal(raw_literal) if literal_kind == "printf" else raw_literal
    ).strip()

    value = receipt.probe_stdout
    if not value or value.strip() == literal:
        return []
    return [
        f"[STDOUT_EMIT] probe_stdout does not equal the terminal "
        f"command's own literal output — probe_command ends in "
        f"{literal_kind} {raw_literal!r}, which can only emit "
        f"{literal!r}, but probe_stdout is {value.strip()!r}. Rerun "
        "the declared command and record its byte-exact output; do "
        "not paraphrase (OMN-15710)."
    ]


def _registry_emit_violations(segment: str, receipt: ModelDodReceipt) -> list[str]:
    """STDOUT_EMIT violations against the closed terminal-command registry.

    ``probe_stdout`` ONLY — see module docstring.
    """
    spec = next((s for s in _EMITTABILITY_REGISTRY if s.matches(segment)), None)
    if spec is None:
        return []

    value = receipt.probe_stdout
    if not value or _is_json_shaped(value):
        return []
    last_line = _last_nonempty_line(value)
    if spec.validate(last_line):
        return []
    return [
        f"[STDOUT_EMIT] probe_stdout is not shape-consistent with "
        f"the terminal command {segment!r} (detected class: "
        f"{spec.name}, expected {spec.expectation}) — got "
        f"{last_line!r}. Rerun the declared command and record its "
        "actual output (OMN-15710)."
    ]


def _stdout_emittability_violations(receipt: ModelDodReceipt) -> list[str]:
    """Return STDOUT_EMIT violation fragments (empty = clean or undetectable class).

    Scoped to ``probe_stdout`` only — deliberately excludes ``actual_output``.
    ``ModelDodReceipt.actual_output`` is documented (see the field's own
    docstring in omnibase_core) as "distinct from probe_stdout... may be a
    structured / truncated rendering" — i.e. schema-sanctioned to diverge
    from the literal captured stream. A corpus-wide dry run of this rule
    against ``actual_output`` (OMN-15710 verification) found dozens of
    pre-existing, legitimate receipts across many tickets using
    ``actual_output`` for exactly that documented purpose — a narrative
    summary, sometimes describing a second (RED) probe leg not shown in
    ``probe_command`` at all. Checking ``actual_output`` for literal
    equality is incompatible with that schema-sanctioned use and was a
    systemic false positive, not a defect signal — narrowed accordingly.
    Documented residual: the occ6080-grammar-repair-note defect shape
    (probe_stdout correct, actual_output paraphrased) is therefore NOT
    caught by this mechanism; OMN-15710 AC1 tracks that pair's closure via
    independent verification instead.

    Gated on ``OMN_15710_CUTOFF``, not ``HARDENING_CUTOFF`` — see that
    constant's comment.
    """
    if not _after_omn_15710_cutoff(receipt.run_timestamp):
        return []
    if receipt.status is not EnumReceiptStatus.PASS:
        return []

    segment = _trusted_terminal_segment(receipt.probe_command)
    if segment is None:
        return []

    # The printf/echo literal class and the registry classes are mutually
    # exclusive by construction: a terminal command is either a bare literal
    # emitter or a shape checked by the registry, never both.
    if _literal_emit_match(segment) is not None:
        return _literal_emit_violations(segment, receipt)
    return _registry_emit_violations(segment, receipt)


def _supersession_candidates(receipt_path: Path) -> list[Path]:
    """Return append-only supersession files that may replace receipt_path."""
    if receipt_path.suffix != ".yaml":
        return []
    stem = receipt_path.name[: -len(receipt_path.suffix)]
    return sorted(receipt_path.parent.glob(f"{stem}.supersede.*{receipt_path.suffix}"))


def _contract_hash_violation(
    receipt: ModelDodReceipt, contract_path: Path
) -> str | None:
    """Return a violation string if receipt's contract hash binding is stale.

    Honors the entry-hash-first dual-accept policy (OMN-13888 mint order,
    OMN-14411 gate parity): the caller has already confirmed at least one of
    ``contract_entry_sha256`` / ``contract_sha256`` is set. Returns ``None``
    when the receipt is bound cleanly to the current contract.
    """
    contract_entry_sha256 = getattr(receipt, "contract_entry_sha256", None)
    contract_sha256 = getattr(receipt, "contract_sha256", None)
    if contract_entry_sha256 is not None:
        try:
            contract_data = yaml.safe_load(contract_path.read_text())
        except (OSError, yaml.YAMLError) as exc:
            return f"unreadable contract YAML at {contract_path}: {exc}"
        try:
            expected_entry = compute_contract_entry_sha256(
                contract_data, receipt.evidence_item_id
            )
        except ContractEntryNotFoundError:
            return (
                f"dod_evidence item {receipt.evidence_item_id!r} not found in "
                f"{contract_path}; contract_entry_sha256 cannot be validated. "
                "The entry was removed or renamed after this receipt was "
                "produced (append-only violation) — do not fabricate a hash."
            )
        if contract_entry_sha256 != expected_entry:
            return (
                "contract_entry_sha256 mismatch — receipt has "
                f"{contract_entry_sha256!r} but "
                f"dod_evidence[{receipt.evidence_item_id!r}] in {contract_path} "
                f"hashes to {expected_entry!r}. That entry was edited after this "
                "receipt was produced; rerun probes and regenerate the receipt."
            )
        return None

    expected_whole = f"sha256:{compute_contract_sha256(contract_path)}"
    if contract_sha256 != expected_whole:
        return (
            f"contract_sha256 mismatch — receipt has {contract_sha256!r} "
            f"but sha256({contract_path}) is {expected_whole!r}. The contract "
            "mutated after this receipt was produced; rerun probes and "
            "regenerate the receipt (mint contract_entry_sha256 per OMN-13888 "
            "so future appends to other entries do not invalidate it again)."
        )
    return None


def _receipt_binding_violations(
    receipt: ModelDodReceipt, contracts_dir: Path
) -> list[str]:
    """Return contract-binding + verifier violation fragments for one receipt.

    Shared core for both the primary per-file check and supersession
    replacement validation (OMN-14411), so the two paths cannot drift out of
    sync. Fragments carry no receipt/candidate path prefix; callers prepend
    their own.
    """
    violations: list[str] = []

    contract_entry_sha256 = getattr(receipt, "contract_entry_sha256", None)
    contract_sha256 = getattr(receipt, "contract_sha256", None)
    if contract_sha256 is None and contract_entry_sha256 is None:
        violations.append(
            "missing contract_sha256 (OMN-13060/A-5). Tool-generate the "
            "receipt; never hand-author. Prefer contract_entry_sha256 "
            f"(OMN-13888) — the per-entry hash of "
            f"dod_evidence[{receipt.evidence_item_id!r}] in "
            f"contracts/{receipt.ticket_id}.yaml — which survives later "
            "appends to the contract; legacy contract_sha256 is "
            f"sha256(contracts/{receipt.ticket_id}.yaml)."
        )
    else:
        contract_path = contracts_dir / f"{receipt.ticket_id}.yaml"
        if not contract_path.is_file():
            violations.append(
                f"contract {contract_path} does not exist for ticket "
                f"{receipt.ticket_id}"
            )
        else:
            mismatch = _contract_hash_violation(receipt, contract_path)
            if mismatch is not None:
                violations.append(mismatch)

    if receipt.status is EnumReceiptStatus.PASS and _is_denylisted_verifier(
        receipt.verifier
    ):
        violations.append(
            f"PASS receipt uses session-local verifier alias "
            f"{receipt.verifier!r} (OMN-13060/A-5). Name an identifiable "
            "independent verifier."
        )

    violations.extend(_absolute_path_violations(receipt))
    violations.extend(_stdout_emittability_violations(receipt))

    return violations


def _valid_supersession_replacement(
    receipt_path: Path, contracts_dir: Path
) -> tuple[bool, list[str]]:
    """Return whether a sibling supersession cleanly replaces receipt_path.

    OCC receipts are append-only: after a contract changes, the old receipt is
    preserved and a net-new ``command.supersede.NNNN.yaml`` carries the rebound
    receipt under ``replacement``. This gate should validate the authoritative
    replacement instead of requiring mutation of the immutable base receipt.
    """
    candidates = _supersession_candidates(receipt_path)
    if not candidates:
        return False, []

    target = receipt_path.as_posix()
    errors: list[str] = []
    for candidate in candidates:
        try:
            raw = yaml.safe_load(candidate.read_text())
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"{candidate}: unreadable supersession YAML: {exc}")
            continue
        if not isinstance(raw, dict) or raw.get("supersedes") != target:
            continue
        replacement = raw.get("replacement")
        if not isinstance(replacement, dict):
            errors.append(f"{candidate}: supersession has no mapping replacement")
            continue
        try:
            receipt = ModelDodReceipt.model_validate(replacement)
        except ValidationError as exc:
            errors.append(
                f"{candidate}: replacement fails ModelDodReceipt validation: {exc}"
            )
            continue

        violations = _receipt_binding_violations(receipt, contracts_dir)
        if violations:
            errors.extend(f"{candidate}: replacement {v}" for v in violations)
            continue
        return True, []

    return False, errors


def _validate_hardened_receipt(
    receipt_path: Path, receipt: ModelDodReceipt, contracts_dir: Path
) -> list[str]:
    return [
        f"{receipt_path}: {fragment}"
        for fragment in _receipt_binding_violations(receipt, contracts_dir)
    ]


def _coerce_timestamp(raw: object) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


_TIMESTAMP_KEYS = ("run_timestamp", "verified_at")


def _extract_receipt_timestamp(raw: dict[str, object]) -> datetime | None:
    """Extract the receipt's production timestamp from raw YAML data.

    Prefers top-level ``run_timestamp`` (the ModelDodReceipt field), then
    falls back to ``verified_at`` at any nesting depth — pre-schema legacy
    receipts (e.g. ``OMN-10362/dod-00*``) carry only that key, sometimes
    nested. Returns None when no parseable timestamp exists anywhere; the
    caller exempts such files because a timestamp-less artifact cannot
    parse as ModelDodReceipt and is already rejected as NONPASS by the
    receipt gate — this hook is the sha/verifier ratchet, not the schema
    enforcer.
    """
    top = _coerce_timestamp(raw.get("run_timestamp"))
    if top is not None:
        return top
    return _walk_for_timestamp(raw)


def _walk_for_timestamp(node: object) -> datetime | None:
    """Depth-first search for the first parseable timestamp key."""
    if isinstance(node, dict):
        for key in _TIMESTAMP_KEYS:
            found = _coerce_timestamp(node.get(key))
            if found is not None:
                return found
        children: list[object] = list(node.values())
    elif isinstance(node, list):
        children = list(node)
    else:
        return None
    for child in children:
        found = _walk_for_timestamp(child)
        if found is not None:
            return found
    return None


def _validate_receipt_model(
    receipt_path: Path, raw: dict[str, object]
) -> tuple[ModelDodReceipt | None, str | None]:
    """Validate a receipt across old/new omnibase_core receipt schemas."""
    try:
        return ModelDodReceipt.model_validate(raw), None
    except ValidationError as exc:
        if "contract_entry_sha256" not in raw:
            return (
                None,
                f"{receipt_path}: receipt fails ModelDodReceipt validation: {exc}",
            )
        legacy_raw = dict(raw)
        contract_entry_sha256 = legacy_raw.pop("contract_entry_sha256")
        try:
            receipt = ModelDodReceipt.model_validate(legacy_raw)
        except ValidationError:
            return (
                None,
                f"{receipt_path}: receipt fails ModelDodReceipt validation: {exc}",
            )
        object.__setattr__(receipt, "contract_entry_sha256", contract_entry_sha256)
        return receipt, None


# ---------------------------------------------------------------------------
# OMN-15459 — supersession binding (wrong-item rebind)
# ---------------------------------------------------------------------------

SUPERSESSION_TICKET = "OMN-15459"
SUPERSESSION_BASELINE_PATH = Path(
    ".onex_ratchets/omn_15459_supersession_binding_baseline.yaml"
)
RECEIPTS_ROOT = Path("drift/dod_receipts")

_SUPERSEDE_TOKEN_RE = re.compile(r"\.supersede\.([^.]+)\.ya?ml$")
_PATH_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_./-]*"
    r"\.(?:py|ts|tsx|js|jsx|ya?ml|sh|sql|md|toml|json|cfg|ini|txt)\b"
)
_QUOTED_RE = re.compile(r"""['"]([^'"\n]{3,})['"]""")
_PR_IN_ID_RE = re.compile(r"pr-(\d+)")
_PR_IN_CMD_RE = re.compile(r"(?:gh pr (?:view|checks|diff|merge|list)\s+|/pulls/)(\d+)")
_SYMBOLISH_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")

# Basenames that identify no item in particular: every receipt lives in a
# command.yaml, every ticket has a contract.yaml. Admitting these as anchors
# would let "greps some receipt file" satisfy family binding for any item.
_GENERIC_BASENAMES = frozenset(
    {
        "command.yaml",
        "command.yml",
        "contract.yaml",
        "contract.yml",
        "__init__.py",
        "ci.yml",
        "ci.yaml",
        "pyproject.toml",
        "readme.md",
    }
)

# Quoted operands that are jq/format plumbing rather than an item's subject.
_GENERIC_QUOTED = frozenset(
    {
        ".content",
        ".state",
        ".number",
        "number,state",
        ".[].filename",
        ".files[].path",
        "%s\\n",
    }
)


class SupersessionRule:
    """Rule identifiers used in violation strings and baseline entries."""

    DISTINCTNESS = "S1"
    FAMILY_BINDING = "S2"


def _normalize_check(value: object) -> str:
    """Collapse whitespace so YAML folding cannot mask an identical check."""
    return " ".join(str(value).split())


def _supersede_token(path: Path) -> str | None:
    match = _SUPERSEDE_TOKEN_RE.search(path.name)
    return match.group(1) if match else None


@cache
def _load_mapping(path: Path) -> dict[str, object] | None:
    """Parse a YAML mapping once per process.

    Corpus mode touches every supersession's cohort siblings and every
    contract; without memoisation the scan is quadratic in a 1,800-file
    corpus and does not finish. Callers treat the result as read-only.
    """
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return None
    return raw if isinstance(raw, dict) else None


def _supersession_item_id(path: Path, data: dict[str, object]) -> str:
    item = data.get("evidence_item_id")
    if isinstance(item, str) and item:
        return item
    replacement = data.get("replacement")
    if isinstance(replacement, dict):
        nested = replacement.get("evidence_item_id")
        if isinstance(nested, str) and nested:
            return nested
    return path.parent.name


def _supersession_check_value(data: dict[str, object]) -> str | None:
    replacement = data.get("replacement")
    if not isinstance(replacement, dict):
        return None
    check_value = replacement.get("check_value")
    if not isinstance(check_value, str) or not check_value.strip():
        return None
    return _normalize_check(check_value)


@cache
def _contract_entry(
    contracts_dir: Path, ticket_id: str, item_id: str
) -> dict[str, object] | None:
    contract = _load_mapping(contracts_dir / f"{ticket_id}.yaml")
    if contract is None:
        return None
    entries = contract.get("dod_evidence")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id") == item_id:
            return entry
    return None


@cache
def _item_anchors(
    contracts_dir: Path, ticket_id: str, item_id: str
) -> tuple[frozenset[str], frozenset[str]]:
    """Return (text anchors lowercased, PR-number anchors) for a superseded item.

    Anchors are what makes a replacement *about this item*: the paths,
    symbols and test targets the item's own declared checks name, the PR
    number its id embeds, and the item id itself. Generic plumbing
    (command.yaml, jq selectors) is excluded — admitting it would let any
    receipt-shaped grep satisfy the rule for every item.
    """
    entry = _contract_entry(contracts_dir, ticket_id, item_id)
    text_anchors: set[str] = {item_id.lower()}
    pr_anchors: set[str] = set(_PR_IN_ID_RE.findall(item_id))

    checks = (entry or {}).get("checks")
    if not isinstance(checks, list):
        checks = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        declared = check.get("check_value")
        if not isinstance(declared, str):
            continue
        for match in _PATH_TOKEN_RE.finditer(declared):
            token = match.group(0)
            basename = token.rsplit("/", maxsplit=1)[-1]
            if basename.lower() in _GENERIC_BASENAMES:
                continue
            text_anchors.add(token.lower())
            text_anchors.add(basename.lower())
        for quoted in _QUOTED_RE.findall(declared):
            operand = quoted.strip()
            if operand in _GENERIC_QUOTED or operand.startswith("."):
                continue
            if not _SYMBOLISH_RE.search(operand):
                continue
            text_anchors.add(operand.lower())
        pr_anchors.update(_PR_IN_CMD_RE.findall(declared))

    return frozenset(text_anchors), frozenset(pr_anchors)


def _check_references_item(
    check_value: str, text_anchors: frozenset[str], pr_anchors: frozenset[str]
) -> bool:
    lowered = check_value.lower()
    if any(anchor in lowered for anchor in text_anchors):
        return True
    return any(
        re.search(rf"\b{re.escape(pr)}\b", check_value) is not None for pr in pr_anchors
    )


@cache
def _cohort_members(path: Path) -> tuple[Path, ...]:
    """Supersessions minted together with ``path`` for the same consuming PR.

    A cohort is (ticket directory, ``.supersede.<TOKEN>.`` suffix). That is
    the unit the producer emits in one shot, and the unit across which one
    probe was reused for N items.
    """
    token = _supersede_token(path)
    if token is None:
        return (path,)
    ticket_dir = path.parent.parent
    if not ticket_dir.is_dir():
        return (path,)
    return tuple(sorted(ticket_dir.glob(f"*/*.supersede.{token}.yaml")))


@cache
def _repaired_targets(item_dir: Path, contracts_dir: Path) -> frozenset[str]:
    """Paths in ``item_dir`` cured by a net-new, itself-clean repair record.

    Merged receipts are immutable, so a mis-paired supersession is repaired by
    APPENDING a sibling whose ``supersedes:`` names *that supersession record*
    rather than the base receipt — the shape ``ModelReceiptSupersession``
    already sanctions ("path of the receipt **or record** this one replaces").
    ``corrects:`` is not an option: the model is ``extra="forbid"``, so an
    unknown key would make the record itself invalid.

    Two conditions, both required:

    * the repair is itself clean under S1+S2 — otherwise "repair" is a second
      rebind wearing a different hat;
    * the repair's ``.supersede.<NNNN>.`` token is HIGHER than its target's.
      ``omnibase_core.validation.validator_receipt_supersession`` resolves the
      active receipt as the highest ``NNNN`` in the chain, so a lower-numbered
      repair would leave the wrong-item record authoritative — cosmetic, not a
      cure.
    """
    repaired: set[str] = set()
    if not item_dir.is_dir():
        return frozenset(repaired)
    for candidate in sorted(item_dir.glob("*.supersede.*.yaml")):
        data = _load_mapping(candidate)
        if data is None:
            continue
        superseded = data.get("supersedes")
        if not isinstance(superseded, str) or ".supersede." not in superseded:
            continue
        declared = Path(superseded.strip())
        # ``supersedes`` is OCC-root-relative while ``item_dir`` may be
        # absolute or relative depending on how the gate was invoked. Match on
        # the <ticket>/<item> tail and resolve the file inside item_dir, so the
        # cure works identically from pre-commit (relative paths), CI corpus
        # mode, and an absolute-path invocation.
        if (
            declared.parent.name != item_dir.name
            or declared.parent.parent.name != item_dir.parent.name
        ):
            continue
        target = item_dir / declared.name
        if target == candidate or not target.is_file():
            continue
        repair_token = _supersede_token(candidate)
        target_token = _supersede_token(target)
        if repair_token is None or target_token is None:
            continue
        if not (repair_token.isdigit() and target_token.isdigit()):
            continue
        if int(repair_token) <= int(target_token):
            continue
        if _supersession_violations(
            candidate, contracts_dir, baseline=frozenset(), follow_repairs=False
        ):
            continue
        repaired.add(target.as_posix())
    return frozenset(repaired)


def _supersession_violations(
    path: Path,
    contracts_dir: Path,
    baseline: frozenset[str],
    *,
    follow_repairs: bool = True,
) -> list[tuple[str, str]]:
    """Return [(rule, message)] for one supersession file (empty = clean)."""
    posix = path.as_posix()
    data = _load_mapping(path)
    if data is None:
        return []
    check_value = _supersession_check_value(data)
    if check_value is None:
        # No replacement check to pair with anything. Malformed replacements
        # are the base gate's job (_valid_supersession_replacement), not this
        # rule's.
        return []

    if follow_repairs and posix in _repaired_targets(path.parent, contracts_dir):
        return []

    item_id = _supersession_item_id(path, data)
    ticket_id = data.get("ticket_id")
    if not isinstance(ticket_id, str) or not ticket_id:
        ticket_id = path.parent.parent.name

    violations: list[tuple[str, str]] = []

    # S1 — distinctness across the cohort.
    collisions: set[str] = set()
    for sibling in _cohort_members(path):
        if sibling == path:
            continue
        sibling_data = _load_mapping(sibling)
        if sibling_data is None:
            continue
        if _supersession_check_value(sibling_data) != check_value:
            continue
        sibling_item = _supersession_item_id(sibling, sibling_data)
        if sibling_item == item_id:
            continue
        if follow_repairs and (
            sibling.as_posix() in _repaired_targets(sibling.parent, contracts_dir)
        ):
            continue
        collisions.add(sibling_item)
    if collisions:
        violations.append(
            (
                SupersessionRule.DISTINCTNESS,
                f"replacement.check_value is byte-identical to the supersession "
                f"for {len(collisions)} DIFFERENT evidence item(s) minted in the "
                f"same cohort ({', '.join(sorted(collisions))}). One probe cannot "
                f"be the authoritative proof of several distinct bars — that is "
                f"the {SUPERSESSION_TICKET} wrong-item rebind. Give each "
                "superseded item a check that discriminates it, or supersede "
                "only the one item this probe actually proves.",
            )
        )

    # S2 — family binding to the superseded item.
    text_anchors, pr_anchors = _item_anchors(contracts_dir, ticket_id, item_id)
    if not _check_references_item(check_value, text_anchors, pr_anchors):
        sample = sorted(a for a in text_anchors if a != item_id.lower())[:4]
        violations.append(
            (
                SupersessionRule.FAMILY_BINDING,
                f"replacement.check_value references nothing the superseded item "
                f"{item_id!r} declares. Expected at least one anchor from that "
                f"item's own dod_evidence entry in "
                f"contracts/{ticket_id}.yaml — paths/symbols "
                f"{sample or '(none declared)'}, PR number(s) "
                f"{sorted(pr_anchors) or '(none)'}, or the item id itself — but "
                f"the replacement runs: {check_value[:160]!r}. A substantive "
                "probe of the WRONG item is still a wrong-item rebind "
                f"({SUPERSESSION_TICKET}).",
            )
        )

    # Baseline suppression is per (path, RULE), not per path: a file carried as
    # a known S1 violation that later also trips S2 must still be reported.
    suppressed = _baseline_index(baseline).get(posix)
    if suppressed is None:
        return violations
    if not suppressed:
        return []
    return [item for item in violations if item[0] not in suppressed]


@lru_cache(maxsize=8)
def _baseline_index(baseline: frozenset[str]) -> dict[str, frozenset[str]]:
    """Map baselined path -> suppressed rule codes.

    Entries are ``<path>::S1+S2``. A bare ``<path>`` (the shape a
    hand-written entry tends to take) maps to the empty set, meaning
    "suppress every rule for this file".
    """
    index: dict[str, set[str]] = {}
    for entry in baseline:
        path_part, _, rules_part = entry.partition("::")
        rules = index.setdefault(path_part, set())
        if rules_part:
            rules.update(rules_part.split("+"))
    return {path: frozenset(rules) for path, rules in index.items()}


def load_supersession_baseline(baseline_path: Path) -> frozenset[str]:
    """Load the frozen, shrink-only baseline of pre-existing violations."""
    data = _load_mapping(baseline_path)
    if data is None:
        return frozenset()
    entries = data.get("violations")
    if not isinstance(entries, list):
        return frozenset()
    return frozenset(str(entry) for entry in entries if isinstance(entry, str))


def check_supersession_file(
    path: Path, contracts_dir: Path, baseline: frozenset[str]
) -> list[str]:
    """Return violation strings for one supersession file (empty = clean)."""
    return [
        f"{path}: [{rule}] {message}"
        for rule, message in _supersession_violations(path, contracts_dir, baseline)
    ]


def scan_supersession_corpus(
    receipts_root: Path, contracts_dir: Path
) -> dict[str, list[str]]:
    """Return {posix path: [rules]} for every violating supersession on disk."""
    findings: dict[str, list[str]] = {}
    for path in sorted(receipts_root.glob("*/*/*.supersede.*.yaml")):
        violations = _supersession_violations(path, contracts_dir, baseline=frozenset())
        if violations:
            findings[path.as_posix()] = sorted({rule for rule, _ in violations})
    return findings


def _baseline_entries(findings: dict[str, list[str]]) -> list[str]:
    return sorted(f"{path}::{'+'.join(rules)}" for path, rules in findings.items())


def run_supersession_corpus(
    receipts_root: Path, contracts_dir: Path, baseline_path: Path
) -> int:
    """Corpus ratchet: set-equality against the frozen baseline, both ways."""
    findings = scan_supersession_corpus(receipts_root, contracts_dir)
    observed = set(_baseline_entries(findings))
    baseline = load_supersession_baseline(baseline_path)

    new_violations = sorted(observed - baseline)
    stale_baseline = sorted(baseline - observed)

    print(
        f"Supersession binding corpus ({SUPERSESSION_TICKET}): "
        f"{len(observed)} violating file(s); baseline {len(baseline)}."
    )
    if not new_violations and not stale_baseline:
        print("Corpus matches the frozen baseline exactly.")
        return 0

    if new_violations:
        print(f"\nNEW violations absent from {baseline_path} ({len(new_violations)}):")
        for entry in new_violations:
            print(f"  + {entry}")
        for entry in new_violations[:20]:
            path = Path(entry.split("::", maxsplit=1)[0])
            for line in check_supersession_file(path, contracts_dir, frozenset()):
                print(f"      {line}")
        print(
            "\nDo NOT pad the baseline. Either give each superseded item a check "
            "that discriminates it, or append a `corrects:` repair record."
        )
    if stale_baseline:
        print(
            f"\nBaseline entries that no longer violate ({len(stale_baseline)}) — "
            "shrink the baseline in the same PR that repaired them:"
        )
        for entry in stale_baseline:
            print(f"  - {entry}")
    return 1


def write_supersession_baseline(
    receipts_root: Path, contracts_dir: Path, baseline_path: Path
) -> int:
    """Regenerate the frozen baseline (repair PRs only; never to pass a new file)."""
    findings = scan_supersession_corpus(receipts_root, contracts_dir)
    entries = _baseline_entries(findings)
    header = (
        "---\n"
        "# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.\n"
        "# SPDX-License-Identifier: MIT\n"
        "#\n"
        f"# Frozen, shrink-only baseline of pre-existing {SUPERSESSION_TICKET}\n"
        "# supersession-binding violations across drift/dod_receipts/**, as of the\n"
        f"# {SUPERSESSION_TICKET} landing commit.\n"
        "#\n"
        "# Each entry is `<supersession path>::<rules>` where the rules are:\n"
        "#   S1  distinctness  — byte-identical replacement.check_value shared with\n"
        "#                       another item's supersession minted in the same cohort\n"
        "#   S2  family binding — replacement.check_value references nothing the\n"
        "#                       superseded item's own contract entry declares\n"
        "#\n"
        "# RATCHET DISCIPLINE: this list may only SHRINK. Do NOT add entries — a new\n"
        "# wrong-item rebind is a hard failure of\n"
        "# scripts/validation/check_receipt_hardening.py, full stop. Remove an entry\n"
        "# only when the merged supersession has been repaired by an APPENDED sibling\n"
        "# carrying `corrects: <that path>` (merged receipts are never rewritten);\n"
        "# the gate then stops counting it and corpus mode fails until this file is\n"
        "# shrunk to match. Both directions are asserted, so partial removal and\n"
        "# padding each hard-fail\n"
        "# tests/unit/scripts/test_supersession_binding_gate.py.\n"
        "#\n"
        "# Regenerate (repair PRs only):\n"
        "#   uv run python scripts/validation/check_receipt_hardening.py \\\n"
        "#     --write-supersession-baseline\n"
        "violations:\n"
    )
    body = "".join(f"  - {entry}\n" for entry in entries)
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(header + body)
    print(f"Wrote {len(entries)} baseline entries to {baseline_path}.")
    return 0


def check_supersession_wiring(ci_yaml_path: Path) -> list[str]:
    """Assert the corpus ratchet job exists, is unconditional, and gates CI Summary.

    Detection that is not a merge gate gets ignored (root CLAUDE.md rule 5),
    and a gate whose two halves can be deleted in one edit is not a gate.
    This runs both as a pre-commit hook scoped to ci.yml and as a step inside
    the job itself, so the wiring is re-asserted on every PR rather than only
    on PRs that happen to touch the workflow.
    """
    job_id = "supersession-binding-ratchet"
    summary_id = "ci-summary"
    try:
        loaded = yaml.safe_load(ci_yaml_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        return [f"unreadable {ci_yaml_path}: {exc}"]
    jobs = (loaded or {}).get("jobs")
    if not isinstance(jobs, dict):
        return [f"{ci_yaml_path} declares no `jobs:` mapping"]

    job = jobs.get(job_id)
    if not isinstance(job, dict):
        return [
            f"job `{job_id}` is absent from {ci_yaml_path.name}. It is the only "
            "required-path surface that scans every supersession in "
            "drift/dod_receipts/**; removing it re-opens the wrong-item rebind "
            f"channel ({SUPERSESSION_TICKET})."
        ]

    failures: list[str] = []
    if "needs" in job:
        failures.append(
            f"job `{job_id}` declares `needs:` ({job['needs']!r}). It must be "
            "unconditional — a needs-chain lets an upstream skip silently skip "
            "the ratchet."
        )
    if "if" in job:
        failures.append(
            f"job `{job_id}` declares `if:` ({job['if']!r}). It must be "
            "unconditional — a skip here means something is wrong, not that the "
            "gate legitimately opted out."
        )

    steps = job.get("steps")
    run_blob = "\n".join(
        str(step.get("run", ""))
        for step in (steps if isinstance(steps, list) else [])
        if isinstance(step, dict)
    )
    if "--supersession-corpus" not in run_blob:
        failures.append(
            f"job `{job_id}` does not run check_receipt_hardening.py "
            "--supersession-corpus. The job name is not the gate; executing the "
            "corpus ratchet is."
        )
    if "test_supersession_binding_gate.py" not in run_blob:
        failures.append(
            f"job `{job_id}` does not run "
            "tests/unit/scripts/test_supersession_binding_gate.py, which holds "
            "the RED/GREEN controls against the live OCC#5534 defect shape."
        )
    if "--check-supersession-wiring" not in run_blob:
        failures.append(
            f"job `{job_id}` does not re-run --check-supersession-wiring. The "
            "job must re-assert its own wiring on every PR, not only on PRs that "
            "edit ci.yml."
        )

    summary = jobs.get(summary_id)
    if not isinstance(summary, dict):
        failures.append(
            f"job `{summary_id}` is absent — `CI Summary` is the required context "
            "on OCC dev; without it nothing is enforced."
        )
        return failures
    needs = summary.get("needs")
    needs_list = needs if isinstance(needs, list) else [needs]
    if job_id not in [str(item) for item in needs_list]:
        failures.append(
            f"job `{summary_id}` does not list `{job_id}` in `needs:`, so the "
            "required context does not wait for the ratchet."
        )
    summary_steps = summary.get("steps")
    summary_blob = "\n".join(
        str(step.get("run", ""))
        for step in (summary_steps if isinstance(summary_steps, list) else [])
        if isinstance(step, dict)
    )
    if f"needs.{job_id}.result" not in summary_blob:
        failures.append(
            f"job `{summary_id}` has no strict success-only check on "
            f"`needs.{job_id}.result`. `needs:` alone treats `skipped` as "
            "non-blocking — the gate must fail closed on any non-success."
        )
    return failures


def check_receipt_file(
    receipt_path: Path,
    contracts_dir: Path,
    supersession_baseline: frozenset[str] | None = None,
) -> list[str]:
    """Return violation strings for one receipt file (empty = clean)."""
    if ".supersede." in receipt_path.name:
        return check_supersession_file(
            receipt_path,
            contracts_dir,
            supersession_baseline if supersession_baseline is not None else frozenset(),
        )

    try:
        raw = yaml.safe_load(receipt_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        return [f"{receipt_path}: unreadable receipt YAML: {exc}"]
    if not isinstance(raw, dict):
        return [f"{receipt_path}: receipt YAML is not a mapping"]

    # Legacy exemption decided on the raw timestamp BEFORE model validation,
    # so pre-cutoff receipts with historical schema quirks never block.
    # No timestamp at all → exempt: such a file cannot parse as
    # ModelDodReceipt and the receipt gate already rejects it as NONPASS.
    run_ts = _extract_receipt_timestamp(raw)
    if run_ts is None or run_ts < HARDENING_CUTOFF:
        return []

    superseded, supersession_errors = _valid_supersession_replacement(
        receipt_path, contracts_dir
    )
    if superseded:
        return []
    if supersession_errors:
        return supersession_errors

    receipt, error = _validate_receipt_model(receipt_path, raw)
    if error is not None:
        return [error]
    if receipt is None:
        return [f"{receipt_path}: receipt validation returned no model"]

    return _validate_hardened_receipt(receipt_path, receipt, contracts_dir)


def _check_staged_file(
    path: Path,
    contracts_dir: Path,
    supersession_baseline: frozenset[str],
    contract_abs_path_baseline: frozenset[str],
) -> list[str]:
    """Route one staged file to the contract- or receipt-shaped check (OMN-15710).

    Contract files (``contracts/<TICKET>.yaml``) carry
    ``dod_evidence[*].checks[*].check_value`` directly and get the
    contract-shaped ABS_PATH scan instead of ``ModelDodReceipt`` parsing.
    """
    if path.as_posix().startswith(f"{contracts_dir.as_posix()}/"):
        return check_contract_file(path, contract_abs_path_baseline)
    return check_receipt_file(path, contracts_dir, supersession_baseline)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Receipt hardening gate: staged DoD receipts produced on/after "
            f"{HARDENING_CUTOFF.date()} must carry a matching contract_sha256 "
            "and a non-session-local verifier."
        )
    )
    parser.add_argument(
        "files", nargs="*", help="Receipt YAML paths (from pre-commit)."
    )
    parser.add_argument(
        "--contracts-dir",
        default="contracts",
        help="Directory containing OMN-XXXX.yaml ticket contracts.",
    )
    parser.add_argument(
        "--receipts-root",
        default=str(RECEIPTS_ROOT),
        help="Root of the DoD receipt corpus (supersession corpus mode).",
    )
    parser.add_argument(
        "--supersession-baseline",
        default=str(SUPERSESSION_BASELINE_PATH),
        help=(
            f"Frozen shrink-only baseline of pre-existing {SUPERSESSION_TICKET} "
            "supersession-binding violations."
        ),
    )
    parser.add_argument(
        "--supersession-corpus",
        action="store_true",
        help=(
            "Scan every supersession in the corpus and assert set equality "
            "against the frozen baseline in both directions."
        ),
    )
    parser.add_argument(
        "--write-supersession-baseline",
        action="store_true",
        help=(
            "Regenerate the frozen baseline. Repair PRs only — never run this "
            "to make a newly authored supersession pass."
        ),
    )
    parser.add_argument(
        "--check-supersession-wiring",
        action="store_true",
        help=(
            "Anti-removal anchor: assert the corpus ratchet job exists, is "
            "unconditional, and fails CI Summary closed."
        ),
    )
    parser.add_argument(
        "--ci-yaml",
        default=".github/workflows/ci.yml",
        help="Workflow file inspected by --check-supersession-wiring.",
    )
    parser.add_argument(
        "--contract-abs-path-baseline",
        default=str(CONTRACT_ABS_PATH_BASELINE_PATH),
        help=(
            "Frozen shrink-only baseline of pre-existing OMN-15710 ABS_PATH "
            "violations in contract dod_evidence check_value entries "
            "(contracts carry no run_timestamp, so OMN_15710_CUTOFF cannot "
            "gate them)."
        ),
    )
    args = parser.parse_args(argv)
    contracts_dir = Path(args.contracts_dir)
    baseline_path = Path(args.supersession_baseline)
    contract_abs_path_baseline = load_contract_abs_path_baseline(
        Path(args.contract_abs_path_baseline)
    )

    if args.write_supersession_baseline:
        return write_supersession_baseline(
            Path(args.receipts_root), contracts_dir, baseline_path
        )

    if args.check_supersession_wiring:
        ci_yaml = Path(args.ci_yaml)
        failures = check_supersession_wiring(ci_yaml)
        if failures:
            print(
                f"SUPERSESSION BINDING WIRING GATE FAILED ({ci_yaml}) "
                f"[{SUPERSESSION_TICKET}]:"
            )
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print(f"SUPERSESSION BINDING WIRING GATE PASSED ({ci_yaml})")
        return 0

    if args.supersession_corpus:
        return run_supersession_corpus(
            Path(args.receipts_root), contracts_dir, baseline_path
        )

    supersession_baseline = load_supersession_baseline(baseline_path)
    all_violations: list[str] = []
    for file_arg in args.files:
        path = Path(file_arg)
        if not path.is_file():
            continue  # deleted/renamed paths are not this gate's concern
        all_violations.extend(
            _check_staged_file(
                path, contracts_dir, supersession_baseline, contract_abs_path_baseline
            )
        )

    if all_violations:
        print(f"Receipt hardening gate: {len(all_violations)} violation(s):\n")
        for violation in all_violations:
            print(f"  {violation}")
        print(
            "\nFix the receipt (tool-generate; bind the current contract hash); "
            "never bypass the gate."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
