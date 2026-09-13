# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tripwire for the machine dual control on prod-promotion grants (OMN-18327).

WHAT THIS ASSERTS, AND WHY THE NAME NO LONGER DESCRIBES IT
----------------------------------------------------------
The console-script name, the `ci.yml` job name, and this module's filename
are deliberately UNCHANGED from the OMN-14445 original. They are load-bearing
identifiers: the job name is keyed in
`tests/test_omn_16373_cross_repo_pat_retirement.py`, cited by the merged,
append-only OCC contract `contracts/OMN-16373.yaml`, and surfaced as a check
name on every PR in this repo. Renaming them would be a CI change in a change
that is supposed to make exactly one substantive change. Read the name as an
identifier, not as a description of the assertion below.

ORIGINAL SCOPE (OMN-14445), NOW SUPERSEDED
-------------------------------------------
This job used to assert a GitHub setting: it FAILED whenever
`@OmniNode-ai/platform-leads` had more than one member while
`required_pull_request_reviews` was absent on `dev`. The reasoning was that
OMN-14441's `approved_by != PR-author` check is a complete defense against a
forged self-approval only while the team has exactly one member, so a second
member without CODEOWNERS review would let an author name the OTHER lead in
`approved_by` with nobody having reviewed anything.

That tripwire did exactly what it was built to do: it fired on 2026-09-04,
eight minutes after a second platform lead joined, and `require_code_owner_
reviews` was enabled on `dev` to clear it. The consequence was the merge
freeze the org's standing rule exists to prevent. Every lane in this fleet
commits under one shared account, so the operator is the author of record on
essentially every change-control PR, and GitHub blocks self-approval — which
left OCC#9362 green on every required check and unmergeable, and with it every
product PR whose OCC companion had to merge first.

Operator ruling, in-session, 2026-09-13, firm, recorded verbatim at
`docs/tracking/ROLLING_WORK_LEDGER.md:7452`: "you need to make sure that OCC
tickets are mechanical, like they're supposed to be." Read together with the
standing 2026-08-28 ruling that no human review is a required check on any
process until full-time employees exist, change-control PRs land on machine
gates only. Asserting a GitHub review setting is therefore asserting a thing
the org has ruled out, and a gate that demands a state policy forbids is a
gate that can only be satisfied by breaking policy.

CURRENT SCOPE
-------------
The same safety concern — an approval that the requester supplied to
themselves — is now carried by two MACHINE refusals, and this job asserts
that both of them exist rather than asserting that a human reviewed anything.

  1. AUTHORING TIME, in this repo. `validate_prod_promotion_grants` refuses
     an entry NEW in the change whose `approved_by` is the requester, with
     the reason string `self_granted` (OMN-17157). Asserted BEHAVIOURALLY:
     this job builds a synthetic self-approved entry, runs the real
     validator over it, and requires a refusal — and then runs the SAME
     entry with a different approver and requires NO self-approval refusal.
     A check that only ever proves a failure case cannot tell a working
     validator from one that refuses everything, so the positive control is
     part of the gate, not part of its test suite.

  2. AUTHORING TIME, WIRED. The refusal existing in a module proves nothing
     if CI never passes a requester: `validate_grants` runs the check only
     when `requester is not None`. This job therefore parses `ci.yml` and
     requires that the `validate-prod-promotion-grants` job actually passes
     `--requester`. That job is unconditional and sits under the required
     `CI Summary` rollup, which is fail-closed on a skipped or cancelled
     member.

  3. PROMOTION TIME, in omninode_infra. `scripts/validate_prod_promotion_
     grant.py` refuses `approved_by == requested_by` against the DEPLOY
     DISPATCHER, an identity that is not knowable when the grant is
     authored, and resolves it to `EnumGrantOutcome.SELF_GRANTED`. This is
     the half that the authoring-time check narrows rather than replaces, so
     losing it silently is the failure this tripwire now exists to catch.
     Read over the GitHub API, org-private, with the same credential and the
     same retry/classification machinery the original reads used.

Any of the three missing is a TRIP (exit 1). The check is fail-closed on an
unreadable fact (exit 2), exactly as before.

THE HONEST LIMIT, KEPT
----------------------
This is the same limit CLAUDE.md rule 12 states in its own text, and nothing
here removes it: no file proves a human said the words. Neither refusal stops
a requester from typing the OTHER lead's login into `approved_by` when that
lead never approved anything. What the machine controls enforce is BLAST
RADIUS — a grant is digest-pinned, time-bounded, single-use, uniquely
identified, refused when self-named at authoring time, and refused again when
self-named at dispatch time — not operator authenticity. Enforced human review
was the only control that would have covered authenticity, and the operator
has ruled it out until there are full-time employees to carry it. Do not
re-add it here; re-raise it with the operator instead.

