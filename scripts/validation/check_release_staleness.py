# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""dev-vs-tag release staleness gate (OMN-18010, deliverable 3).

WHAT FAILED, MEASURED
---------------------
Merged work keeps missing releases. A release in this org is a manual,
ticket-driven tag push; "Done" is measured at merge; and *nothing audits the
distance between ``dev`` and the last tag*. ``omnimarket#2304`` merged
2026-09-05 and sat unreleased with its release ticket in Backlog; ``#2334`` did
the same; a beta PRD scoring pass found five landed-but-not-deployed items at
once. Two repos (``omniintelligence``, ``omniclaude``) have been effectively
unreleased for three months without a single alarm.

This module is the third root cause's remedy: a gate, not a sweep. CLAUDE.md
rule 5 — a detection tool that is not a blocking check gets ignored — so this
is wired as a job whose context ``CI Summary`` asserts on every
``onex_change_control`` dev PR (``scripts/ci/ci_summary_gate.py``
``EXPECTED_EXTERNAL_CONTEXTS``), plus a daily schedule.

WHAT IT MEASURES
----------------
For each repo in the publishing set (``release_staleness_policy.yaml``):

* the latest ``vX.Y.Z`` tag, chosen by SEMVER ORDER, never by the API's
  return order (the tags endpoint is not sorted by version);
* the commits on ``dev`` that are not contained in that tag, via
  ``compare/{tag}...dev``;
* which of those touch a **packaged path** — the same set the release-on-merge
  trigger filters on (``src/``, ``pyproject.toml``, ``uv.lock``), so a
  docs-only merge is correctly not a release debt;
* the age of the OLDEST such commit.

A repo is STALE when it carries at least one unreleased packaged-source commit
whose age exceeds ``max_age_hours`` (default 24).

WHY NOT EVERY REPO BLOCKS ON DAY ONE
------------------------------------
This gate's context is asserted on every ``onex_change_control`` PR, and OCC is
the evidence-companion repo for the whole org: a red gate here stops every
product PR everywhere. Release-on-merge (deliverable 1) is armed on exactly one
repo today, so enforcing staleness on the other seven would wedge the org for a
condition nobody yet has a mechanism to clear — the release is still a manual
tag push. So the policy carries an explicit per-repo ``enforced`` flag: a repo
blocks once its automatic release exists. Until then it is measured, printed
and counted every single run as ``STALE_UNENFORCED``, and named in a
SHRINK-ONLY roster that ``--check-roster`` and
``tests/test_check_release_staleness.py`` refuse to let grow. Enforcement can
only widen; it is a ratchet, not a dial.

WHY THE PATH FILTER IS RE-DERIVED PER COMMIT, NOT READ OFF THE RANGE DIFF
------------------------------------------------------------------------
``compare``'s ``files`` array is the AGGREGATE diff of the whole range. It
answers "did anything packaged change across these 300 commits", which is not
the question — the question is *which commit* is the oldest packaged one, and
therefore how long the debt has been sitting. So the packaged set is resolved
by asking for the commits on ``dev`` touching that path since the range's own
earliest commit, and intersecting by sha with the compare set. The
intersection is what makes it exact: ``since`` alone would over-report commits
already contained in the tag.

Since OMN-19099 that ask is one batched GraphQL ``history(path:, since:)``
document covering every (repo, path) pair rather than one REST call each, but
the SHAPE of the question is unchanged and deliberately so. Batching was
allowed to change the transport and not the attribution: a per-commit answer
is still a per-commit answer. The cheaper route that reuses the ``compare``
payload already in hand was considered and REFUSED for the reason in the
paragraph above.

WHAT IS BATCHED, AND WHAT IS NOT (OMN-19099)
--------------------------------------------
This gate runs on every pull request in this repo and re-read the same roster
every time: 50 REST requests per run against the eight-repo roster, about
12,000 requests on 2026-09-21 between 12:00Z and 21:00Z, roughly 2,300 in the
18:00Z hour, against an installation ceiling of either 6,050 or 15,000 per
hour. It was 22% of every App-authenticated run in that window.

Batched: the tag reads (11 paginated REST calls became 1 GraphQL document,
2 when any repo carries more than 100 tags) and the packaged-path history
(24 REST calls became 1).

Measured together, against 50 before: **18 requests on the test fixture and
19 on the live roster.** Those two numbers differ for one reason and it is
worth stating rather than rounding away — the fixture gives every repo a
single tag, so its tag phase is one document, while the live roster's
omnimarket carries 148 and needs a second page. Both were measured, the
19 independently. Quoting only the 18 would understate the live cost and,
worse, would make the next reader compute the remaining headroom under
``MAX_REQUESTS_PER_SWEEP`` as 2 when it is actually 1.

The budget is a CEILING at 20 rather than a pin at the measured number, on
purpose. A pin would fail the moment any roster repo crossed 100 tags, which
is a tag-count change and not a batching regression, and the pressure would
then be to weaken the assertion. What actually catches de-batching is the
companion test asserting growth PER REPO, since a lost batch shows up as the
document count multiplying with the roster rather than as a single number
creeping.

NOT batched: ``compare``. Its truncation refusal — a collected commit count
short of the API's own ``total_commits`` refuses rather than under-reporting —
is the check that stops a capped range reading as a clean repo, and
re-implementing it against a different transport to save eight requests is a
bad trade in a gate that blocks the whole org.

FAILING CLOSED IS NOT THE SAME AS FAILING MUTELY (OMN-19098)
------------------------------------------------------------
Until 2026-09-21 every non-zero ``gh`` exit collapsed into one ``ProbeError``
shape, so an empty API quota was indistinguishable from a dead repo, a
malformed response or a real staleness finding. On 2026-09-21 the
``onexbot-occ-writer`` installation bucket (id 148180820) emptied at 19:41Z.
Four runs failed between 19:40:57Z and 19:43:32Z, each emitting seven or
eight per-repo ERROR rows, and the single infrastructure event was diagnosed
four separate times while it cascaded into ``CI Summary`` and blocked
unrelated merges. The measured cost of that conflation was about an hour.

So a quota refusal now has its own verdict (``QUOTA_EXHAUSTED``), its own
exception (``QuotaExhaustedError``) and its own machine-readable outcome
token (``OCC_QUOTA_EXHAUSTED``) printed on its own line in stdout, in the
JSON and in the step summary, with the bucket reset time where it can be
read.

Three things this deliberately does NOT do:

