# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Whether an evidence item's checks can prove the criterion it binds.

The acceptance-criterion binding gate could already ask two questions of a
``binds_ac`` claim: does the ticket HAVE that criterion, and is the binding
pinned to the criterion as it reads now. It could not ask the third one, and
said so in its own words: *"it cannot tell whether the item's checks actually
prove the criterion its label names."*

This module answers a decidable slice of that third question. Not "is this the
right proof" -- that stays authorial judgement -- but the one case where the
answer is mechanical:

    An item whose every check reads the repository tree cannot prove a
    criterion that can only be settled by measuring a running system.

A source grep answers *"was this code written"*. A criterion that says a
consumer reads ``FLOWING`` on the live dev lane, or that a topic's
high-watermark advanced, asks *"what is the system doing"*. No amount of the
first is any amount of the second, and the two are distinguishable from text
alone.

Why this is not the runtime ``proof_class``
-------------------------------------------
``node_dod_verify`` computes a ``proof_class`` per check at VERIFY time
(``behavior`` / ``readback`` / ``merge-state`` / ``surrogate``), and the
evidence closer reads it -- it refuses a readback-only binding on a criterion
that is not state-shaped. That classifier sees a check's execution. This one
sees only the contract's YAML, at AUTHORING time, before anything has run. It
is the authoring-time sibling of that idea, deliberately narrower, and it fires
where the closer's carve-out does not reach: a ``command`` check that greps
merged source classifies as neither ``readback`` nor anything the closer
refuses, so a source grep bound to a live-lane criterion passes every gate in
the fleet today.

The two asymmetries, stated rather than implied
-----------------------------------------------
**The evidence side fails OPEN on an unknown check.** An item is ``static``
only when EVERY check is provably static. A ``command`` this module cannot read
makes the item non-static, and non-static never refuses. The opposite reading --
unknown means static -- would refuse most of the corpus on commands the
classifier simply does not understand, which is a false-refusal machine, not a
gate. What this costs is stated in the honest limit below.

**The criterion side fails CLOSED on its inputs.** A criterion is ``live`` when
its own text or its declared falsifier names a probe surface. The vocabularies
below are the definition, in code, tested -- not a heuristic tuned until the
corpus went quiet.

The honest limit
----------------
This refuses a proof that is provably the wrong KIND. It cannot tell a right
kind of proof that is wrong on the facts, it cannot read a shell command it has
no vocabulary for, and it says nothing about a criterion that names no probe
surface. What it removes is the silent case: one grep, bound to four live-lane
measurements, one of which was false at the moment it was bound.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "LIVE_MEASUREMENT_TERMS",
    "LIVE_PROBE_COMMAND_MARKERS",
    "SOURCE_READ_COMMAND_MARKERS",
    "STATIC_CHECK_TYPES",
    "TEST_SETTLED_FALSIFIER_MARKERS",
    "criterion_is_live",
    "item_is_static",
    "live_terms_in",
    "static_check_reason",
]

# -- the criterion side -------------------------------------------------------