Usage:
    uv run check-platform-leads-review-tripwire

Exit codes:
    0: safe — both machine refusals exist and the authoring-time one is wired.
    1: TRIPPED — at least one of the three facts is false. The prod-promotion
       grant's dual control has lost a half; say which one in the message.
    2: INCONCLUSIVE — a fact could not be determined (token scope, an
       unreadable `ci.yml`, or an unclassified GitHub API failure). Treated
       as a failure: an unproven safety property does not pass.

Wedge-risk note (OMN-14445 review, still current): the promotion-time read is
ORG-PRIVATE with no unauthenticated fallback, so this job has a hard
dependency on a token with access to omninode_infra (`CROSS_REPO_PAT` in CI).
If that PAT is ever absent, expired, or rotated without that access, this job
goes INCONCLUSIVE on EVERY PR, not just a PR that changes the grants file,
because it is unconditional. `--credential-origin` exists so the failure
message names which case applies instead of leaving an operator to guess at
3am. The two local facts need no network at all, which is a narrowing of that
exposure: the original scope required a live read for BOTH of its facts.

Rate-limit note (OMN-16373): GitHub returns **HTTP 403 for BOTH** "your token
lacks the scope" and "you exhausted the REST rate limit", and `CROSS_REPO_PAT`
shares one 5,000 req/hr primary bucket with every other tool, agent, and
workflow acting as its owner. Every observed INCONCLUSIVE on this job to date
has been the rate-limit case (e.g. jobs 98501448095 / 98499618819,
2026-08-27), yet an earlier message asserted a scope regression — sending at
least five separate lanes chasing a credential that was never broken. Failures
are therefore classified from the error BODY, not the status code, and the
retryable classes (rate limit, 5xx/network) are retried within the job's own
timeout budget before the gate gives up.

The gate remains fail-closed for policy and credential failures. Confirmed
rate-limit exhaustion is different: it proves only that the shared API bucket
is empty, not that a refusal was removed. After the bounded retry budget, the
job exits 0 with a DEFERRED diagnostic so one overloaded credential cannot
wedge every PR in the repo. Retrying a *transient* failure is not weakening a
gate — reporting a transient failure as a permanent credential defect is.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NamedTuple

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: This repo's CI workflow, relative to the checkout root.
DEFAULT_CI_WORKFLOW: Final[str] = ".github/workflows/ci.yml"
#: The `ci.yml` job that must actually invoke the grants validator with a
#: requester. Spelled once; `authoring_time_refusal_wired` resolves it out of
#: the parsed workflow rather than grepping, so a mention in a comment or in a
#: different job cannot satisfy it.
GRANTS_VALIDATOR_JOB: Final[str] = "validate-prod-promotion-grants"
#: The flag whose ABSENCE makes the authoring-time refusal dead code:
#: `validate_grants` skips the self-approval check when `requester is None`.
REQUESTER_FLAG: Final[str] = "--requester"

#: The promotion-time half of the dual control, in the separate private repo
#: that owns the k3s prod-promotion gate.
DEFAULT_PROMOTION_GATE_REPO: Final[str] = "OmniNode-ai/omninode_infra"
DEFAULT_PROMOTION_GATE_PATH: Final[str] = "scripts/validate_prod_promotion_grant.py"
DEFAULT_PROMOTION_GATE_REF: Final[str] = "dev"
#: Two INDEPENDENT markers, both required. The comparison alone could survive
#: while the outcome it produces is downgraded to a warning, and the enum
#: member alone could survive with nothing reaching it; requiring both means a
#: rename of either trips this gate instead of half-passing it.
PROMOTION_GATE_MARKERS: Final[tuple[str, ...]] = (
    "approved_by == requested_by",
    "SELF_GRANTED",
)

#: The reason string BOTH halves of the dual control emit for this one
#: condition — `EnumGrantOutcome.SELF_GRANTED` at promotion time, and
#: `SELF_GRANTED_REASON` in this repo's grants validator at authoring time.
#: Spelled here as a literal rather than imported from the validator on
#: purpose: when the validator does not define it, that is the TRIP this gate
#: reports, and a module-level ImportError would report it as a crash instead.
SELF_GRANTED_REASON: Final[str] = "self_granted"

