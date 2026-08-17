# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""OCC-autobind trigger predicate with self-companion exclusion (OMN-15334).

WHAT THIS FIXES
---------------
On 2026-07-28 a single product PR (``OmniNode-ai/omninode_infra#735``,
OMN-15332) minted a self-recursive cascade of OCC companions: ``#5312`` ->
``#5354``, one link every ~20-35s. A second cascade (OMN-15338) seeded from
``#5355`` and was still growing at ``#5362`` when this module was written. The
loop is:

1. A product PR opens; autobind mints OCC companion **A**.
2. **A** is itself a PR in ``onex_change_control``, so the OCC-resident callers
   (``.github/workflows/call-occ-autobind.yml`` /
   ``call-occ-companion-effect.yml``, wired by OMN-15261) fire on it and mint a
   companion **B** *for the companion*.
3. Repeat forever. Nothing in the loop is self-excluding, so termination is by
   attrition, not by a bound.

The OMN-15247 "no RED-derivable candidate" guard closes each link, so the
cascade is pure waste: a full OCC gate matrix per link, an orphaned open PR per
cascade, and a dangling ``Evidence-Source:`` on the product PR pointing at a
companion the emitter then destroyed.

THE FIX IS SELF-EXCLUSION, NOT A DEPTH CAP
------------------------------------------
A depth cap still burns N runs and still leaves the last link orphaned; it
treats the symptom. This module is the *predicate* the OCC callers consult
before publishing an autobind command: a PR whose only content is companion
evidence for another PR never gets a companion of its own. There is
deliberately **no depth counter here** — depth is not the invariant, "evidence
PRs do not get evidence PRs" is.

WHY SUPPRESSING EVIDENCE PRs CANNOT STRAND THEM (live evidence)
---------------------------------------------------------------
``occ-preflight / eligibility`` does not require an autobind companion on an OCC
evidence PR. 293 of the 300 most recent ``onex_change_control`` PRs are
``evidence(...)`` companions, and every one of them merged while OCC had *no*
autobind caller at all (the caller landed on OCC ``dev`` at 2026-07-28T19:06:40Z,
commit ``3a712963``). Spot-verified on two pre-caller companions that never had
a companion of their own: ``#5305`` and ``#5311``, both
``occ-preflight / eligibility = pass``. So the suppression this module performs
restores the exact state under which those 293 PRs merged cleanly.

SIGNALS (three, any one sufficient)
-----------------------------------
1. **Machine companion head ref.** Both machine producers author on
   ``auto/<repo-slug>-pr-<n>-occ-autobind``:
   ``omnimarket/src/omnimarket/nodes/node_pr_lifecycle_fix_effect/handlers/occ_companion_emitter.py:442``
   and
   ``omnimarket/src/omnimarket/nodes/node_occ_companion_compute/handlers/handler_occ_companion_compute.py:497``.
   Mirrors ``omnimarket/src/omnimarket/occ_contention.py:95``.
