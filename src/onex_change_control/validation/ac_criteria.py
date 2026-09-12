# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18236 — reading a ticket's acceptance criteria, and pinning one by hash.

A `binds_ac` entry names a criterion. Until now nothing checked that the
criterion it names EXISTS, and nothing noticed when the criterion's text was
rewritten under a passing check. Both are the same missing fact: the binding
points at a moving target and has no way to say which revision it was accepted
against.

This module supplies the two primitives that close that.

**The criterion reader.** Which lines of a ticket body are its acceptance
criteria, and what label each carries. This is a DELIBERATE PORT of the
evidence closer's own reader, at `omnibase_infra`
`src/omnibase_infra/nodes/node_evidence_autoclose_sweep_effect/handlers/handler_evidence_autoclose_sweep.py`
(`_is_ac_heading`, `_acceptance_criteria_items`, `_canonical_ac_label`). It is a
port and not an import because that module lives in a repository that depends on
this one, so importing it here would invert the layer graph. The two must agree:
a label this side accepts and that side does not is a binding the closer will
never see, and a criterion this side cannot read is one it will hash wrongly.
`tests/test_omn_18236_ac_criteria.py` pins the awkward shapes both sides were
measured against — `**AC1** - text`, `- AC-2: text`, `AC 3) text`, `DoD4 -- text`
— so a change on either side has a test naming the other.

Moving this pair into `omnibase_core`, which both repositories already depend
on, would remove the duplication outright. That is a release-and-repin cascade
across three repositories and is not attempted here; the coupling is stated
rather than hidden, which is the next best thing.

**The criterion hash.** A criterion's identity, so a binding can be pinned to
the revision it was accepted against. Whitespace and line wrapping are
canonicalized away, because re-flowing a paragraph is not a rewrite. Nothing
else is: a wording change, a negation, a changed threshold and a case change all
produce a different hash, and a binding pinned to the old one is STALE and
reverts to unproven until somebody re-accepts it. That is the whole point — it
is what stops a criterion from being quietly rewritten underneath a check that
is still passing.