#: Total attempts (initial + retries) for a retryable `gh api` failure.
GH_MAX_ATTEMPTS: Final[int] = 4
#: Mirrors subprocess.run(timeout=...). Retry planning reserves this much time
#: before starting the next `gh api` call.
GH_COMMAND_TIMEOUT_SECONDS: Final[float] = 30.0
#: Exponential-backoff base for transient (5xx / network) failures.
GH_BACKOFF_BASE_SECONDS: Final[float] = 2.0
#: Hard ceiling on a single rate-limit wait. The job's timeout-minutes is 45;
#: this keeps the worst case (3 waits) well inside it rather than burning the
#: whole budget on one sleep and dying without a diagnostic.
GH_MAX_RATE_LIMIT_WAIT_SECONDS: Final[float] = 600.0
#: Cushion added to a reported reset epoch — GitHub's reset is a boundary, and
#: retrying on the exact second reliably returns another 403.
GH_RATE_LIMIT_RESET_CUSHION_SECONDS: Final[float] = 5.0
#: Shared retry deadline across both live reads. The workflow timeout is 45m;
#: keep a 3m cushion so the script can emit a DEFERRED/INCONCLUSIVE diagnostic
#: before Actions terminates the job.
GH_SHARED_RETRY_DEADLINE_SECONDS: Final[float] = (45.0 * 60.0) - (3.0 * 60.0)
#: GitHub's documented minimum retry wait for secondary rate limits when no
#: Retry-After header is available and the primary bucket is not exhausted.
GH_SECONDARY_RATE_LIMIT_MIN_WAIT_SECONDS: Final[float] = 60.0
#: Hard ceiling on one secondary-limit wait. Longer waits are deferred to a
#: later workflow run rather than occupying the runner until job timeout.
GH_MAX_SECONDARY_RATE_LIMIT_WAIT_SECONDS: Final[float] = 600.0


class EnumGhFailureClass(StrEnum):
    """Why a `gh api` call failed — classified from the error body, not the status.

    HTTP 403 is ambiguous by itself: GitHub uses it for both "insufficient
    scope" and "rate limit exceeded". Branching on the status code is what
    produced the OMN-16373 misdiagnosis; branching on the message does not.
    """

    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    PERMISSION = "permission"
    UNCLASSIFIED = "unclassified"


#: Substrings that identify a rate-limit rejection (primary or secondary).
_RATE_LIMIT_SIGNATURES: Final[tuple[str, ...]] = (
    "rate limit exceeded",
    "secondary rate limit",
    "exceeded a secondary rate limit",
    "was submitted too quickly",
    "you have triggered an abuse detection mechanism",
)

_SECONDARY_RATE_LIMIT_SIGNATURES: Final[tuple[str, ...]] = (
    "secondary rate limit",
    "exceeded a secondary rate limit",
    "was submitted too quickly",
    "you have triggered an abuse detection mechanism",
)

#: Substrings that identify a transient server-side or network failure.
_TRANSIENT_SIGNATURES: Final[tuple[str, ...]] = (
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "no server is currently available",
    "server error",
    "connection reset",
    "unexpected eof",
    "timeout awaiting",
    "i/o timeout",
    "temporary failure in name resolution",
)

#: Substrings that identify a genuine credential/permission rejection — the
#: only class for which "check the token's scopes" is the right advice.
_PERMISSION_SIGNATURES: Final[tuple[str, ...]] = (
    "http 401",
    "bad credentials",
    "requires authentication",
    "resource not accessible",
    "not accessible by integration",
    "must have admin rights",
    "insufficient scope",
    "token has not been granted",
)


class GiveUpContext(NamedTuple):
    """How hard this call actually tried before giving up.

    Bundled rather than passed loose so `_diagnose` keeps a readable
    signature, and so "how many attempts" and "how far away was the reset"
    can never drift apart in the message.
    """

    attempts_made: int
    reset_wait_seconds: float | None = None


class TripwireInconclusiveError(RuntimeError):
    """Raised when a required live fact could not be determined."""


class TripwireDeferredRateLimitError(TripwireInconclusiveError):
    """Raised when GitHub rate limiting, not policy state, blocks the live read."""


class TripwireAuthoringRefusalAbsentError(RuntimeError):
    """Raised when the authoring-time refusal is not present to be exercised.

    Deliberately NOT a `TripwireInconclusiveError`. An absent refusal is a
    determinate, reportable fact — the control is gone — and reporting it as
    "could not determine" would send an operator to check a credential
    instead of to restore the check.
    """