2. **Evidence-companion title.** Every OCC evidence PR title renderer emits
   ``evidence(<TICKET>): ...``:
   ``handler_occ_companion_effect.py:576`` (``OCC companion for ...``),
   ``occ_companion_emitter.py:1733`` (``OCC Evidence-Source autobind for ...``),
   ``occ_companion_emitter.py:767`` / ``:880`` (comma-joined multi-ticket forms),
   ``handler_occ_observation_effect.py:159`` (``OCC observation append ...``).
   Signal 1 alone is NOT sufficient: ``#5325``
   (``codex-a/omn-15332-infra-735-occ-replacement``) and ``#5342``
   (``jonah/omn-15332-occ-companion``) are hand-authored companions on human
   head refs, and the emitter companioned both anyway (``#5327``, ``#5344``).
   Signal 2 is what catches them, and it is what keeps the OMN-15323
   observation-store re-arm from seeding a cascade on every emission.
3. **``occ:machine-minted`` label.** The authoritative ``minted_by_node``
   marker (``omnimarket/src/omnimarket/events/occ_autoauthor.py``). It is
   applied best-effort *after* the PR is opened, so it is a redundant third
   belt, never the primary signal.

REJECTED ALTERNATIVE: inspecting the PR's changed files ("only contracts/ +
evidence/ + receipts") would be the most literal reading of "content is only
companion evidence", but it costs a ``/pulls/{n}/files`` API call inside a
trigger guard. A transport failure then has to choose between failing open
(cascade returns) and failing closed (legitimate companions silently stranded,
the OMN-15022 signature). The title convention is deterministic, offline, and
293/300 accurate on live data, so the guard stays metadata-only.

RUNTIME CONSTRAINTS
-------------------
This module is intentionally **stdlib-only and imports nothing from its own
package**. ``import onex_change_control`` pulls pydantic + omnibase_core and
takes ~3.4s; a loop-breaker must not inherit that dependency surface. The CI
guard therefore runs this file *by path* on a bare interpreter
(``python src/onex_change_control/scripts/check_occ_autobind_trigger.py``) while
tests import it normally. Same file, same code, both paths proven.

Ticket: OMN-15334 (parent OMN-15261; lineage OMN-15247, OMN-15022, OMN-13976)
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# --- Seam constants ----------------------------------------------------------

#: Head-ref shape both machine producers author on. Pinned to
#: ``occ_companion_emitter.py:442`` / ``handler_occ_companion_compute.py:497``,
#: which build ``f"auto/{repo_slug.lower()}-pr-{pr_number}-occ-autobind"``.
#: Anchored at both ends deliberately: if a producer ever changes the shape the
#: evidence-title signal still closes the loop, so a miss here degrades to a
#: second belt rather than to a cascade.
MACHINE_COMPANION_BRANCH_RE: re.Pattern[str] = re.compile(r"^auto/.+-occ-autobind$")

#: OCC evidence-companion title convention: ``evidence(<TICKET>[, <TICKET>]): ...``.
#: ``[^)]*OMN-\d+[^)]*`` covers the comma-joined multi-ticket renderers at
#: ``occ_companion_emitter.py:767`` and ``:880``.
EVIDENCE_COMPANION_TITLE_RE: re.Pattern[str] = re.compile(
    r"^evidence\([^)]*OMN-\d+[^)]*\)\s*:"
)

#: Authoritative node-minted marker; see ``omnimarket.events.occ_autoauthor``.
OCC_MACHINE_MINTED_LABEL = "occ:machine-minted"

#: Ticket token every autobind-eligible PR must carry (title or head ref).
TICKET_PREFIX = "OMN-"

#: Dependency bots, exempt from OCC evidence entirely (OMN-13762). Both the gh
#: CLI login form and the raw event-payload form.
DEPENDENCY_BOT_ACTORS: frozenset[str] = frozenset(
    {
        "dependabot",
        "dependabot[bot]",
        "app/dependabot",
        "renovate",
        "renovate[bot]",
        "app/renovate",
    }
)

#: Env vars the CI guard reads. Every one MUST be set by the caller workflow —
#: an empty value is legal, an *absent* one is broken wiring and fails loud
#: (CLAUDE.md rule 8: never silently default).
REQUIRED_ENV_VARS: tuple[str, ...] = (
    "OCC_TRIGGER_EVENT_NAME",
    "OCC_TRIGGER_ACTOR",
    "OCC_TRIGGER_PR_TITLE",
    "OCC_TRIGGER_HEAD_REF",
    "OCC_TRIGGER_LABELS_JSON",
)

#: GITHUB_OUTPUT key the caller workflow gates its publish job on.
SHOULD_PUBLISH_OUTPUT_NAME = "should_publish"


@unique
class EnumOccAutobindTriggerVerdict(str, Enum):
    """Why the OCC autobind trigger did or did not publish for one PR."""

    PUBLISH = "publish"
    """Ordinary product PR: mint the companion."""

    SKIP_NOT_PULL_REQUEST = "skip_not_pull_request"
    """Not a ``pull_request`` event; the born path has no PR to bind."""

    SKIP_DEPENDENCY_BOT = "skip_dependency_bot"
    """Trusted dependency bot; exempt from OCC evidence (OMN-13762)."""

    SKIP_NO_TICKET_REFERENCE = "skip_no_ticket_reference"
    """No ``OMN-`` token in title or head ref; fixed by the pr-title gate."""

    SKIP_MACHINE_COMPANION_BRANCH = "skip_machine_companion_branch"
    """Head ref is a machine companion branch (``auto/*-occ-autobind``)."""

    SKIP_EVIDENCE_COMPANION_TITLE = "skip_evidence_companion_title"
    """Title is the ``evidence(<TICKET>):`` companion-evidence convention."""

    SKIP_MACHINE_MINTED_LABEL = "skip_machine_minted_label"
    """Carries the ``occ:machine-minted`` marker label."""


#: Verdicts that mean "this PR is itself companion evidence for another PR".
SELF_COMPANION_VERDICTS: frozenset[EnumOccAutobindTriggerVerdict] = frozenset(
    {
        EnumOccAutobindTriggerVerdict.SKIP_MACHINE_COMPANION_BRANCH,
        EnumOccAutobindTriggerVerdict.SKIP_EVIDENCE_COMPANION_TITLE,
        EnumOccAutobindTriggerVerdict.SKIP_MACHINE_MINTED_LABEL,
    }
)


@dataclass(frozen=True)
class ModelOccAutobindTriggerDecision:
    """The trigger decision for one PR event, with its stated reason."""

    verdict: EnumOccAutobindTriggerVerdict
    reason: str

    @property
    def should_publish(self) -> bool:
        """True iff the autobind command should be published for this PR."""
        return self.verdict is EnumOccAutobindTriggerVerdict.PUBLISH

    @property
    def is_self_companion(self) -> bool:
        """True iff the skip was because the PR is itself companion evidence."""
        return self.verdict in SELF_COMPANION_VERDICTS


def is_machine_companion_branch(head_ref: str) -> bool:
    """Pure: True iff ``head_ref`` is a machine-minted OCC companion branch."""
    return bool(MACHINE_COMPANION_BRANCH_RE.match(head_ref.strip()))


def is_evidence_companion_title(pr_title: str) -> bool:
    """Pure: True iff ``pr_title`` follows the OCC evidence-companion convention."""
    return bool(EVIDENCE_COMPANION_TITLE_RE.match(pr_title.strip()))


def is_machine_minted(labels: Iterable[str]) -> bool:
    """Pure: True iff the PR carries the node-minted marker label."""
    return OCC_MACHINE_MINTED_LABEL in {label.strip() for label in labels}


def decide_occ_autobind_trigger(
    *,
    event_name: str,
    actor: str,
    pr_title: str,
    head_ref: str,
    labels: Sequence[str] = (),
) -> ModelOccAutobindTriggerDecision:
    """Decide whether the OCC autobind command should be published for one PR.

    The single decision point for both OCC callers. Ordered most-specific-last
    so the reported reason is the most informative one available: transport and
    eligibility skips first, then the three self-companion signals.
    """
    if event_name != "pull_request":
        return ModelOccAutobindTriggerDecision(
            verdict=EnumOccAutobindTriggerVerdict.SKIP_NOT_PULL_REQUEST,
            reason=(
                f"event {event_name!r} is not 'pull_request'; the OCC born path "
                f"binds a PR and has nothing to act on."
            ),
        )

    if actor.strip().lower() in DEPENDENCY_BOT_ACTORS:
        return ModelOccAutobindTriggerDecision(
            verdict=EnumOccAutobindTriggerVerdict.SKIP_DEPENDENCY_BOT,
            reason=(
                f"actor {actor!r} is a trusted dependency bot; dependency-bump PRs "
                f"carry no Linear ticket and are exempt from OCC evidence (OMN-13762)."
            ),
        )

    if TICKET_PREFIX not in pr_title and TICKET_PREFIX not in head_ref:
        return ModelOccAutobindTriggerDecision(
            verdict=EnumOccAutobindTriggerVerdict.SKIP_NO_TICKET_REFERENCE,
            reason=(
                "neither the PR title nor the head ref carries an 'OMN-' ticket "
                "token; a ticketless PR is fixed by the pr-title gate, never "
                "papered over with a mechanical companion."
            ),
        )

    if is_machine_companion_branch(head_ref):
        return ModelOccAutobindTriggerDecision(
            verdict=EnumOccAutobindTriggerVerdict.SKIP_MACHINE_COMPANION_BRANCH,
            reason=(
                f"head ref {head_ref!r} is a machine-minted OCC companion branch "
                f"(auto/*-occ-autobind). This PR IS the evidence; companioning it "
                f"is the OMN-15334 self-recursive cascade."
            ),
        )

    if is_evidence_companion_title(pr_title):
        return ModelOccAutobindTriggerDecision(
            verdict=EnumOccAutobindTriggerVerdict.SKIP_EVIDENCE_COMPANION_TITLE,
            reason=(
                f"title {pr_title!r} follows the OCC evidence-companion convention "
                f"'evidence(<TICKET>): ...'; its content is companion evidence for "
                f"another PR, which never needs companion evidence of its own "
                f"(OMN-15334)."
            ),
        )

    if is_machine_minted(labels):
        return ModelOccAutobindTriggerDecision(
            verdict=EnumOccAutobindTriggerVerdict.SKIP_MACHINE_MINTED_LABEL,
            reason=(
                f"PR carries the {OCC_MACHINE_MINTED_LABEL!r} marker label; it was "
                f"minted by a machine producer and is companion evidence, not a "
                f"product change (OMN-15334)."
            ),
        )

    return ModelOccAutobindTriggerDecision(
        verdict=EnumOccAutobindTriggerVerdict.PUBLISH,
        reason="product PR with a ticket token and no self-companion signal.",
    )


# --- CI entrypoint -----------------------------------------------------------


def _single_line(value: str) -> str:
    """Collapse a value to one line so it is safe as a ``GITHUB_OUTPUT`` value."""
    return " ".join(value.split())


def _parse_labels(raw: str) -> list[str]:
    """Parse the ``toJSON(...labels.*.name)`` payload; tolerate an empty value.

    Labels are the redundant third signal, so a malformed payload degrades to
    "no labels" with a warning rather than failing the guard. The head-ref and
    title signals still decide.
    """
    text = raw.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        print(
            f"::warning::OCC autobind guard: OCC_TRIGGER_LABELS_JSON is not valid "
            f"JSON ({text!r}); continuing on the head-ref and title signals only.",
            file=sys.stderr,
        )
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _write_github_output(decision: ModelOccAutobindTriggerDecision) -> None:
    """Write the decision to ``GITHUB_OUTPUT`` when running inside Actions."""
    output_path = os.environ.get("GITHUB_OUTPUT", "")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(
            f"{SHOULD_PUBLISH_OUTPUT_NAME}={str(decision.should_publish).lower()}\n"
        )
        handle.write(f"verdict={decision.verdict.value}\n")
        handle.write(f"reason={_single_line(decision.reason)}\n")


def main() -> int:
    """CI entrypoint: decide, log the decision loudly, emit the job output.

    Exits 0 for both publish and skip — this guard is additive and must never
    fail a PR. It exits 1 only when a required env var is *absent*, which means
    the caller workflow's wiring is broken; that is a loud, non-blocking red
    signal on a non-required workflow, never a silent no-op.
    """
    missing = [name for name in REQUIRED_ENV_VARS if name not in os.environ]
    if missing:
        print(
            f"::error::OCC autobind guard: required environment variable(s) "
            f"{', '.join(missing)} are not set. The caller workflow's env block is "
            f"out of sync with this predicate (OMN-15334 seam).",
            file=sys.stderr,
        )
        return 1

    decision = decide_occ_autobind_trigger(
        event_name=os.environ["OCC_TRIGGER_EVENT_NAME"],
        actor=os.environ["OCC_TRIGGER_ACTOR"],
        pr_title=os.environ["OCC_TRIGGER_PR_TITLE"],
        head_ref=os.environ["OCC_TRIGGER_HEAD_REF"],
        labels=_parse_labels(os.environ["OCC_TRIGGER_LABELS_JSON"]),
    )

    # A skip is always announced with its reason. A silent green no-op is the
    # OMN-15022 signature and is explicitly not acceptable here.
    if decision.should_publish:
        print(
            f"::notice::OCC autobind guard: PUBLISH — {_single_line(decision.reason)}"
        )
    else:
        print(
            f"::notice::OCC autobind guard: SKIP [{decision.verdict.value}] — "
            f"{_single_line(decision.reason)}"
        )

    print(
        json.dumps(
            {
                "should_publish": decision.should_publish,
                "verdict": decision.verdict.value,
                "reason": _single_line(decision.reason),
                "is_self_companion": decision.is_self_companion,
            },
            indent=2,
            sort_keys=True,
        )
    )

    _write_github_output(decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