**The honest limit, restated from the plan.** The hash detects a criterion that
CHANGED. It cannot detect a criterion that was wrong when it was written.
"""

from __future__ import annotations

import hashlib
import re

__all__ = [
    "MAX_CRITERION_HASH_INPUT_CHARS",
    "acceptance_criteria_items",
    "canonical_ac_label",
    "criteria_by_label",
    "criterion_hash",
    "is_ac_heading",
    "item_text",
    "normalise_criterion",
]

# -- the reader's regex set, ported verbatim from the closer ------------------

_LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(.*)$")
_AC_ITEM_RE = re.compile(
    r"^[ \t]*([*_]*)[ \t]*(AC[-_ ]?\d+)(?!\d)[*_]*(.*?)[ \t]*$", re.IGNORECASE
)
_TRAILING_EMPHASIS_RE = re.compile(r"[*_]+$")
_TRAILING_QUALIFIER_RE = re.compile(r"\s*\([^)]*\)\s*$")
_TASK_MARKER_RE = re.compile(r"^\[[ \t xX]\][ \t]*")
_HEADING_ENUM_RE = re.compile(r"^\d+[.)]\s*")
_AC_LABEL_RE = re.compile(
    r"^[\s>*_+-]*(?:\*\*)?\s*(AC|DOD)[-_ .]?(\d+)\b", re.IGNORECASE
)

_AC_HEADING_TEXTS = frozenset(
    {
        "acceptance criteria",
        "acceptance criteria (ac)",
        "acceptance criterion",
        "acceptance",
        "ac",
        "acs",
        "definition of done",
        "definition of done (dod)",
        "dod",
    }
)
#: Only multi-word spellings are eligible for the leading-qualifier match, so a
#: heading that merely ENDS in "ac" or "dod" ("## Notes on AC") does not open a
#: criteria section over unrelated content.
_AC_HEADING_PHRASES = frozenset(text for text in _AC_HEADING_TEXTS if " " in text)

_WHITESPACE_RUN_RE = re.compile(r"\s+")

#: Ceiling on the text fed to the hash. A criterion longer than this is
#: truncated before hashing, deterministically, so a pathological body cannot
#: make the hash depend on how much of it somebody pasted. Generous enough that
#: no real criterion reaches it.
MAX_CRITERION_HASH_INPUT_CHARS = 4000


def is_ac_heading(line: str) -> bool:
    """True when ``line`` reads as an acceptance-criteria heading.

    Tolerates ``## Acceptance Criteria``, ``**Acceptance criteria:**``,
    ``### 3. Acceptance criteria``, a trailing qualifier
    (``Acceptance criteria (falsifiable)``) and a leading one
    (``Falsifiable acceptance criteria``).
    """
    raw = line.strip()
    if not raw:
        return False
    looks_like_heading = raw.startswith("#") or (
        raw.startswith("**") and raw.endswith("**")
    )
    text = raw.lstrip("#").strip()
    text = text.strip("*_").strip()
    text = _HEADING_ENUM_RE.sub("", text)
    text = text.rstrip(":").strip()
    text = text.strip("*_").strip()
    folded = text.casefold()
    if folded in _AC_HEADING_TEXTS:
        return True
    trimmed = _TRAILING_QUALIFIER_RE.sub("", folded).strip().rstrip(":").strip()
    if trimmed in _AC_HEADING_TEXTS:
        return True
    return looks_like_heading and any(
        trimmed.endswith(f" {known}") for known in _AC_HEADING_PHRASES
    )


def item_text(line: str) -> str:
    """The criterion text ``line`` carries, or ``""`` when it carries none.

    One function so a criterion read inside a section and the same criterion
    read by the whole-body fallback below produce the IDENTICAL string. Two
    extractors would give one criterion two hashes, and a binding would go
    stale for no reason but which pass happened to find it.
    """
    list_match = _LIST_ITEM_RE.match(line)
    if list_match:
        return _TASK_MARKER_RE.sub("", list_match.group(1)).strip()
    ac_match = _AC_ITEM_RE.match(line)
    if not ac_match:
        return ""
    lead, token, rest = ac_match.groups()
    text = f"{token}{rest}".strip()
    if lead:
        text = _TRAILING_EMPHASIS_RE.sub("", text).strip()
    return text


def acceptance_criteria_items(description: str) -> list[str]:
    """Items listed under an acceptance-criteria heading in ``description``.

    A section runs from an acceptance-criteria heading to the next markdown
    heading. When the body carries NO recognised heading at all, the whole body
    is the section — the closer's own fallback, kept because a ticket that lists
    its criteria under a prose opener otherwise parses as having none.

    ONE DELIBERATE DIVERGENCE FROM THE CLOSER, measured 2026-09-12.
    -------------------------------------------------------------
    The closer BREAKS out of the scan at the first non-criteria heading, so it
    reads the FIRST criteria section and nothing after it. Measured against the
    67 live contracts that declare a binding: OMN-18186 carries AC1 to AC4 under
    ``## Acceptance criteria`` and AC5, AC6 under a later
    ``### Added acceptance criteria``, and the closer reads four. Its contract
    binds all six, so two real, author-written criteria read as fabricated.

    This reader CLOSES the section at a non-criteria heading and RE-OPENS at the
    next criteria heading, so every criteria section in the body is read. That
    direction is safe by the closer's own rule — over-reading holds a flip,
    under-reading releases one — and it is the difference between a gate that
    says "your ticket does not have AC5" and one that is right.

    The closer needs the same one-line change and does not have it yet; until it
    does, it under-reads exactly these tickets. That gap is reported rather than
    patched from here: this repository cannot edit that one, and a silent
    divergence would be worse than a stated one.
    """
    items: list[str] = []
    saw_ac_heading = any(is_ac_heading(line) for line in description.splitlines())
    in_section = not saw_ac_heading
    for line in description.splitlines():
        if is_ac_heading(line):
            in_section = True
            continue
        if not in_section:
            continue
        if line.lstrip().startswith("#") and saw_ac_heading:
            in_section = False
            continue
        text = item_text(line)
        if text:
            items.append(text)
    return items


def canonical_ac_label(text: str) -> str:
    """``AC3`` / ``DOD2`` parsed from a criterion or a binding entry, or ``""``.

    Both sides of the join go through this one function, so a contract writing
    ``ac-3`` and a ticket writing ``**AC3**`` bind, and neither side can
    normalise differently from the other.
    """
    match = _AC_LABEL_RE.match(text.strip())
    if not match:
        return ""
    return f"{match.group(1).upper()}{int(match.group(2))}"


def normalise_criterion(text: str) -> str:
    """The criterion text the hash is taken over.

    Whitespace runs collapse to one space and the ends are stripped, so
    re-wrapping a paragraph or re-indenting a bullet is not a rewrite. NOTHING
    ELSE is normalised — not case, not punctuation, not markdown emphasis —
    because each of those can change what a criterion requires.
    """
    return _WHITESPACE_RUN_RE.sub(" ", text).strip()[:MAX_CRITERION_HASH_INPUT_CHARS]


def criterion_hash(text: str) -> str:
    """The sha256 hex digest identifying this criterion's current revision."""
    return hashlib.sha256(normalise_criterion(text).encode("utf-8")).hexdigest()


def criteria_by_label(description: str) -> dict[str, str]:
    """``{label: criterion text}`` for every LABELLED criterion in ``description``.

    An unlabelled criterion is omitted rather than given a positional name: an
    ordinal derived from parse position renumbers every binding below it the
    moment a bullet is inserted, which would make every pin stale for a reason
    that has nothing to do with the criterion's text. A ticket whose criteria
    are unlabelled cannot be bound, and the gate says so in those words.

    First occurrence of a label wins. A body that labels two criteria ``AC1``
    is malformed on the ticket side; picking the first is stable, and the gate
    reports the duplicate rather than silently choosing.

    THE WHOLE-BODY FALLBACK, and why it is over-inclusive on purpose.
    ----------------------------------------------------------------
    Criteria sections are read first. Then any LABELLED criterion line the
    sections missed is picked up from anywhere in the body. Measured on the live
    corpus: OMN-17907 writes AC4 and AC5 under ``## Second finding, folded in``
    — a heading no reader will ever recognise as a criteria heading — and the
    ticket body itself says in as many words that they are full acceptance
    criteria rather than commentary. Section-only reading calls that contract's
    AC4 binding a claim on a criterion the ticket does not have, which is false.

    Over-inclusive is the right direction HERE specifically, and only here. This
    map answers two questions: does a claimed label EXIST, and what is its text.
    Being generous about WHERE the author wrote a criterion cannot let a
    fabricated label through — ``AC7`` still appears nowhere — it only stops the
    gate accusing an author of inventing a criterion they actually wrote.
    """
    resolved: dict[str, str] = {}
    for item in acceptance_criteria_items(description):
        label = canonical_ac_label(item)
        if not label or label in resolved:
            continue
        resolved[label] = item

    for raw in description.splitlines():
        text = item_text(raw)
        label = canonical_ac_label(text) if text else ""
        if not label or label in resolved:
            continue
        resolved[label] = text
    return resolved