def _run_gh(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  Why: command args are fixed by caller, no shell.
        ["gh", *args],  # noqa: S607  Why: `gh` resolved from PATH, matching repo convention.
        capture_output=True,
        text=True,
        timeout=GH_COMMAND_TIMEOUT_SECONDS,
        check=False,
    )


def classify_gh_failure(stderr: str) -> EnumGhFailureClass:
    """Classify a failed `gh api` call from its error text.

    Order matters: rate-limit and transient signatures are checked before
    permission ones, because a rate-limit body also carries "HTTP 403" and a
    naive status-code match would swallow it.
    """
    lowered = stderr.lower()
    if any(sig in lowered for sig in _RATE_LIMIT_SIGNATURES):
        return EnumGhFailureClass.RATE_LIMITED
    if any(sig in lowered for sig in _TRANSIENT_SIGNATURES):
        return EnumGhFailureClass.TRANSIENT
    if any(sig in lowered for sig in _PERMISSION_SIGNATURES):
        return EnumGhFailureClass.PERMISSION
    return EnumGhFailureClass.UNCLASSIFIED


def seconds_until_core_reset(now: float | None = None) -> float | None:
    """Seconds until the core REST bucket resets, or None if unreadable.

    `GET /rate_limit` is documented as not counting against the rate limit,
    so this stays safe to call from inside a rate-limited state.
    """
    result = _run_gh(["api", "rate_limit"])
    if result.returncode != 0:
        return None
    try:
        reset = float(json.loads(result.stdout)["resources"]["core"]["reset"])
    except (ValueError, KeyError, TypeError):
        return None
    return max(0.0, reset - (time.time() if now is None else now))


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff for a transient failure on `attempt` (1-based)."""
    return GH_BACKOFF_BASE_SECONDS * float(2 ** (attempt - 1))


def _is_secondary_rate_limit(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(sig in lowered for sig in _SECONDARY_RATE_LIMIT_SIGNATURES)


def _retry_after_seconds(stderr: str) -> float | None:
    match = re.search(r"\bretry-after\b[^0-9]*(\d+)", stderr, re.IGNORECASE)
    if match is None:
        return None
    return float(match.group(1))


def _secondary_rate_limit_delay(stderr: str, attempt: int) -> float | None:
    retry_after = _retry_after_seconds(stderr)
    backoff = GH_SECONDARY_RATE_LIMIT_MIN_WAIT_SECONDS * float(2 ** (attempt - 1))
    delay = retry_after if retry_after is not None else backoff
    delay = max(GH_SECONDARY_RATE_LIMIT_MIN_WAIT_SECONDS, delay)
    if delay > GH_MAX_SECONDARY_RATE_LIMIT_WAIT_SECONDS:
        return None
    return delay


def _rate_limit_delay(reset_wait: float | None, attempt: int) -> float | None:
    """Seconds to wait out a rate limit, or None if waiting is futile.

    `reset_wait is None` means the core bucket's reset could not be read —
    typically a *secondary* rate limit, which has no published reset. Plain
    backoff is the right response there; giving up would turn a few seconds
    of throttling into a failed gate.
    """
    backoff = _backoff_seconds(attempt)
    if reset_wait is None:
        return backoff
    if reset_wait > GH_MAX_RATE_LIMIT_WAIT_SECONDS:
        return None
    return max(backoff, reset_wait + GH_RATE_LIMIT_RESET_CUSHION_SECONDS)


def _run_gh_checked(
    args: Sequence[str],
    *,
    action: str,
    credential_origin: str,
    sleep: Callable[[float], None] = time.sleep,
    deadline: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `gh api`, retrying retryable failures, or raise INCONCLUSIVE.

    Only rate-limit and transient failures are retried. A permission failure
    is returned immediately — retrying a revoked token wastes the job's
    budget and delays the operator seeing the real message.
    """
    for attempt in range(1, GH_MAX_ATTEMPTS + 1):
        if (
            deadline is not None
            and time.monotonic() + GH_COMMAND_TIMEOUT_SECONDS >= deadline
        ):
            raise TripwireInconclusiveError(
                _diagnose(
                    action,
                    "retry deadline reached before starting another gh api call",
                    credential_origin,
                    None,
                    give_up=GiveUpContext(attempt - 1),
                )
            )
        result = _run_gh(args)
        if result.returncode == 0:
            return result
        failure = classify_gh_failure(result.stderr)
        reset_wait: float | None = None
        delay: float | None = None
        if attempt < GH_MAX_ATTEMPTS:
            if failure is EnumGhFailureClass.TRANSIENT:
                delay = _backoff_seconds(attempt)
            elif failure is EnumGhFailureClass.RATE_LIMITED:
                if _is_secondary_rate_limit(result.stderr):
                    delay = _secondary_rate_limit_delay(result.stderr, attempt)
                else:
                    reset_wait = seconds_until_core_reset()
                    delay = _rate_limit_delay(reset_wait, attempt)
        if (
            delay is not None
            and deadline is not None
            and time.monotonic() + delay + GH_COMMAND_TIMEOUT_SECONDS >= deadline
        ):
            delay = None
        if delay is None:
            error_type: type[TripwireInconclusiveError]
            error_type = (
                TripwireDeferredRateLimitError
                if failure is EnumGhFailureClass.RATE_LIMITED
                else TripwireInconclusiveError
            )
            raise error_type(
                _diagnose(
                    action,
                    result.stderr,
                    credential_origin,
                    failure,
                    give_up=GiveUpContext(attempt, reset_wait),
                )
            )
        print(
            f"{action}: {failure.value} failure on attempt {attempt}/"
            f"{GH_MAX_ATTEMPTS}; retrying in {delay:.0f}s",
            file=sys.stderr,
        )
        sleep(delay)
    # Unreachable: the final attempt always raises above.
    raise TripwireInconclusiveError(  # pragma: no cover
        _diagnose(action, "retry loop exhausted", credential_origin, None)
    )