#: Terms whose presence in a criterion (or in its declared falsifier) means the
#: criterion is settled by MEASURING A RUNNING SYSTEM rather than by reading the
#: repository.
#:
#: Curated, not swept. Each entry names a probe surface a person has to point an
#: instrument at: a lane, a consumer or its bus position, a materialised
#: projection row, a workflow run, a deployed revision, a health verdict, or one
#: of the four commands anybody in this fleet reaches for to ask a live question.
#: A term that merely SOUNDS operational and can be satisfied by reading a file
#: is deliberately absent -- "contract", "handler", "topic" on its own, "gate".
#:
#: Matched on WORD BOUNDARIES over the casefolded text, so `live` does not match
#: `delivered` and `lag` does not match `flagged`. Multi-word entries match with
#: their internal whitespace flexible, so a criterion that line-wraps between
#: "consumer" and "group" is read the same as one that does not.
#: DELIBERATELY ABSENT, each measured against the live corpus and rejected:
#: ``runtime``, ``window``, ``broker``, ``partition``, ``subscription``,
#: ``restart`` and ``receipt``. Every one of them appears in ordinary prose
#: ABOUT a hermetic test -- "a unit test derives the expected count from the
#: served model", "a red-first test pins the defect against a broker client" --
#: and each produced a false live verdict on a criterion a test settles. A term
#: that a test-shaped criterion can carry is not a probe surface; it is a noun.
LIVE_MEASUREMENT_TERMS: tuple[str, ...] = (
    # the lane / the running system
    "live",
    "lane",
    "dev lane",
    "stability-test",
    "cluster",
    "namespace",
    "deployed",
    "post-deploy",
    # bus position -- the classic un-fakeable facts
    "consumer group",
    "consumer_group",
    "high-watermark",
    "high watermark",
    "hwm",
    "lag",
    "offset",
    "flowing",
    "stalled",
    # materialised state
    "projection",
    "rows in",
    "row count",
    "materialized",
    "materialised",
    "readback",
    "read back",
    # runs and health
    "run id",
    "run_id",
    "healthy",
    "unhealthy",
    "health check",
    "health probe",
    # the instruments themselves
    "kubectl",
    "psql",
    "rpk",
    "docker",
)

#: Markers that a criterion's FALSIFIER settles it by running a test or a
#: hermetic command rather than by measuring a system. A falsifier carrying one
#: of these and no live term means the criterion is test-settled, whatever
#: operational nouns its prose happens to use.
#:
#: This is a counter-signal, never an override: a falsifier that says "run the
#: suite AND read the lane back" carries both, and a criterion that names a
#: probe surface stays live. Ordering matters and is asserted in the tests.
TEST_SETTLED_FALSIFIER_MARKERS: tuple[str, ...] = (
    "pytest",
    "uv run",
    "npm test",
    "npm run",
    "a unit test",
    "the unit test",
    "a test",
    "the test",
    "red-first",
    "red first",
    "a fixture",
    "the fixture",
    "assert",
)

#: One compiled alternation, longest-first so `consumer group` wins over `lag`
#: in a criterion carrying both and the reported term is the most specific one.
_LIVE_TERM_RE = re.compile(
    r"(?<![\w-])(?:"
    + "|".join(
        re.escape(term).replace(r"\ ", r"\s+")
        for term in sorted(LIVE_MEASUREMENT_TERMS, key=len, reverse=True)
    )
    + r")(?![\w-])",
    re.IGNORECASE,
)


def live_terms_in(text: str) -> list[str]:
    """Every live-measurement term ``text`` carries, in the order it carries them.

    Returned rather than a bare boolean so a refusal can QUOTE what made the
    criterion live. A gate that said "this criterion is live" without naming the
    word it read sends the author hunting through the vocabulary.
    """
    seen: list[str] = []
    for match in _LIVE_TERM_RE.finditer(text):
        term = " ".join(match.group(0).split()).casefold()
        if term not in seen:
            seen.append(term)
    return seen


def criterion_is_live(criterion: str, falsifier: str | None = None) -> bool:
    """True when this criterion can only be settled by measuring a live system.

    THE FALSIFIER IS THE AUTHORITY, and the criterion prose is the fallback.
    The falsifier is the author saying, before any outcome was known, what would
    settle this. When it names a probe surface the criterion is live no matter
    how abstract its prose ("a reducer that publishes reports messages_out > 0"
    -- settled on the lane). When it names a test run and no probe surface, the
    criterion is test-settled no matter how operational its prose sounds -- the
    case that produced every false verdict the vocabulary above was narrowed to
    remove.

    Only when there is no falsifier at all does the criterion's own text decide.
    """
    falsifier_terms = live_terms_in(falsifier or "")
    if falsifier_terms:
        return True
    if falsifier and _is_test_settled(falsifier):
        return False
    return bool(live_terms_in(criterion))


def _is_test_settled(falsifier: str) -> bool:
    folded = falsifier.casefold()
    return any(marker in folded for marker in TEST_SETTLED_FALSIFIER_MARKERS)


# -- the evidence side --------------------------------------------------------

