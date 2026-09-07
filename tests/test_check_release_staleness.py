# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Behavioural tests for ``scripts/validation/check_release_staleness.py`` (OMN-18010).

Background
----------
Merged work kept missing releases: ``omnimarket#2304`` merged 2026-09-05 and
sat unreleased with its release ticket in Backlog, ``#2334`` did the same, and
a PRD scoring pass found five landed-but-not-deployed items at once. The third
named root cause was that **nothing audited the distance between dev and the
last tag**. Deliverable 3 of OMN-18010 is that audit, as a gate rather than a
sweep (CLAUDE.md rule 5).

These tests drive the real script with a stubbed ``gh`` on ``PATH``, so they
exercise the script's own logic and its exit codes rather than restating them.
The stub is table-driven: a JSON fixture maps a substring of the ``gh`` argv to
a canned stdout and exit code, so a test declares what GitHub says and nothing
else.

RED/GREEN
---------
``test_zero_stale_rows_with_a_blind_control_is_not_green`` is the load-bearing
one. An empty result is not evidence of absence (CLAUDE.md rule 16): a sweep
whose path filter silently matches nothing returns zero rows and reads exactly
like a clean bill of health. That case must exit non-zero, and this test is the
only thing standing between the gate and a permanently, uselessly green check.

``test_unenforced_roster_is_shrink_only`` pins the other direction: the
enforcement roster exists so the gate does not wedge the org on day one, and it
must not be usable as a way to make a red repo quiet.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "validation" / "check_release_staleness.py"
LIVE_POLICY = REPO_ROOT / "scripts" / "validation" / "release_staleness_policy.yaml"
LIVE_BASELINE = (
    REPO_ROOT
    / ".onex_ratchets"
    / "omn_18010_release_staleness_unenforced_baseline.yaml"
)
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-staleness-gate.yml"
CI_SUMMARY_GATE = REPO_ROOT / "scripts" / "ci" / "ci_summary_gate.py"

pytestmark = pytest.mark.unit

#: The trigger filter the gate must agree with, read from omnimarket's
#: release-on-merge decision module. A path set that drifts from the release
#: trigger measures a debt the release would not actually clear.
RELEASE_ON_MERGE_PATHS = ["src", "pyproject.toml", "uv.lock"]


# ---------------------------------------------------------------------------
# gh stub
# ---------------------------------------------------------------------------

_STUB = """#!/usr/bin/env python3
import json, os, sys

argv = " ".join(sys.argv[1:])
with open(os.environ["GH_STUB_FIXTURE"], encoding="utf-8") as handle:
    rules = json.load(handle)
for rule in rules:
    if rule["match"] in argv:
        sys.stdout.write(rule.get("stdout", ""))
        sys.exit(rule.get("exit", 0))
sys.stderr.write("gh stub: no rule matched: " + argv + "\\n")
sys.exit(97)
"""