def _describe_give_up(give_up: GiveUpContext | None) -> str:
    """Say exactly why this call stopped retrying — no rounder-sounding fiction.

    An earlier draft of this message claimed "already retried up to 4 times"
    even when it had given up on the first attempt because the bucket's reset
    was hours away. Overstating the effort in a diagnostic is the same class
    of defect as misnaming the cause, so the numbers here are the real ones.
    """
    if give_up is None:
        return ""
    attempts_made = give_up.attempts_made
    reset_wait = give_up.reset_wait_seconds
    plural = "" if attempts_made == 1 else "s"
    detail = f" Gave up after {attempts_made} attempt{plural}"
    if reset_wait is not None and reset_wait > GH_MAX_RATE_LIMIT_WAIT_SECONDS:
        detail += (
            f": the bucket does not reset for ~{reset_wait / 60:.0f} min, "
            f"beyond this job's {GH_MAX_RATE_LIMIT_WAIT_SECONDS / 60:.0f} min wait "
            "budget, so waiting inside the job would only burn its timeout"
        )
    elif attempts_made >= GH_MAX_ATTEMPTS:
        detail += f" (the full retry budget of {GH_MAX_ATTEMPTS})"
    return detail + "."


def _diagnose(
    action: str,
    stderr: str,
    credential_origin: str,
    failure: EnumGhFailureClass | None = None,
    *,
    give_up: GiveUpContext | None = None,
) -> str:
    """Build a legible diagnostic that names the real cause before anything else.

    OMN-14445 review: a fail-closed gate whose failure can't be decoded at
    3am invites `--no-verify` habits. This is explicit about the most likely
    cause FIRST, then the raw gh error, so "this is a token problem, not a
    policy violation" is the first thing an operator reads.

    OMN-16373: when the failure is classifiable from the error body, that
    classification WINS over the credential-origin heuristic. Naming a scope
    regression for what is actually a shared-bucket rate limit is worse than
    saying nothing — it sends people to rotate a working credential.
    """
    raw = stderr.strip() or "unknown gh api error"
    if failure is EnumGhFailureClass.RATE_LIMITED:
        cause = (
            "RATE LIMIT, NOT A SCOPE PROBLEM: GitHub rejected this read "
            "because the REST rate-limit bucket for this token's owner was "
            "exhausted — GitHub returns HTTP 403 for this exactly as it does "
            "for a scope failure, so the status code alone does not "
            "distinguish them. The token's scopes are NOT implicated: this "
            "same call succeeds on other PRs in the same window."
            f"{_describe_give_up(give_up)} Fix: "
            "re-run this job once the bucket resets, and reduce the "
            "concurrent API load sharing that identity. Do NOT rotate the "
            "PAT on this evidence."
        )
        return f"{cause}\n{action}: {raw}"
    if failure is EnumGhFailureClass.TRANSIENT:
        cause = (
            "TRANSIENT GITHUB API FAILURE, NOT A SCOPE PROBLEM: GitHub "
            "returned a server-side or network error, which says nothing "
            "about this token's scopes or about platform-leads membership."
            f"{_describe_give_up(give_up)} Fix: "
            "check githubstatus.com, then re-run this job."
        )
        return f"{cause}\n{action}: {raw}"
    if credential_origin == "fallback":
        cause = (
            "TOKEN PROBLEM, NOT A POLICY VIOLATION: CROSS_REPO_PAT was not "
            "available for this run (unset, expired/revoked, or withheld by "
            "GitHub for a fork-originated PR) — this job fell back to the "
            "default GITHUB_TOKEN, which cannot read org-private resources "
            "(org team membership / branch protection) by GitHub design. "
            "Fix: restore a valid CROSS_REPO_PAT with read:org scope in "
            "repo secrets. This is NOT evidence that platform-leads grew or "
            "that anyone did anything wrong."
        )
    elif credential_origin == "cross_repo_pat":
        cause = (
            "TOKEN PROBLEM, NOT A POLICY VIOLATION: CROSS_REPO_PAT was "
            "present but the API call still failed, and the error body "
            "matched no known rate-limit or transient signature. The "
            "remaining candidates are that it lacks read:org scope or lost "
            "org access. Fix: verify the PAT's scopes and that its owner is "
            "still an org member — but FIRST confirm the raw error below is "
            "not a rate-limit message this classifier failed to recognise "
            "(OMN-16373), because a working PAT has been misdiagnosed as an "
            "expired one on exactly that mistake."
        )
    else:
        cause = (
            "Could not determine whether CROSS_REPO_PAT was available for "
            "this run; treat as a possible token problem before assuming a "
            "policy violation."
        )
    return f"{cause}\n{action}: {raw}"