#: Check types that are STATIC by their type alone, whatever their value says.
#:
#: ``file_exists`` is `omnibase_core`'s entire ``WEAK_PROOF_CHECK_TYPES``
#: (``model_dod_receipt.py``), where a passing one is downgraded to ADVISORY on
#: the reasoning that a file's presence proves a write happened, never that the
#: behaviour happened. ``grep`` and ``test_exists`` are the same fact in two
#: other spellings: a pattern found in the tree, and a test file that exists
#: without being run.
STATIC_CHECK_TYPES: frozenset[str] = frozenset({"file_exists", "grep", "test_exists"})

#: Substrings in a ``command`` check that mean it reaches a running system, a
#: workflow run, or an interpreter. Any one of these makes the check NOT static,
#: whatever else the command also does.
LIVE_PROBE_COMMAND_MARKERS: tuple[str, ...] = (
    "psql",
    "docker",
    "kubectl",
    "rpk ",
    "ssh ",
    "aws ",
    "curl",
    "wget",
    "redis-cli",
    "pytest",
    "uv run",
    "python ",
    "python3 ",
    "npm ",
    "node ",
    "make ",
    "onex ",
    "/actions/",
    "gh run ",
    "gh workflow",
    "gh api graphql",
)

#: Substrings in a ``command`` check that mean it reads the repository tree or
#: GitHub's record OF that tree. A command carrying one of these and none of the
#: markers above is source-static: it can answer what was written, never what is
#: running.
SOURCE_READ_COMMAND_MARKERS: tuple[str, ...] = (
    "grep",
    " rg ",
    "git show",
    "git diff",
    "git log",
    "git cat-file",
    "git rev-parse",
    "base64 -d",
    "base64 --decode",
    "/contents/",
    "contents/",
    "/pulls/",
    "gh pr view",
    "gh pr diff",
    "gh pr list",
    "test -f",
    "test -e",
    "cat ",
    "head -",
    "tail -",
    "wc -l",
    "ls ",
    "find ",
)


def _command_text(check: Mapping[str, object]) -> str:
    raw = check.get("check_value")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        # A `grep` check's dict value -- pattern and path, both tree reads.
        return " ".join(str(value) for value in raw.values())
    return ""


def static_check_reason(check: Mapping[str, object]) -> str | None:
    """Why this ONE check is static, or ``None`` when it is not (or unknown).

    ``None`` is the fail-OPEN answer described in the module docstring: it
    covers both "this check executes something" and "this command uses a
    vocabulary I do not have", and neither ever produces a refusal.
    """
    check_type = str(check.get("check_type") or "").strip().lower()
    if check_type in STATIC_CHECK_TYPES:
        return f"check_type {check_type!r} reads the tree without running it"
    if check_type != "command":
        # test_passes, endpoint, behavior_proven, semantic_grading: each either
        # executes something or is an attestation this module does not judge.
        return None
    command = _command_text(check)
    folded = command.casefold()
    if any(marker in folded for marker in LIVE_PROBE_COMMAND_MARKERS):
        return None
    hits = [marker for marker in SOURCE_READ_COMMAND_MARKERS if marker in folded]
    if not hits:
        return None
    return (
        "the command only reads the repository tree "
        f"({', '.join(repr(hit.strip()) for hit in hits[:3])}) "
        "and contacts no lane, run or projection"
    )


def item_is_static(checks: Sequence[Mapping[str, object]]) -> tuple[bool, list[str]]:
    """``(is_static, reasons)`` for a whole evidence item.

    An item is static only when it HAS checks and EVERY one of them is static.
    An item with no checks at all is NOT static here: it proves nothing, which
    is a different defect that other rules own, and refusing it from this rule
    would report a proof-class mismatch where none was measured.
    """
    if not checks:
        return False, []
    reasons: list[str] = []
    for check in checks:
        reason = static_check_reason(check)
        if reason is None:
            return False, []
        reasons.append(reason)
    return True, reasons


def all_static(checks: Iterable[Mapping[str, object]]) -> bool:
    """Convenience boolean for callers that do not need the reasons."""
    return item_is_static(list(checks))[0]