* It does not make quota exhaustion a pass. The exit code is still 1. A run
  that could not read the roster has not shown the roster is fresh.
* It does not retry. Retrying inside an exhausted window adds load to the
  saturated bucket, so a quota row also SUPPRESSES the positive control,
  which is a second full sweep that could not succeed anyway.
* It does not skip the job. ``CI Summary`` L4 layer is success-only and a
  skipped external context fails closed, so the job runs unconditionally on
  every pull request exactly as before.

FAIL-CLOSED, EVERYWHERE
-----------------------
Every unreadable repo, truncated compare, malformed tag list or unparseable
timestamp is an ERROR row and a non-zero exit. It is never a silent zero.
``compare`` caps its commit list, so a run whose collected commit count is
short of the API's own ``total_commits`` refuses rather than under-reporting —
that truncation is exactly the shape that would have reported ``omniclaude``'s
317-commit backlog as clean.

THE POSITIVE CONTROL IS NOT OPTIONAL
------------------------------------
An empty result is not evidence of absence (CLAUDE.md rule 16). A sweep that
errors, or whose path filter silently matches nothing, returns zero rows and
reads exactly like a clean bill of health. So when the real sweep finds zero
stale repos, this tool **automatically re-runs itself against a known-old base
ref** (each repo's earliest release tag) and refuses to report GREEN unless
that control produces rows. ``--positive-control`` runs that leg alone.

Deliberately stdlib-only apart from PyYAML (already a hard dependency of this
package) and the ``gh`` CLI, so it runs identically in CI, in a worktree, and
from the morning ground-state lane.

Usage::

    python3 scripts/validation/check_release_staleness.py
    python3 scripts/validation/check_release_staleness.py --json
    python3 scripts/validation/check_release_staleness.py --positive-control
    python3 scripts/validation/check_release_staleness.py --repo omnimarket

Exit codes:
    0 — every enforced repo is fresh (and the zero was proven by its control)
    1 — at least one enforced repo is stale, or the sweep could not see
        (including a quota refusal — see ``OCC_QUOTA_EXHAUSTED`` above, which
        changes what the run SAYS, never whether it blocks)
    2 — configuration/usage error (bad policy file, no ``gh``, bad arguments)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = _REPO_ROOT / "scripts" / "validation" / "release_staleness_policy.yaml"
DEFAULT_BASELINE = (
    _REPO_ROOT / ".onex_ratchets" / "omn_18010_release_staleness_unenforced_baseline.yaml"
)

ORG = "OmniNode-ai"

#: ``vX.Y.Z`` only. Anything else in the tag namespace (``v1.x`` governance
#: tags, rc/dev suffixes) is not a package release and must not be treated as
#: one — reading a non-release tag as the base is how a stale repo reports
#: fresh.
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

#: Verdicts. ``STALE_UNENFORCED`` is stale-and-recorded: it prints in the
#: table and is counted, but does not fail the run, because the repo has no
#: release-on-merge trigger yet and therefore no mechanism by which anyone
#: could keep it fresh. The roster of such repos is SHRINK-ONLY.
VERDICT_FRESH = "FRESH"
VERDICT_STALE = "STALE"
VERDICT_STALE_UNENFORCED = "STALE_UNENFORCED"
VERDICT_ERROR = "ERROR"

#: A read failed because the API quota for the acting identity was empty, not
#: because the repo is stale, unreadable or misconfigured. This is a DISTINCT
#: verdict rather than an ``ERROR`` row on purpose: on 2026-09-21 the
#: onexbot-occ-writer installation bucket emptied at 19:41Z and every roster
#: read in the same run failed together, which surfaced as seven independent
#: per-repo failures and was diagnosed four separate times. One infrastructure
#: event must read as one infrastructure event.
VERDICT_QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"

#: The machine-readable token a downstream consumer greps for. It is emitted
#: on its own line in stdout and in the step summary, so recognising a breadth
#: event never requires parsing a human sentence or reading a job log.
OUTCOME_QUOTA_EXHAUSTED = "OCC_QUOTA_EXHAUSTED"

#: GitHub's primary rate-limit refusal, as ``gh`` surfaces it on stderr. Both
#: spellings are real: an installation token says "for installation ID <n>", a
#: user token says "for user ID <n>". Matching the shared prefix catches both
#: without pinning an id. Verbatim sample (run 35646352206, 2026-09-21):
#:
#:   gh: API rate limit exceeded for installation ID 148180820. ... (HTTP 403)
_RATE_LIMIT_SIGNATURE = re.compile(r"API rate limit exceeded", re.IGNORECASE)

#: The secondary limit is a different mechanism (burst/abuse detection) with
#: the same operational meaning here: the identity cannot read right now, and
#: retrying immediately makes it worse.
_SECONDARY_LIMIT_SIGNATURE = re.compile(
    r"secondary rate limit|exceeded a secondary rate limit", re.IGNORECASE
)


class PolicyError(Exception):
    """Operator-facing misuse: unreadable or malformed policy file."""


class ProbeError(Exception):
    """A repo could not be read. Always an ERROR row, never a zero."""


class QuotaExhaustedError(ProbeError):
    """The acting identity API quota is empty. Distinct from ProbeError.

    WHY THIS SUBCLASSES ``ProbeError`` RATHER THAN SITTING BESIDE IT. Every
    existing ``except ProbeError`` site in this module is a fail-closed site:
    it turns an unreadable repo into a non-zero exit. Quota exhaustion must
    keep failing closed at every one of those sites, and a sibling class would
    have required editing each catch to re-establish that -- with any one
    missed catch silently becoming a pass. Subclassing makes the fail-closed
    posture the default and the distinct handling the explicit opt-in, which
    is the safe direction for this failure to be missed in.

    The type is still distinct: ``type(exc) is ProbeError`` is False, callers
    that care catch ``QuotaExhaustedError`` FIRST, and the verdict, the outcome
    token and the exit path all differ.

    ``reset_at`` is best-effort. It comes from the ``/rate_limit`` endpoint,
    which GitHub documents as not counting against the limit it reports, so
    reading it during an exhaustion costs nothing. When that read itself fails
    the field stays ``None`` and the report says the reset time is unknown
    rather than inventing one.
    """

    def __init__(
        self,
        message: str,
        *,
        reset_at: datetime | None = None,
        secondary: bool = False,
    ) -> None:
        super().__init__(message)
        self.reset_at = reset_at
        self.secondary = secondary

    @property
    def reset_detail(self) -> str:
        if self.reset_at is None:
            return "reset time unknown"
        return f"resets at {self.reset_at.astimezone(UTC).isoformat()}"


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoPolicy:
    """One publishing repo, and whether staleness there BLOCKS.

    ``enforced`` is the blast-radius control, and it is the honest one. This
    gate's context is asserted on every ``onex_change_control`` PR, and OCC is
    the evidence-companion repo for the whole org — so a red gate here stops
    every product PR everywhere. Enforcing staleness on a repo that has no
    release-on-merge trigger would therefore wedge the org for a condition
    nobody has a mechanism to clear: the release is still a manual tag push.

    So a repo is enforced once its automatic release exists. Until then it is
    measured, printed and counted every run, and named in a shrink-only roster
    (``.onex_ratchets/omn_18010_release_staleness_unenforced_baseline.yaml``)
    that a test refuses to let grow. Enforcement can only widen.
    """

    repo: str
    branch: str
    packaged_paths: tuple[str, ...]
    enforced: bool
    deferral_reason: str
    deferral_ticket: str


@dataclass(frozen=True)
class Policy:
    max_age_hours: float
    repos: tuple[RepoPolicy, ...]

    @property
    def unenforced(self) -> tuple[str, ...]:
        return tuple(repo.repo for repo in self.repos if not repo.enforced)


def _parse_ts(raw: object, where: str) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if not isinstance(raw, str):
        raise PolicyError(f"{where}: expected an ISO-8601 timestamp, got {raw!r}")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:  # pragma: no cover - message is the value
        raise PolicyError(f"{where}: unparseable timestamp {raw!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def load_policy(path: Path) -> Policy:
    """Read and validate the policy file. Every defect is exit 2, not a default."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(f"policy file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise PolicyError(f"policy file is not valid YAML: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError(f"policy file must be a mapping: {path}")

    max_age = raw.get("max_age_hours")
    if (
        not isinstance(max_age, (int, float))
        or isinstance(max_age, bool)
        or max_age <= 0
    ):
        raise PolicyError("policy: max_age_hours must be a positive number")

    repos_raw = raw.get("repos")
    if not isinstance(repos_raw, list) or not repos_raw:
        raise PolicyError("policy: repos must be a non-empty list")

    repos: list[RepoPolicy] = []
    seen: set[str] = set()
    for entry in repos_raw:
        if not isinstance(entry, dict):
            raise PolicyError(f"policy: repo entry must be a mapping, got {entry!r}")
        name = entry.get("repo")
        if not isinstance(name, str) or not name:
            raise PolicyError(f"policy: repo entry needs a repo name: {entry!r}")
        if name in seen:
            raise PolicyError(f"policy: repo {name} is declared twice")
        seen.add(name)
        paths = entry.get("packaged_paths")
        if (
            not isinstance(paths, list)
            or not paths
            or not all(isinstance(p, str) and p for p in paths)
        ):
            raise PolicyError(
                f"policy: {name}: packaged_paths must be a non-empty list of strings"
            )
        branch = entry.get("branch", "dev")
        if not isinstance(branch, str) or not branch:
            raise PolicyError(f"policy: {name}: branch must be a non-empty string")
        enforced = entry.get("enforced")
        if not isinstance(enforced, bool):
            raise PolicyError(
                f"policy: {name}: enforced must be an explicit true or false — "
                "a repo whose blast radius is left to a default is a repo nobody decided about"
            )
        reason = entry.get("deferral_reason", "")
        ticket = entry.get("deferral_ticket", "")
        if not enforced:
            if not isinstance(reason, str) or len(reason.strip()) < 20:
                raise PolicyError(
                    f"policy: {name}: enforced=false needs a substantive deferral_reason"
                )
            if not isinstance(ticket, str) or not re.fullmatch(r"OMN-\d+", ticket):
                raise PolicyError(
                    f"policy: {name}: enforced=false needs a deferral_ticket of the form OMN-XXXX"
                )
        elif reason or ticket:
            raise PolicyError(
                f"policy: {name}: enforced=true must not carry deferral_reason/deferral_ticket"
            )
        repos.append(
            RepoPolicy(
                repo=name,
                branch=branch,
                packaged_paths=tuple(paths),
                enforced=enforced,
                deferral_reason=reason if isinstance(reason, str) else "",
                deferral_ticket=ticket if isinstance(ticket, str) else "",
            )
        )

    return Policy(max_age_hours=float(max_age), repos=tuple(repos))


def load_unenforced_baseline(path: Path) -> tuple[str, ...]:
    """Frozen, shrink-only roster of repos whose staleness does not yet block."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(f"baseline file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise PolicyError(f"baseline file is not valid YAML: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError(f"baseline file must be a mapping: {path}")
    entries = raw.get("unenforced")
    if not isinstance(entries, list) or not all(isinstance(e, str) and e for e in entries):
        raise PolicyError(f"baseline: `unenforced` must be a list of repo names: {path}")
    return tuple(sorted(set(entries)))


def check_roster(policy: Policy, baseline: tuple[str, ...]) -> list[str]:
    """Ratchet: the unenforced roster may only SHRINK. Returns violation lines."""
    added = sorted(set(policy.unenforced) - set(baseline))
    stale_entries = sorted(
        set(baseline) - {repo.repo for repo in policy.repos}
    )
    violations = []
    for repo in added:
        violations.append(
            f"{repo}: newly marked enforced=false. The unenforced roster is shrink-only "
            "— a repo that already blocked may not stop blocking. Fix the staleness, "
            "do not widen the roster."
        )
    for repo in stale_entries:
        violations.append(
            f"{repo}: named in the baseline but absent from the publishing set — remove "
            "the stale baseline entry in the same change that removed the repo."
        )
    return violations


# ---------------------------------------------------------------------------
# GitHub reads
# ---------------------------------------------------------------------------


def _gh(args: list[str]) -> str:
    """Run ``gh`` and return stdout.

    stderr is NEVER discarded (CLAUDE.md rule 16): a suppressed error here
    yields zero rows and reads as a clean bill of health. It is captured and
    folded into the raised ``ProbeError`` so the finding names the reason.
    """
    global _REQUEST_COUNT
    _REQUEST_COUNT += 1
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise PolicyError("the `gh` CLI is required and was not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"gh {' '.join(args)}: timed out after 180s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")[:400]
        secondary = bool(_SECONDARY_LIMIT_SIGNATURE.search(detail))
        if secondary or _RATE_LIMIT_SIGNATURE.search(detail):
            raise QuotaExhaustedError(
                f"gh {' '.join(args)}: exit {proc.returncode}: {detail}",
                reset_at=_read_rate_limit_reset(),
                secondary=secondary,
            )
        raise ProbeError(f"gh {' '.join(args)}: exit {proc.returncode}: {detail}")
    return proc.stdout


def _read_rate_limit_reset() -> datetime | None:
    """Best-effort reset time for the exhausted bucket.

    GitHub documents ``/rate_limit`` as not counting against the limit it
    reports, so this is safe to call at the exact moment the bucket is empty
    -- which is the only moment it is useful.

    Never raises. A reset time we could not read is reported as unknown; an
    invented one would be worse than none, because a lane would wait on it.
    This deliberately does NOT go through ``_gh``: that function raises on a
    non-zero exit, and this helper is called from inside that raise path.

    A consequence of that, stated rather than left to be rediscovered: this
    call is therefore NOT counted in ``_REQUEST_COUNT``, so it does not appear
    in the sweep budget. That is correct on both counts -- ``/rate_limit`` does
    not spend the bucket it reports, and it is only ever reached on a sweep
    that has already failed, which is not a sweep the budget is measuring.
    """
    try:
        proc = subprocess.run(
            ["gh", "api", "rate_limit", "--jq", ".resources.core.reset"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw.isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


#: Every GitHub call this module makes goes through ``_gh`` or
#: ``_gh_graphql``, and both bump this. A GraphQL document counts as ONE
#: request no matter how many repos it covers, which is the entire point of
#: batching and therefore the thing the budget test has to be able to see.
#: Module-level rather than threaded through every signature because the
#: alternative is a counter argument on nine functions that exists only for a
#: test, and a count that is easy to bypass is not a budget.
_REQUEST_COUNT = 0

#: GitHub caps a GraphQL connection page at 100 nodes.
_GRAPHQL_PAGE = 100

#: A batched read that never stops paging is a hang in a required check, so
#: every pagination loop is bounded. Twenty rounds is 2,000 tags or commits
#: per repo, far past anything in this roster, and a roster that genuinely
#: needs more should fail loudly rather than spin.
_MAX_PAGES = 20


def request_count() -> int:
    """Total GitHub requests issued this process. Read by the budget test."""
    return _REQUEST_COUNT


def reset_request_count() -> None:
    global _REQUEST_COUNT
    _REQUEST_COUNT = 0


def _gh_graphql(document: str) -> dict[str, Any]:
    """POST one GraphQL document. ONE request, however many repos it spans.

    Errors are NOT flattened into a single failure. GitHub answers a partial
    document with data for the aliases that resolved and an ``errors`` array
    naming the ones that did not, so a single dead repo must not be allowed to
    read as eight dead repos -- that conflation is the same shape the sibling
    quota ticket exists to remove, and batching would reintroduce it by
    default if the caller just raised on any error.
    """
    out = _gh(["api", "graphql", "-f", f"query={document}"])
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"graphql: unparseable response: {out[:300]}") from exc
    if not isinstance(payload, dict):
        raise ProbeError(f"graphql: expected an object, got {type(payload).__name__}")
    return payload


def _graphql_alias_errors(payload: dict[str, Any], aliases: dict[str, str]) -> dict[str, str]:
    """Map GraphQL errors back to the repo whose alias they name.

    An error with no usable path is attributed to EVERY alias in the document
    rather than dropped: an unattributable failure means we do not know which
    repos were read, and silently treating them as read is how a blind sweep
    reports a clean bill of health.
    """
    found: dict[str, str] = {}
    for err in payload.get("errors") or []:
        message = str(err.get("message", "graphql error"))
        path = err.get("path") or []
        alias = str(path[0]) if path else ""
        if alias in aliases:
            found[aliases[alias]] = message
        else:
            for repo in aliases.values():
                found.setdefault(repo, message)
    return found


def _semver(tag: str) -> tuple[int, int, int]:
    match = RELEASE_TAG.match(tag)
    if match is None:  # pragma: no cover - callers pre-filter
        raise ProbeError(f"not a release tag: {tag}")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def fetch_release_tags(repos: Sequence[str]) -> dict[str, list[str] | str]:
    """Every ``vX.Y.Z`` tag for each repo, ascending by semver.

    ONE GraphQL document covers the whole roster, and a second only covers the
    repos that still have pages left. The eight-repo roster costs 2 requests
    where the per-repo REST paginate cost 11 (148/122/113/53/19/36/44/36 tags).

    Returns a per-repo result so a single unreadable repo stays a single ERROR
    row: the value is the ascending tag list, or an error string.

    SEMVER ORDER IS STILL APPLIED HERE, and still not taken from the API.
    GraphQL can order refs alphabetically or by tag commit date, and neither is
    semver -- alphabetically ``v0.4.99`` sorts above ``v0.4.183``. So this
    fetches every tag and sorts locally, exactly as the REST path did. Ordering
    by commit date and trusting the first page would be cheaper and is the
    mistake this docstring exists to refuse: the module contract says the base
    is chosen by semver order, never by the API return order.
    """
    collected: dict[str, list[str]] = {repo: [] for repo in repos}
    failed: dict[str, str] = {}
    cursors: dict[str, str | None] = {repo: None for repo in repos}
    pending = list(repos)

    for _ in range(_MAX_PAGES):
        if not pending:
            break
        aliases = {f"r{i}": repo for i, repo in enumerate(pending)}
        parts = []
        for alias, repo in aliases.items():
            cursor = cursors[repo]
            after = f', after: "{cursor}"' if cursor else ""
            parts.append(
                f'{alias}: repository(owner: "{ORG}", name: "{repo}") {{ '
                f'refs(refPrefix: "refs/tags/", first: {_GRAPHQL_PAGE}{after}) {{ '
                f"pageInfo {{ hasNextPage endCursor }} nodes {{ name }} }} }}"
            )
        payload = _gh_graphql("query { " + " ".join(parts) + " }")
        errors = _graphql_alias_errors(payload, aliases)
        data = payload.get("data") or {}

        still_pending: list[str] = []
        for alias, repo in aliases.items():
            if repo in errors:
                failed[repo] = errors[repo]
                continue
            node = data.get(alias)
            if not isinstance(node, dict):
                failed[repo] = "graphql returned no repository node"
                continue
            refs = node.get("refs") or {}
            for entry in refs.get("nodes") or []:
                name = str((entry or {}).get("name", "")).strip()
                if RELEASE_TAG.match(name):
                    collected[repo].append(name)
            page = refs.get("pageInfo") or {}
            if page.get("hasNextPage") and page.get("endCursor"):
                cursors[repo] = str(page["endCursor"])
                still_pending.append(repo)
        pending = still_pending
    else:
        if pending:
            raise ProbeError(
                f"graphql tag pagination exceeded {_MAX_PAGES} rounds for: "
                + ", ".join(pending)
            )

    result: dict[str, list[str] | str] = {}
    for repo in repos:
        if repo in failed:
            result[repo] = f"{repo}: {failed[repo]}"
        elif not collected[repo]:
            result[repo] = (
                f"{repo}: no vX.Y.Z tags exist — it has never published a release"
            )
        else:
            result[repo] = sorted(set(collected[repo]), key=_semver)
    return result


def list_release_tags(repo: str) -> list[str]:
    """Every ``vX.Y.Z`` tag for ONE repo, ascending by semver.

    The batched ``fetch_release_tags`` is the path a sweep takes. This single
    -repo form remains for the positive control, which measures exactly one
    repo and would gain nothing from a document of one.
    """
    answer = fetch_release_tags([repo])[repo]
    if isinstance(answer, str):
        raise ProbeError(answer)
    return answer


@dataclass(frozen=True)
class CommitRef:
    sha: str
    committed_at: datetime


def compare_commits(repo: str, base: str, head: str) -> list[CommitRef]:
    """Commits reachable from ``head`` but not ``base``.

    Refuses on truncation: ``compare`` caps its ``commits`` array, and a short
    read is the exact shape that reports a three-month backlog as clean.
    """
    totals = _gh(
        [
            "api",
            f"repos/{ORG}/{repo}/compare/{base}...{head}?per_page=1",
            "--jq",
            ".total_commits",
        ]
    ).strip()
    try:
        total = int(totals)
    except ValueError as exc:
        raise ProbeError(f"{repo}: compare {base}...{head} returned no total_commits") from exc
    if total == 0:
        return []

    out = _gh(
        [
            "api",
            "--paginate",
            f"repos/{ORG}/{repo}/compare/{base}...{head}?per_page=100",
            "--jq",
            '.commits[] | [.sha, .commit.committer.date] | @tsv',
        ]
    )
    commits: list[CommitRef] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, _, date = line.partition("\t")
        if not sha or not date:
            raise ProbeError(f"{repo}: malformed compare row: {line!r}")
        commits.append(CommitRef(sha=sha, committed_at=_parse_ts(date, f"{repo}: commit {sha}")))
    if len(commits) < total:
        raise ProbeError(
            f"{repo}: compare {base}...{head} reported {total} commits but only "
            f"{len(commits)} were readable — refusing to under-report a truncated range"
        )
    return commits


def fetch_path_commits(
    wanted: Sequence[tuple[str, str, str, datetime]],
) -> dict[tuple[str, str], set[str] | str]:
    """Shas touching each ``(repo, path)`` at or after that repo's ``since``.

    ONE GraphQL document covers every (repo, path) pair. The eight-repo roster
    carries three packaged paths each, so this replaces 24 REST reads with 1.

    PER-COMMIT ATTRIBUTION IS PRESERVED, and that is the whole constraint. The
    obvious cheaper route -- reading the packaged set off the ``compare``
    payload already in hand -- does not work: ``compare``'s ``files`` array is
    the AGGREGATE diff of the whole range, so it answers "did anything packaged
    change across these commits" when the question is "WHICH commit is the
    oldest packaged one, and therefore how long has the debt been sitting".
    ``history(path:)`` keeps the per-commit answer, which is why the batching
    goes through GraphQL rather than through the payload already fetched.

    ``since`` is per repo because it is that repo's own earliest unreleased
    commit, so it is not known until after ``compare`` has run. That ordering
    is why this is a second document rather than part of the tag fetch.
    """
    if not wanted:
        return {}
    keys = [(repo, path) for repo, _, path, _ in wanted]
    collected: dict[tuple[str, str], set[str]] = {key: set() for key in keys}
    failed: dict[tuple[str, str], str] = {}
    cursors: dict[tuple[str, str], str | None] = {key: None for key in keys}
    by_key = {(repo, path): (branch, since) for repo, branch, path, since in wanted}
    pending = list(keys)

    for _ in range(_MAX_PAGES):
        if not pending:
            break
        aliases = {f"q{i}": key for i, key in enumerate(pending)}
        parts = []
        for alias, key in aliases.items():
            repo, path = key
            branch, since = by_key[key]
            since_iso = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            cursor = cursors[key]
            after = f', after: "{cursor}"' if cursor else ""
            parts.append(
                f'{alias}: repository(owner: "{ORG}", name: "{repo}") {{ '
                f'object(expression: "{branch}") {{ ... on Commit {{ '
                f'history(path: "{path}", since: "{since_iso}", '
                f"first: {_GRAPHQL_PAGE}{after}) {{ "
                f"pageInfo {{ hasNextPage endCursor }} nodes {{ oid }} }} }} }} }}"
            )
        payload = _gh_graphql("query { " + " ".join(parts) + " }")
        errors = _graphql_alias_errors(payload, {a: f"{k[0]}:{k[1]}" for a, k in aliases.items()})
        data = payload.get("data") or {}

        still_pending: list[tuple[str, str]] = []
        for alias, key in aliases.items():
            repo, path = key
            label = f"{repo}:{path}"
            if label in errors:
                failed[key] = errors[label]
                continue
            node = data.get(alias)
            if not isinstance(node, dict):
                failed[key] = "graphql returned no repository node"
                continue
            obj = node.get("object")
            if not isinstance(obj, dict):
                # A branch that does not resolve is a real finding, not an
                # empty packaged set: an empty set reads as "nothing packaged
                # changed", which is a clean bill of health we have not earned.
                failed[key] = f"branch {by_key[key][0]} did not resolve"
                continue
            history = obj.get("history") or {}
            for entry in history.get("nodes") or []:
                oid = str((entry or {}).get("oid", "")).strip()
                if oid:
                    collected[key].add(oid)
            page = history.get("pageInfo") or {}
            if page.get("hasNextPage") and page.get("endCursor"):
                cursors[key] = str(page["endCursor"])
                still_pending.append(key)
        pending = still_pending
    else:
        if pending:
            raise ProbeError(
                f"graphql history pagination exceeded {_MAX_PAGES} rounds for: "
                + ", ".join(f"{r}:{p}" for r, p in pending)
            )

    result: dict[tuple[str, str], set[str] | str] = {}
    for key in keys:
        if key in failed:
            result[key] = f"{key[0]}: {key[1]}: {failed[key]}"
        else:
            result[key] = collected[key]
    return result


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    repo: str
    verdict: str
    base_ref: str = ""
    unreleased_total: int = 0
    unreleased_packaged: int = 0
    oldest_packaged_at: str = ""
    age_hours: float = 0.0
    detail: str = ""
    deferral_ticket: str = ""
    #: Only populated on a QUOTA_EXHAUSTED row. Empty string means the reset
    #: time could not be read, never that the bucket resets now.
    quota_reset_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def measure_roster(
    policy_repos: Sequence[RepoPolicy],
    *,
    base_override: str | None = None,
) -> tuple[dict[str, tuple[str, int, list[CommitRef]]], dict[str, str]]:
    """Measure the whole roster in three phases, batching the two that batch.

    Returns ``(measured_by_repo, errors_by_repo)``. A repo appears in exactly
    one of the two, so a single unreadable repo is still a single ERROR row --
    batching must not turn one dead repo into eight.

    THE PHASES, AND WHY THERE ARE THREE RATHER THAN TWO.

    1. Tags for every repo, one GraphQL document (2 requests for this roster,
       against 11 for the per-repo REST paginate).
    2. ``compare`` per repo, over REST, UNCHANGED. This is deliberately not
       batched: its truncation refusal -- a collected commit count short of the
       API's own ``total_commits`` refuses rather than under-reporting -- is
       the check that stops a capped range reading as a clean repo, and it is
       not worth re-implementing against a different transport to save eight
       requests.
    3. Path history for every (repo, path), one GraphQL document (1 request
       against 24).

    Phase 3 cannot merge into phase 1 because each repo's ``since`` is that
    repo's own earliest unreleased commit, which phase 2 is what discovers.
    """
    measured: dict[str, tuple[str, int, list[CommitRef]]] = {}
    errors: dict[str, str] = {}

    # Phase 1 --------------------------------------------------------------
    bases: dict[str, str] = {}
    if base_override is None:
        tags = fetch_release_tags([r.repo for r in policy_repos])
        for policy_repo in policy_repos:
            answer = tags[policy_repo.repo]
            if isinstance(answer, str):
                errors[policy_repo.repo] = answer
            else:
                bases[policy_repo.repo] = answer[-1]
    else:
        for policy_repo in policy_repos:
            bases[policy_repo.repo] = base_override

    # Phase 2 --------------------------------------------------------------
    ranges: dict[str, list[CommitRef]] = {}
    for policy_repo in policy_repos:
        name = policy_repo.repo
        if name in errors:
            continue
        try:
            unreleased = compare_commits(name, bases[name], policy_repo.branch)
        except QuotaExhaustedError:
            # Phase 2 is the one phase still on REST, so it is the one place a
            # quota refusal can arrive at a PER-REPO catch. Demoting it to a
            # per-repo error string here would strip the distinct verdict, the
            # reset time and the outcome token, and the run would report N
            # unreadable repos for one empty bucket -- precisely the
            # conflation OMN-19098 removed. It is re-raised so ``evaluate``'s
            # quota arm classifies it, and it propagates for the same reason
            # the batched phases let it propagate: an empty bucket is not a
            # fact about this repo.
            raise
        except ProbeError as exc:
            errors[name] = str(exc)
            continue
        if not unreleased:
            measured[name] = (bases[name], 0, [])
            continue
        ranges[name] = unreleased

    # Phase 3 --------------------------------------------------------------
    wanted: list[tuple[str, str, str, datetime]] = []
    for policy_repo in policy_repos:
        name = policy_repo.repo
        if name not in ranges:
            continue
        # `since` is the range's own earliest commit, NOT the tag's date: a tag
        # can sit on a commit whose timestamp is later than an unreleased
        # sibling's (rebases, squashes, and an out-of-order merge all produce
        # that), and anchoring on the tag date would silently drop those.
        since = min(commit.committed_at for commit in ranges[name])
        for path in policy_repo.packaged_paths:
            wanted.append((name, policy_repo.branch, path, since))

    touched = fetch_path_commits(wanted)

    for policy_repo in policy_repos:
        name = policy_repo.repo
        if name not in ranges:
            continue
        unreleased = ranges[name]
        in_range = {commit.sha: commit for commit in unreleased}
        packaged: set[str] = set()
        failure = ""
        for path in policy_repo.packaged_paths:
            # Distinct name from the phase-1 tag answer: that one is a tag
            # list, this one is a sha set, and sharing a binding across the
            # phases is how a type error hides behind a union.
            touched_shas = touched.get((name, path))
            if isinstance(touched_shas, str):
                failure = touched_shas
                break
            if touched_shas is None:
                failure = f"{name}: {path}: no answer returned for this path"
                break
            packaged |= touched_shas
        if failure:
            errors[name] = failure
            continue
        hits = [in_range[sha] for sha in packaged & in_range.keys()]
        hits.sort(key=lambda commit: commit.committed_at)
        measured[name] = (bases[name], len(unreleased), hits)

    return measured, errors


def evaluate(
    policy: Policy,
    *,
    now: datetime,
    repos: tuple[RepoPolicy, ...] | None = None,
    base_override: str | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    targets = tuple(repos if repos is not None else policy.repos)
    try:
        measured, probe_errors = measure_roster(targets, base_override=base_override)
    except QuotaExhaustedError as exc:
        # MUST precede the ProbeError arm: QuotaExhaustedError subclasses it,
        # so the broader arm would swallow this one and re-create the exact
        # conflation the QUOTA_EXHAUSTED verdict exists to remove (OMN-19098).
        #
        # Batching made this arm BROADER rather than narrower, and that is the
        # correct direction. Before OMN-19099 the quota refusal was caught per
        # repo, so a bucket that emptied mid-sweep produced a quota row only
        # for the repos not yet read and left the earlier ones looking fine.
        # The roster reads are now one document per phase, so an empty bucket
        # refuses the document covering every repo: one infrastructure event,
        # reported once as one cause against every repo it actually stopped us
        # reading. That is exactly what OMN-19098 asked for and what the
        # per-repo loop could only approximate.
        return [
            Finding(
                repo=r.repo,
                verdict=VERDICT_QUOTA_EXHAUSTED,
                detail=(
                    f"{'secondary ' if exc.secondary else ''}API quota exhausted, "
                    f"{exc.reset_detail}: {exc}"
                ),
                quota_reset_at=(
                    exc.reset_at.astimezone(UTC).isoformat() if exc.reset_at else ""
                ),
            )
            for r in targets
        ]
    except ProbeError as exc:
        # A transport-level failure of a BATCHED read is not attributable to
        # one repo, so it is recorded against every repo the document covered
        # rather than against none. Reporting it against none would leave the
        # run looking like a clean sweep of an empty roster.
        return [
            Finding(repo=r.repo, verdict=VERDICT_ERROR, detail=str(exc)) for r in targets
        ]
    for policy_repo in targets:
        if policy_repo.repo in probe_errors:
            findings.append(
                Finding(
                    repo=policy_repo.repo,
                    verdict=VERDICT_ERROR,
                    detail=probe_errors[policy_repo.repo],
                )
            )
            continue
        base_ref, total, hits = measured[policy_repo.repo]

        if not hits:
            findings.append(
                Finding(
                    repo=policy_repo.repo,
                    verdict=VERDICT_FRESH,
                    base_ref=base_ref,
                    unreleased_total=total,
                    detail=(
                        f"{total} unreleased commit(s), none packaged"
                        if total
                        else "dev is contained in the latest tag"
                    ),
                )
            )
            continue

        oldest = hits[0]
        age_hours = (now - oldest.committed_at).total_seconds() / 3600.0
        if age_hours <= policy.max_age_hours:
            findings.append(
                Finding(
                    repo=policy_repo.repo,
                    verdict=VERDICT_FRESH,
                    base_ref=base_ref,
                    unreleased_total=total,
                    unreleased_packaged=len(hits),
                    oldest_packaged_at=oldest.committed_at.astimezone(UTC).isoformat(),
                    age_hours=round(age_hours, 2),
                    detail=f"within the {policy.max_age_hours:g}h release window",
                )
            )
            continue

        if not policy_repo.enforced:
            findings.append(
                Finding(
                    repo=policy_repo.repo,
                    verdict=VERDICT_STALE_UNENFORCED,
                    base_ref=base_ref,
                    unreleased_total=total,
                    unreleased_packaged=len(hits),
                    oldest_packaged_at=oldest.committed_at.astimezone(UTC).isoformat(),
                    age_hours=round(age_hours, 2),
                    deferral_ticket=policy_repo.deferral_ticket,
                    detail=(
                        f"recorded debt, not yet blocking ({policy_repo.deferral_ticket}): "
                        f"{policy_repo.deferral_reason}"
                    ),
                )
            )
            continue

        findings.append(
            Finding(
                repo=policy_repo.repo,
                verdict=VERDICT_STALE,
                base_ref=base_ref,
                unreleased_total=total,
                unreleased_packaged=len(hits),
                oldest_packaged_at=oldest.committed_at.astimezone(UTC).isoformat(),
                age_hours=round(age_hours, 2),
                detail=f"oldest unreleased packaged commit {oldest.sha[:12]}",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# The positive control
# ---------------------------------------------------------------------------


@dataclass
class ControlResult:
    passed: bool
    detail: str
    rows: list[Finding] = field(default_factory=list)


def run_positive_control(policy: Policy, *, now: datetime) -> ControlResult:
    """Re-run the sweep against a base ref that MUST look stale.

    A zero-row sweep is indistinguishable from a broken one, so a GREEN verdict
    is only reported once this control has produced rows. The injected base is
    each repo's EARLIEST release tag — a real ref, months or years old, whose
    range therefore contains packaged commits by construction. If the control
    also returns zero, the sweep is blind and the run fails.
    """
    subject: RepoPolicy | None = None
    base: str | None = None
    reasons: list[str] = []
    for policy_repo in policy.repos:
        try:
            tags = list_release_tags(policy_repo.repo)
        except ProbeError as exc:
            reasons.append(str(exc))
            continue
        if len(tags) < 2:
            reasons.append(f"{policy_repo.repo}: only {len(tags)} release tag(s), unusable as a control")
            continue
        subject, base = policy_repo, tags[0]
        break

    if subject is None or base is None:
        return ControlResult(
            passed=False,
            detail="no repo could supply a known-old base tag: " + "; ".join(reasons),
        )

    rows = evaluate(policy, now=now, repos=(subject,), base_override=base)
    stale_rows = [
        row for row in rows if row.verdict in (VERDICT_STALE, VERDICT_STALE_UNENFORCED)
    ]
    if not stale_rows:
        return ControlResult(
            passed=False,
            detail=(
                f"control FAILED: {subject.repo} measured against its earliest tag {base} "
                f"produced no stale row ({rows[0].verdict if rows else 'no rows'}: "
                f"{rows[0].detail if rows else ''}) — the sweep cannot see staleness, "
                "so today's zero proves nothing"
            ),
            rows=rows,
        )
    row = stale_rows[0]
    return ControlResult(
        passed=True,
        detail=(
            f"control PASSED: {subject.repo} vs its earliest tag {base} => "
            f"{row.unreleased_packaged} packaged commit(s), oldest {row.oldest_packaged_at}"
        ),
        rows=rows,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_COLUMNS = (
    ("repo", 20),
    ("verdict", 17),
    ("latest tag", 12),
    ("unreleased", 10),
    ("packaged", 8),
    ("oldest packaged commit", 26),
    ("age", 10),
)


def render_table(findings: list[Finding]) -> str:
    header = " ".join(name.ljust(width) for name, width in _COLUMNS).rstrip()
    lines = [header, "-" * len(header)]
    for finding in findings:
        age = f"{finding.age_hours:.1f}h" if finding.age_hours else "-"
        cells = (
            finding.repo,
            finding.verdict,
            finding.base_ref or "-",
            str(finding.unreleased_total),
            str(finding.unreleased_packaged),
            finding.oldest_packaged_at[:25] or "-",
            age,
        )
        lines.append(
            " ".join(cell.ljust(width) for cell, (_, width) in zip(cells, _COLUMNS)).rstrip()
        )
    for finding in findings:
        if finding.verdict != VERDICT_FRESH:
            lines.append(f"  {finding.verdict} {finding.repo}: {finding.detail}")
    return "\n".join(lines)


def _write_step_summary(text: str) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    with open(summary, "a", encoding="utf-8") as handle:
        handle.write(text + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY, help="policy YAML path")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="frozen shrink-only roster of repos whose staleness does not block",
    )
    parser.add_argument(
        "--check-roster",
        action="store_true",
        help="OFFLINE, no network: validate the policy file and assert the unenforced "
        "roster has not grown against the frozen baseline. This is the pre-commit half "
        "of the gate — the sweep itself is a network probe and does not belong in a hook.",
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help="limit the sweep to this repo (repeatable). Narrowing SUPPRESSES the "
        "automatic positive control's ability to speak for the whole set, so a "
        "narrowed run is reported as advisory in its own output.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=None,
        help="override the policy's staleness window (hours)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--positive-control",
        action="store_true",
        help="run ONLY the positive control: measure a repo against a known-old base "
        "tag and require a stale row. Exit 0 proves the sweep can see staleness.",
    )
    parser.add_argument(
        "--no-positive-control",
        action="store_true",
        help="do not auto-run the control on a zero-row sweep. For unit tests and "
        "read-only reporting lanes only; never for a gate run.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = datetime.now(UTC)

    try:
        policy = load_policy(args.policy)
    except PolicyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.check_roster:
        try:
            baseline = load_unenforced_baseline(args.baseline)
        except PolicyError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        violations = check_roster(policy, baseline)
        for violation in violations:
            print(f"RATCHET VIOLATION: {violation}", file=sys.stderr)
        if violations:
            return 1
        print(
            f"roster OK: {len(policy.repos)} publishing repo(s), "
            f"{len(policy.unenforced)} unenforced "
            f"({', '.join(policy.unenforced) or 'none'}), baseline holds {len(baseline)}"
        )
        return 0
    if args.max_age_hours is not None:
        if args.max_age_hours <= 0:
            print("ERROR: --max-age-hours must be positive", file=sys.stderr)
            return 2
        policy = Policy(max_age_hours=args.max_age_hours, repos=policy.repos)

    selected = policy.repos
    if args.repo:
        by_name = {repo.repo: repo for repo in policy.repos}
        unknown = [name for name in args.repo if name not in by_name]
        if unknown:
            print(
                f"ERROR: not in the publishing set: {', '.join(unknown)}", file=sys.stderr
            )
            return 2
        selected = tuple(by_name[name] for name in args.repo)

    try:
        if args.positive_control:
            only = run_positive_control(policy, now=now)
            if args.json:
                print(
                    json.dumps(
                        {
                            "mode": "positive_control",
                            "passed": only.passed,
                            "detail": only.detail,
                            "findings": [row.to_json() for row in only.rows],
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                print(render_table(only.rows))
                print()
                print(only.detail)
            return 0 if only.passed else 1

        findings = evaluate(policy, now=now, repos=selected)
    except PolicyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    stale = [f for f in findings if f.verdict == VERDICT_STALE]
    errors = [f for f in findings if f.verdict == VERDICT_ERROR]
    unenforced = [f for f in findings if f.verdict == VERDICT_STALE_UNENFORCED]
    quota = [f for f in findings if f.verdict == VERDICT_QUOTA_EXHAUSTED]

    control: ControlResult | None = None
    # The control is a SECOND full sweep. Running it while the bucket is empty
    # spends more of the quota that just ran out and cannot succeed anyway, so
    # a quota row suppresses it. This is the do-not-retry-inside-an-exhausted-
    # window rule: a retry adds load to the saturated resource.
    needs_control = (
        not stale and not errors and not quota and not args.no_positive_control
    )
    if needs_control:
        control = run_positive_control(policy, now=now)

    # Quota exhaustion STILL FAILS CLOSED. Naming the cause does not excuse it:
    # a run that could not read the roster has not shown the roster is fresh.
    exit_code = 1 if (stale or errors or quota) else 0
    if control is not None and not control.passed:
        exit_code = 1

    if args.json:
        print(
            json.dumps(
                {
                    "mode": "sweep",
                    "generated_at": now.isoformat(),
                    "max_age_hours": policy.max_age_hours,
                    "narrowed": bool(args.repo),
                    "stale": len(stale),
                    "stale_unenforced": len(unenforced),
                    "errors": len(errors),
                    "quota_exhausted": len(quota),
                    "outcome": OUTCOME_QUOTA_EXHAUSTED if quota else "",
                    "quota_reset_at": next(
                        (f.quota_reset_at for f in quota if f.quota_reset_at), ""
                    ),
                    "positive_control": (
                        {"ran": True, "passed": control.passed, "detail": control.detail}
                        if control is not None
                        else {"ran": False, "passed": None, "detail": "not required"}
                    ),
                    "findings": [f.to_json() for f in findings],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        table = render_table(findings)
        print(table)
        print()
        if args.repo:
            print(
                "NOTE: this run was narrowed with --repo, so it does not speak for the "
                "whole publishing set."
            )
        if control is not None:
            print(control.detail)
        if quota:
            reset = next((f.quota_reset_at for f in quota if f.quota_reset_at), "")
            print(
                f"{OUTCOME_QUOTA_EXHAUSTED}: {len(quota)} roster read(s) refused because "
                f"the acting identity API quota was empty, "
                + (f"resets at {reset}." if reset else "reset time unknown.")
            )
            print(
                "This is an infrastructure limit, not a staleness finding and not a "
                "defect in the repos named above. The gate still fails closed: a run "
                "that could not read the roster has not shown the roster is fresh. Do "
                "not rerun immediately -- a retry inside an exhausted window adds load "
                "to the saturated bucket. Wait for the reset."
            )
        if errors:
            print(f"REFUSED: {len(errors)} repo(s) could not be read. A blind sweep is not a pass.")
        if stale:
            print(
                f"FAIL: {len(stale)} repo(s) carry unreleased packaged-source commits older "
                f"than {policy.max_age_hours:g}h. Cut a release. The unenforced roster in "
                f"{args.policy.name} is shrink-only and is not a way out of this."
            )
        elif not errors and not quota:
            print(
                f"PASS: no enforced repo is stale beyond {policy.max_age_hours:g}h"
                + (
                    f" ({len(unenforced)} unenforced repo(s) carry recorded release debt)."
                    if unenforced
                    else "."
                )
            )
        summary = "## Release staleness\n\n```\n" + table + "\n```"
        if quota:
            reset = next((f.quota_reset_at for f in quota if f.quota_reset_at), "")
            summary += (
                f"\n\n**{OUTCOME_QUOTA_EXHAUSTED}** -- this run was refused by an API "
                "quota limit, not by a staleness finding. "
                + (f"Resets at `{reset}`." if reset else "Reset time unknown.")
            )
        _write_step_summary(summary)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