def _synthetic_grant_entry(*, approved_by: str) -> dict[str, Any]:
    """A schema-shaped grant entry used only to exercise the real validator.

    Never written to the repository's grants file and never resolvable by
    anything downstream: the digest is all-zero and the grant id is the nil
    uuid4 shape. Its only job is to give the authoring-time refusal something
    real to refuse, so this gate proves BEHAVIOUR rather than matching text in
    a module it never runs.
    """
    now = datetime.now(tz=UTC)
    return {
        "grant_id": "grant-00000000-0000-4000-8000-000000000000",
        "runtime_lane": "prod",
        "image_digest": f"sha256:{'0' * 64}",
        "promotion_batch_id": "tripwire-synthetic-probe",
        "approved_by": approved_by,
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": (
            "synthetic entry built in-memory by the OMN-18327 tripwire to "
            "exercise the self-approval refusal; never persisted"
        ),
    }


def _self_approval_errors(*, approved_by: str, requester: str) -> list[str]:
    """Run the REAL grants validator over one synthetic entry; return its errors.

    Raises `TripwireAuthoringRefusalAbsentError` when the validator does not
    expose the OMN-17157 self-approval surface at all — that is a TRIP, not an
    INCONCLUSIVE: a refusal that is not in the module is a refusal that is not
    enforced, which is precisely the condition this gate reports.
    """
    try:
        from onex_change_control.scripts.validate_prod_promotion_grants import (
            validate_grants,
        )
    except ImportError as exc:  # pragma: no cover - the module is a sibling
        msg = (
            "could not import validate_prod_promotion_grants — the "
            f"authoring-time refusal cannot be exercised at all: {exc}"
        )
        raise TripwireAuthoringRefusalAbsentError(msg) from exc

    signature = inspect.signature(validate_grants)
    if "requester" not in signature.parameters:
        msg = (
            "validate_grants() takes no `requester` parameter, so it cannot "
            "refuse a self-approved grant at authoring time. The OMN-17157 "
            "control is absent from this repo."
        )
        raise TripwireAuthoringRefusalAbsentError(msg)

    payload = {"entries": [_synthetic_grant_entry(approved_by=approved_by)]}
    with tempfile.TemporaryDirectory() as tmpdir:
        probe_file = Path(tmpdir) / "tripwire_probe_grants.yaml"
        probe_file.write_text(yaml.safe_dump(payload), encoding="utf-8")
        # Called through a kwargs mapping on purpose. Whether `validate_grants`
        # ACCEPTS `requester` is the fact this gate exists to determine, and it
        # is determined at runtime by the `inspect.signature` guard above — a
        # statically-typed call site would make this module fail to type-check
        # against exactly the revision it is supposed to report on.
        kwargs: dict[str, Any] = {"requester": requester}
        result = validate_grants(probe_file, **kwargs)
    return [str(error) for error in result.errors]


