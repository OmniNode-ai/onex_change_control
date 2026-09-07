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
by asking the commits endpoint, once per packaged path, for the commits on
``dev`` touching that path since the range's own earliest commit, and
intersecting by sha with the compare set. The intersection is what makes it
exact: ``since`` alone would over-report commits already contained in the tag.

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
    2 — configuration/usage error (bad policy file, no ``gh``, bad arguments)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
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


class PolicyError(Exception):
    """Operator-facing misuse: unreadable or malformed policy file."""


class ProbeError(Exception):
    """A repo could not be read. Always an ERROR row, never a zero."""


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
        raise ProbeError(f"gh {' '.join(args)}: exit {proc.returncode}: {detail}")
    return proc.stdout


def _semver(tag: str) -> tuple[int, int, int]:
    match = RELEASE_TAG.match(tag)
    if match is None:  # pragma: no cover - callers pre-filter
        raise ProbeError(f"not a release tag: {tag}")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def list_release_tags(repo: str) -> list[str]:
    """Every ``vX.Y.Z`` tag, ascending by semver."""
    out = _gh(["api", "--paginate", f"repos/{ORG}/{repo}/tags?per_page=100", "--jq", ".[].name"])
    tags = [line.strip() for line in out.splitlines() if RELEASE_TAG.match(line.strip())]
    if not tags:
        raise ProbeError(f"{repo}: no vX.Y.Z tags exist — it has never published a release")
    return sorted(set(tags), key=_semver)


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


def commits_touching(repo: str, branch: str, path: str, since: datetime) -> set[str]:
    """Shas on ``branch`` touching ``path`` at or after ``since``."""
    since_iso = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = _gh(
        [
            "api",
            "--paginate",
            f"repos/{ORG}/{repo}/commits?sha={branch}&path={path}&since={since_iso}&per_page=100",
            "--jq",
            ".[].sha",
        ]
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


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

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def measure_repo(
    policy_repo: RepoPolicy,
    *,
    base_override: str | None = None,
) -> tuple[str, int, list[CommitRef]]:
    """Return ``(base_ref, unreleased_total, unreleased_packaged_commits)``."""
    if base_override is None:
        base_ref = list_release_tags(policy_repo.repo)[-1]
    else:
        base_ref = base_override

    unreleased = compare_commits(policy_repo.repo, base_ref, policy_repo.branch)
    if not unreleased:
        return base_ref, 0, []

    # `since` is the range's own earliest commit, NOT the tag's date: a tag can
    # sit on a commit whose timestamp is later than an unreleased sibling's
    # (rebases, squashes, and an out-of-order merge all produce that), and
    # anchoring on the tag date would silently drop those.
    since = min(commit.committed_at for commit in unreleased)
    in_range = {commit.sha: commit for commit in unreleased}

    packaged: set[str] = set()
    for path in policy_repo.packaged_paths:
        packaged |= commits_touching(policy_repo.repo, policy_repo.branch, path, since)

    hits = [in_range[sha] for sha in packaged & in_range.keys()]
    hits.sort(key=lambda commit: commit.committed_at)
    return base_ref, len(unreleased), hits


def evaluate(
    policy: Policy,
    *,
    now: datetime,
    repos: tuple[RepoPolicy, ...] | None = None,
    base_override: str | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    for policy_repo in repos if repos is not None else policy.repos:
        try:
            base_ref, total, hits = measure_repo(policy_repo, base_override=base_override)
        except ProbeError as exc:
            findings.append(
                Finding(repo=policy_repo.repo, verdict=VERDICT_ERROR, detail=str(exc))
            )
            continue

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

    control: ControlResult | None = None
    needs_control = not stale and not errors and not args.no_positive_control
    if needs_control:
        control = run_positive_control(policy, now=now)

    exit_code = 1 if (stale or errors) else 0
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
        if errors:
            print(f"REFUSED: {len(errors)} repo(s) could not be read. A blind sweep is not a pass.")
        if stale:
            print(
                f"FAIL: {len(stale)} repo(s) carry unreleased packaged-source commits older "
                f"than {policy.max_age_hours:g}h. Cut a release. The unenforced roster in "
                f"{args.policy.name} is shrink-only and is not a way out of this."
            )
        elif not errors:
            print(
                f"PASS: no enforced repo is stale beyond {policy.max_age_hours:g}h"
                + (
                    f" ({len(unenforced)} unenforced repo(s) carry recorded release debt)."
                    if unenforced
                    else "."
                )
            )
        _write_step_summary("## Release staleness\n\n```\n" + table + "\n```")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