def _install_gh_stub(tmp_path: Path, rules: list[dict[str, object]]) -> dict[str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "gh"
    stub.write_text(_STUB, encoding="utf-8")
    stub.chmod(0o755)
    fixture = tmp_path / "gh_fixture.json"
    fixture.write_text(json.dumps(rules), encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["GH_STUB_FIXTURE"] = str(fixture)
    env.pop("GITHUB_STEP_SUMMARY", None)
    return env


def _iso(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _repo_rules(
    repo: str,
    *,
    tags: list[str],
    compare: dict[str, list[tuple[str, str]]],
    packaged: dict[str, list[str]] | None = None,
) -> list[dict[str, object]]:
    """Canned GitHub answers for one repo.

    ``compare`` maps a base ref to the (sha, iso-date) commits ahead of it;
    ``packaged`` maps a base ref to the subset of those shas that touch a
    packaged path (defaults to all of them).
    """
    rules: list[dict[str, object]] = [
        {
            "match": f"repos/OmniNode-ai/{repo}/tags",
            "stdout": "".join(f"{t}\n" for t in tags),
        }
    ]
    for base, commits in compare.items():
        # `?per_page=100` MUST precede `?per_page=1`: the stub matches on the
        # first substring hit, and "per_page=1" is a prefix of "per_page=100".
        rules.append(
            {
                "match": f"{repo}/compare/{base}...dev?per_page=100",
                "stdout": "".join(f"{sha}\t{date}\n" for sha, date in commits),
            }
        )
        rules.append(
            {
                "match": f"{repo}/compare/{base}...dev?per_page=1",
                "stdout": f"{len(commits)}\n",
            }
        )
    hits = (
        packaged
        if packaged is not None
        else {base: [sha for sha, _ in commits] for base, commits in compare.items()}
    )
    # The commits endpoint is not keyed by base ref, so every base's packaged
    # set is unioned into the single answer the script will get.
    union: list[str] = []
    for shas in hits.values():
        union.extend(sha for sha in shas if sha not in union)
    rules.append(
        {
            "match": f"repos/OmniNode-ai/{repo}/commits?sha=dev",
            "stdout": "".join(f"{s}\n" for s in union),
        }
    )
    return rules


def _policy(
    tmp_path: Path, entries: list[dict[str, object]], max_age_hours: int = 24
) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(
        yaml.safe_dump(
            {"max_age_hours": max_age_hours, "repos": entries}, sort_keys=False
        ),
        encoding="utf-8",
    )
    return path


def _baseline(tmp_path: Path, names: list[str]) -> Path:
    path = tmp_path / "baseline.yaml"
    path.write_text(yaml.safe_dump({"unenforced": names}), encoding="utf-8")
    return path


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=120,
    )


def _enforced(repo: str) -> dict[str, object]:
    return {
        "repo": repo,
        "branch": "dev",
        "packaged_paths": RELEASE_ON_MERGE_PATHS,
        "enforced": True,
    }


def _unenforced(repo: str) -> dict[str, object]:
    return {
        "repo": repo,
        "branch": "dev",
        "packaged_paths": RELEASE_ON_MERGE_PATHS,
        "enforced": False,
        "deferral_ticket": "OMN-18010",
        "deferral_reason": (
            "release-on-merge is not yet ported to this repo (rollout step 2)"
        ),
    }


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------


def test_enforced_repo_stale_beyond_the_window_fails(tmp_path: Path) -> None:
    """The whole point: unreleased packaged source older than 24h blocks."""
    rules = _repo_rules(
        "omnimarket",
        tags=["v0.1.0", "v0.4.22"],
        compare={"v0.4.22": [("aaaa111", _iso(200)), ("bbbb222", _iso(2))]},
    )
    env = _install_gh_stub(tmp_path, rules)
    result = _run(env, "--policy", str(_policy(tmp_path, [_enforced("omnimarket")])))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "STALE" in result.stdout
    assert "omnimarket" in result.stdout
    # The oldest commit, not the newest, sets the age.
    assert "aaaa111" in result.stdout


def test_repo_inside_the_window_is_fresh(tmp_path: Path) -> None:
    rules = _repo_rules(
        "omnimarket",
        tags=["v0.1.0", "v0.4.22"],
        compare={
            "v0.4.22": [("bbbb222", _iso(2))],
            # The auto-control leg measures against the earliest tag.
            "v0.1.0": [("cccc333", _iso(9000)), ("bbbb222", _iso(2))],
        },
    )
    env = _install_gh_stub(tmp_path, rules)
    result = _run(env, "--policy", str(_policy(tmp_path, [_enforced("omnimarket")])))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "FRESH" in result.stdout
    assert "control PASSED" in result.stdout


def test_docs_only_commits_are_not_release_debt(tmp_path: Path) -> None:
    """A merge that touched nothing packaged is not an unreleased release."""
    rules = _repo_rules(
        "omnimarket",
        tags=["v0.1.0", "v0.4.22"],
        compare={
            "v0.4.22": [("dddd444", _iso(500))],
            "v0.1.0": [("cccc333", _iso(9000))],
        },
        packaged={"v0.4.22": [], "v0.1.0": ["cccc333"]},
    )
    env = _install_gh_stub(tmp_path, rules)
    result = _run(
        env, "--json", "--policy", str(_policy(tmp_path, [_enforced("omnimarket")]))
    )

    assert result.returncode == 0, result.stdout + result.stderr
    finding = json.loads(result.stdout)["findings"][0]
    assert finding["verdict"] == "FRESH"
    assert finding["unreleased_total"] == 1
    assert finding["unreleased_packaged"] == 0
    assert "none packaged" in finding["detail"]


# ---------------------------------------------------------------------------
# Fail-closed
# ---------------------------------------------------------------------------


def test_zero_stale_rows_with_a_blind_control_is_not_green(tmp_path: Path) -> None:
    """RED case. A sweep that cannot see staleness must not report a pass.

    Here the repo is genuinely fresh against its latest tag AND the control
    against the earliest tag also returns nothing — which is the signature of a
    filter that matches nothing rather than of a released repo. Reporting GREEN
    on that is exactly the failure mode CLAUDE.md rule 16 exists for.
    """
    rules = _repo_rules(
        "omnimarket",
        tags=["v0.1.0", "v0.4.22"],
        compare={"v0.4.22": [], "v0.1.0": []},
    )
    env = _install_gh_stub(tmp_path, rules)
    result = _run(env, "--policy", str(_policy(tmp_path, [_enforced("omnimarket")])))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "control FAILED" in result.stdout


def test_unreadable_repo_is_an_error_row_not_a_silent_zero(tmp_path: Path) -> None:
    env = _install_gh_stub(
        tmp_path,
        [
            {
                "match": "repos/OmniNode-ai/omnimarket/tags",
                "stdout": "",
                "exit": 1,
            }
        ],
    )
    result = _run(env, "--policy", str(_policy(tmp_path, [_enforced("omnimarket")])))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ERROR" in result.stdout
    assert "A blind sweep is not a pass" in result.stdout


def test_truncated_compare_refuses_rather_than_under_reporting(tmp_path: Path) -> None:
    """``compare`` caps its commit list; a short read must not read as clean.

    That truncation is the exact shape that would have reported omniclaude's
    319-commit backlog as a handful of commits.
    """
    env = _install_gh_stub(
        tmp_path,
        [
            {"match": "repos/OmniNode-ai/omnimarket/tags", "stdout": "v0.4.22\n"},
            {
                "match": "omnimarket/compare/v0.4.22...dev?per_page=100",
                "stdout": f"aaaa111\t{_iso(5)}\n",
            },
            {"match": "omnimarket/compare/v0.4.22...dev?per_page=1", "stdout": "300\n"},
        ],
    )
    result = _run(env, "--policy", str(_policy(tmp_path, [_enforced("omnimarket")])))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "truncated" in result.stdout


def test_repo_with_no_release_tags_is_an_error(tmp_path: Path) -> None:
    env = _install_gh_stub(
        tmp_path,
        [
            {
                "match": "repos/OmniNode-ai/omnimarket/tags",
                "stdout": "sprint-2026-09\nv1\n",
            }
        ],
    )
    result = _run(env, "--policy", str(_policy(tmp_path, [_enforced("omnimarket")])))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "never published a release" in result.stdout


def test_latest_tag_is_chosen_by_semver_not_api_order(tmp_path: Path) -> None:
    """``/tags`` is not ordered by version; v0.10.0 must beat v0.9.0."""
    rules = _repo_rules(
        "omnimarket",
        tags=["v0.9.0", "v0.10.0", "v0.1.0"],
        compare={
            "v0.10.0": [("aaaa111", _iso(200))],
            "v0.1.0": [("aaaa111", _iso(200))],
        },
    )
    env = _install_gh_stub(tmp_path, rules)
    result = _run(
        env, "--json", "--policy", str(_policy(tmp_path, [_enforced("omnimarket")]))
    )

    assert result.returncode == 1, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["findings"][0]["base_ref"] == "v0.10.0"


# ---------------------------------------------------------------------------
# The enforcement roster
# ---------------------------------------------------------------------------


def test_unenforced_repo_is_reported_but_does_not_block(tmp_path: Path) -> None:
    rules = _repo_rules(
        "omniclaude",
        tags=["v0.1.0", "v0.25.1"],
        compare={
            "v0.25.1": [("aaaa111", _iso(2000))],
            "v0.1.0": [("aaaa111", _iso(2000))],
        },
    )
    env = _install_gh_stub(tmp_path, rules)
    result = _run(env, "--policy", str(_policy(tmp_path, [_unenforced("omniclaude")])))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "STALE_UNENFORCED" in result.stdout
    assert "omniclaude" in result.stdout
    # Visible, counted, and carrying its ticket — not silenced.
    assert "OMN-18010" in result.stdout


def test_unenforced_roster_is_shrink_only(tmp_path: Path) -> None:
    """Marking an enforced repo unenforced is a hard failure, not a knob."""
    policy = _policy(tmp_path, [_unenforced("omnimarket"), _unenforced("omniclaude")])
    baseline = _baseline(tmp_path, ["omniclaude"])
    env = _install_gh_stub(tmp_path, [])
    result = _run(
        env, "--check-roster", "--policy", str(policy), "--baseline", str(baseline)
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "RATCHET VIOLATION" in result.stderr
    assert "omnimarket" in result.stderr


def test_roster_check_flags_a_baseline_entry_for_a_dropped_repo(tmp_path: Path) -> None:
    policy = _policy(tmp_path, [_enforced("omnimarket")])
    baseline = _baseline(tmp_path, ["omniclaude"])
    env = _install_gh_stub(tmp_path, [])
    result = _run(
        env, "--check-roster", "--policy", str(policy), "--baseline", str(baseline)
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "absent from the publishing set" in result.stderr


def test_enforced_must_be_stated_explicitly(tmp_path: Path) -> None:
    """No default. A repo whose blast radius was never decided is exit 2."""
    policy = _policy(
        tmp_path,
        [
            {
                "repo": "omnimarket",
                "branch": "dev",
                "packaged_paths": RELEASE_ON_MERGE_PATHS,
            }
        ],
    )
    env = _install_gh_stub(tmp_path, [])
    result = _run(env, "--check-roster", "--policy", str(policy))

    assert result.returncode == 2, result.stdout + result.stderr
    assert "explicit true or false" in result.stderr


def test_unenforced_entry_without_a_ticket_is_rejected(tmp_path: Path) -> None:
    entry = _unenforced("omniclaude")
    del entry["deferral_ticket"]
    env = _install_gh_stub(tmp_path, [])
    result = _run(env, "--check-roster", "--policy", str(_policy(tmp_path, [entry])))

    assert result.returncode == 2, result.stdout + result.stderr
    assert "deferral_ticket" in result.stderr


# ---------------------------------------------------------------------------
# The live configuration, and the wiring anchors
# ---------------------------------------------------------------------------


def test_live_policy_and_baseline_agree() -> None:
    """The shipped roster must satisfy its own ratchet. Offline; no network."""
    env = dict(os.environ)
    env.pop("GITHUB_STEP_SUMMARY", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check-roster"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_live_policy_paths_match_the_release_on_merge_filter() -> None:
    """A path set that drifts from the release trigger measures the wrong debt.

    ``release-on-merge.yml``'s ``paths:`` filter and
    ``decide_release_on_merge.py``'s ``PACKAGED_PREFIXES``/``PACKAGED_FILES``
    are what decide whether a merge produces a release. If this gate measured a
    different set it would either flag debt a release cannot clear, or miss
    debt a release would have shipped.
    """
    policy = yaml.safe_load(LIVE_POLICY.read_text(encoding="utf-8"))
    for entry in policy["repos"]:
        assert entry["packaged_paths"] == RELEASE_ON_MERGE_PATHS, entry["repo"]


def test_live_policy_excludes_onex_change_control() -> None:
    """OCC publishes nothing; it hosts the gate, it is not a subject of it."""
    policy = yaml.safe_load(LIVE_POLICY.read_text(encoding="utf-8"))
    assert "onex_change_control" not in {entry["repo"] for entry in policy["repos"]}


def test_gate_workflow_runs_unconditionally_on_pull_request() -> None:
    """Anti-wedge anchor.

    ``CI Summary`` asserts this context fail-closed: a ``skipped`` run counts
    as a failure. So the job must carry no ``if:`` and the trigger no ``paths:``
    filter — either would skip the job on ordinary PRs and wedge every merge in
    the repo.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert "pull_request" in triggers
    assert "schedule" in triggers
    pull_request = triggers["pull_request"] or {}
    assert "paths" not in pull_request
    assert "paths-ignore" not in pull_request
    for job_id, job in workflow["jobs"].items():
        assert "if" not in job, f"job {job_id} carries an if: and can skip"


def test_gate_context_is_asserted_by_ci_summary() -> None:
    """Anti-removal anchor: detection that is not wired is ignored (rule 5).

    The workflow alone blocks nothing — ``onex_change_control`` ``dev`` requires
    the ``CI Summary`` umbrella, so the gate has merge-blocking force only while
    its context is named in ``EXPECTED_EXTERNAL_CONTEXTS``. Deleting either half
    silently re-opens the gap this ticket exists to close.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    names = {job.get("name", job_id) for job_id, job in workflow["jobs"].items()}
    asserted = CI_SUMMARY_GATE.read_text(encoding="utf-8")
    assert any(f'"{name}"' in asserted for name in names), (
        f"none of {sorted(names)} appears in ci_summary_gate.py — the gate would "
        "run and report, and block nothing"
    )


def test_baseline_documents_why_each_repo_is_unenforced() -> None:
    """A roster entry with no stated reason is a decision nobody can review."""
    policy = yaml.safe_load(LIVE_POLICY.read_text(encoding="utf-8"))
    baseline = yaml.safe_load(LIVE_BASELINE.read_text(encoding="utf-8"))
    unenforced = {e["repo"] for e in policy["repos"] if not e["enforced"]}
    assert unenforced == set(baseline["unenforced"])
    for entry in policy["repos"]:
        if entry["enforced"]:
            continue
        assert len(entry["deferral_reason"].strip()) >= 40, entry["repo"]
        assert entry["deferral_ticket"].startswith("OMN-"), entry["repo"]


def test_script_never_suppresses_gh_stderr() -> None:
    """CLAUDE.md rule 16: a suppressed error reads as a clean bill of health.

    Four consecutive false "zero failures" readings were produced this way
    during the trusted-CI re-flip canary; re-running without the suppression
    found 30 real rows.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    assert "2>/dev/null" not in source
    assert "stderr=subprocess.DEVNULL" not in source


def test_usage_error_is_exit_two_not_a_pass(tmp_path: Path) -> None:
    env = _install_gh_stub(tmp_path, [])
    missing = tmp_path / "nope.yaml"
    result = _run(env, "--policy", str(missing))
    assert result.returncode == 2
    assert "policy file not found" in result.stderr


def test_narrowing_to_one_repo_says_so(tmp_path: Path) -> None:
    """A narrowed run must not read as a verdict on the whole publishing set."""
    rules = _repo_rules(
        "omnimarket",
        tags=["v0.1.0", "v0.4.22"],
        compare={
            "v0.4.22": [("bbbb222", _iso(2))],
            "v0.1.0": [("cccc333", _iso(9000))],
        },
    )
    env = _install_gh_stub(tmp_path, rules)
    policy = _policy(tmp_path, [_enforced("omnimarket"), _unenforced("omniclaude")])
    result = _run(env, "--repo", "omnimarket", "--policy", str(policy))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "does not speak for the whole publishing set" in result.stdout
    assert "omniclaude" not in result.stdout


def test_unknown_repo_argument_is_exit_two(tmp_path: Path) -> None:
    env = _install_gh_stub(tmp_path, [])
    result = _run(
        env,
        "--repo",
        "omnifake",
        "--policy",
        str(_policy(tmp_path, [_enforced("omnimarket")])),
    )
    assert result.returncode == 2
    assert "not in the publishing set" in result.stderr


def test_script_is_spdx_stamped() -> None:
    head = SCRIPT.read_text(encoding="utf-8").splitlines()[:2]
    assert head[0].startswith("# SPDX-FileCopyrightText:")
    assert head[1] == "# SPDX-License-Identifier: MIT"


def test_module_docstring_names_the_measured_failure() -> None:
    """Doc discipline: the gate must carry its own reason for existing."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "OMN-18010" in source
    assert textwrap.dedent("omnimarket#2304") in source