def authoring_time_refusal_behaves() -> tuple[bool, str]:
    """Prove the OMN-17157 refusal fires on a self-approval and only on one.

    Two runs, and BOTH are part of the gate:

      * the negative case — `approved_by` IS the requester — must produce a
        `self_granted` error; and
      * the positive control — the identical entry with a different approver
        — must produce NO `self_granted` error.

    Without the second run this gate cannot tell a working validator from one
    that refuses every grant, and "it refused" would be evidence of nothing.
    """
    requester = "tripwire-requester"
    try:
        self_approved = _self_approval_errors(
            approved_by=requester, requester=requester
        )
        peer_approved = _self_approval_errors(
            approved_by="tripwire-other-approver", requester=requester
        )
    except TripwireAuthoringRefusalAbsentError as exc:
        return False, str(exc)

    refused_self = any(SELF_GRANTED_REASON in error for error in self_approved)
    refused_peer = any(SELF_GRANTED_REASON in error for error in peer_approved)

    if not refused_self:
        return False, (
            "the grants validator did NOT refuse a synthetic entry whose "
            f"approved_by equals the requester ({requester!r}). Expected an "
            f"error containing {SELF_GRANTED_REASON!r}; got: {self_approved!r}"
        )
    if refused_peer:
        return False, (
            "the grants validator refused an entry approved by someone OTHER "
            "than the requester, so its refusal does not distinguish a "
            "self-approval from an ordinary grant and proves nothing. Errors: "
            f"{peer_approved!r}"
        )
    return True, (
        "the grants validator refuses a self-approved entry with "
        f"{SELF_GRANTED_REASON!r} and does not refuse the same entry approved "
        "by a different identity (positive control)"
    )


def authoring_time_refusal_wired(ci_workflow: Path) -> tuple[bool, str]:
    """Prove `ci.yml` actually passes a requester to the grants validator.

    `validate_grants` runs the self-approval check only when `requester is
    not None`, so a refusal that CI never supplies a requester to is dead
    code. Parsed as YAML rather than grepped: a `--requester` appearing in a
    comment, or in a different job, is not the same fact.
    """
    try:
        workflow = yaml.safe_load(ci_workflow.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        msg = (
            f"could not parse {ci_workflow} to confirm the grants validator "
            f"is wired with a requester: {exc}"
        )
        raise TripwireInconclusiveError(msg) from exc

    jobs = workflow.get("jobs") if isinstance(workflow, dict) else None
    if not isinstance(jobs, dict):
        msg = (
            f"{ci_workflow} has no `jobs:` mapping; cannot confirm the grants "
            "validator is wired with a requester"
        )
        raise TripwireInconclusiveError(msg)
    job = jobs.get(GRANTS_VALIDATOR_JOB)
    if not isinstance(job, dict):
        return False, (
            f"{ci_workflow} declares no `{GRANTS_VALIDATOR_JOB}` job, so the "
            "authoring-time refusal runs nowhere in CI"
        )

    steps = job.get("steps")
    run_text = "\n".join(
        str(step.get("run", ""))
        for step in (steps if isinstance(steps, list) else [])
        if isinstance(step, dict)
    )
    if REQUESTER_FLAG not in run_text:
        return False, (
            f"the `{GRANTS_VALIDATOR_JOB}` job in {ci_workflow} never passes "
            f"`{REQUESTER_FLAG}`, so validate_grants() runs with requester=None "
            "and the OMN-17157 self-approval check is skipped on every PR"
        )
    return True, (
        f"the `{GRANTS_VALIDATOR_JOB}` job in {ci_workflow} passes "
        f"`{REQUESTER_FLAG}`, and that job is unconditional under the required "
        "`CI Summary` rollup"
    )


def promotion_time_refusal_present(
    repo: str,
    path: str,
    ref: str,
    *,
    credential_origin: str = "unknown",
    deadline: float | None = None,
) -> tuple[bool, str]:
    """Prove the k3s promotion-time gate still refuses a self-granted promotion.

    Read over the GitHub API because omninode_infra is a separate, private
    repo: this job has no checkout of it and cloning one would cost minutes on
    a gate that runs on every PR. Two independent markers are required, so a
    rename of either the comparison or the outcome enum trips this rather than
    half-passing.
    """
    result = _run_gh_checked(
        [
            "api",
            # `ref` goes in the query string, not through `--field`: a
            # `--field` on `gh api` forces a POST, which this read is not.
            f"repos/{repo}/contents/{path}?ref={ref}",
            "--header",
            "Accept: application/vnd.github.raw",
        ],
        action=f"could not read {repo}:{path}@{ref}",
        credential_origin=credential_origin,
        deadline=deadline,
    )
    source = result.stdout
    missing = [marker for marker in PROMOTION_GATE_MARKERS if marker not in source]
    if missing:
        return False, (
            f"{repo}:{path}@{ref} no longer carries the promotion-time "
            f"self-approval refusal — missing marker(s): {missing!r}. That is "
            "the half of the dual control that compares approved_by against "
            "the DEPLOY DISPATCHER, an identity not knowable when the grant "
            "was authored; the authoring-time check narrows it and does not "
            "replace it."
        )
    return True, (
        f"{repo}:{path}@{ref} refuses `approved_by == requested_by` and "
        "resolves it to SELF_GRANTED"
    )


def evaluate(
    *,
    authoring: tuple[bool, str],
    wired: tuple[bool, str],
    promotion: tuple[bool, str],
) -> tuple[bool, str]:
    """Pure decision logic, isolated from I/O so it is directly unit-testable.

    Returns (safe, message). Every failing fact is named, not just the first:
    an operator reading this at 3am should learn which halves of the dual
    control are gone in one read, not one re-run per missing half.
    """
    authoring_ok, authoring_detail = authoring
    wired_ok, wired_detail = wired
    promotion_ok, promotion_detail = promotion
    failures = [
        detail
        for ok, detail in (
            (authoring_ok, f"AUTHORING-TIME REFUSAL: {authoring_detail}"),
            (wired_ok, f"AUTHORING-TIME REFUSAL NOT WIRED: {wired_detail}"),
            (promotion_ok, f"PROMOTION-TIME REFUSAL: {promotion_detail}"),
        )
        if not ok
    ]
    if failures:
        joined = "\n  - ".join(failures)
        return False, (
            "TRIPWIRE TRIPPED: the machine dual control on prod-promotion "
            "grants has lost a half. Since the 2026-09-13 operator ruling "
            "(docs/tracking/ROLLING_WORK_LEDGER.md:7452) these refusals are "
            "the ONLY dual control on a prod-promotion grant — no human "
            "review stands behind them. Restore the missing half; do not "
            f"re-add a required review to compensate.\n  - {joined}"
        )
    return True, (
        "PASS: the machine dual control on prod-promotion grants is intact.\n"
        f"  - authoring time: {authoring_detail}\n"
        f"  - wired in CI: {wired_detail}\n"
        f"  - promotion time: {promotion_detail}\n"
        "  - honest limit (CLAUDE.md rule 12): no file proves a human said "
        "the words. This enforces blast radius, not operator authenticity."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ci-workflow",
        type=Path,
        default=Path(DEFAULT_CI_WORKFLOW),
        help="Path to this repo's ci.yml, relative to the checkout root.",
    )
    parser.add_argument("--promotion-gate-repo", default=DEFAULT_PROMOTION_GATE_REPO)
    parser.add_argument("--promotion-gate-path", default=DEFAULT_PROMOTION_GATE_PATH)
    parser.add_argument("--promotion-gate-ref", default=DEFAULT_PROMOTION_GATE_REF)
    parser.add_argument(
        "--credential-origin",
        default="unknown",
        choices=["cross_repo_pat", "fallback", "unknown"],
        help=(
            "Which token supplied GH_TOKEN for this run — 'cross_repo_pat' if "
            "the elevated secret was present, 'fallback' if it fell back to "
            "the default GITHUB_TOKEN. Enriches the INCONCLUSIVE diagnostic; "
            "does not change pass/fail logic."
        ),
    )
    args = parser.parse_args(argv)
    retry_deadline = time.monotonic() + GH_SHARED_RETRY_DEADLINE_SECONDS

    authoring = authoring_time_refusal_behaves()
    try:
        wired = authoring_time_refusal_wired(args.ci_workflow)
        promotion = promotion_time_refusal_present(
            args.promotion_gate_repo,
            args.promotion_gate_path,
            args.promotion_gate_ref,
            credential_origin=args.credential_origin,
            deadline=retry_deadline,
        )
    except TripwireDeferredRateLimitError as exc:
        print(f"TRIPWIRE DEFERRED: {exc}", file=sys.stderr)
        return 0
    except TripwireInconclusiveError as exc:
        print(f"TRIPWIRE INCONCLUSIVE: {exc}", file=sys.stderr)
        return 2

    print(f"authoring-time refusal behaves: {authoring[0]}")
    print(f"authoring-time refusal wired in CI: {wired[0]}")
    print(f"promotion-time refusal present: {promotion[0]}")
    safe, message = evaluate(authoring=authoring, wired=wired, promotion=promotion)
    print(message, file=sys.stderr if not safe else sys.stdout)
    return 0 if safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
