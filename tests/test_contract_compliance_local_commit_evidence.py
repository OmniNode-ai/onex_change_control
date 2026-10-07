# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-20504 -- same-repo commit-existence evidence is answered from local git.

2,689 of the 2,847 checks in ``contracts/OMN-14888.yaml`` are exactly
``gh api repos/OmniNode-ai/onex_change_control/commits/<sha> --jq .sha``. Each one
spent a GitHub App REST call to ask whether a commit exists in the repository the
runner already has checked out, and a PR touching that contract spent the
installation's whole hourly quota (run 37171017353). The compliance check now
asks the checkout instead, with ONE batched ``git fetch`` for shas the checkout
lacks. Every other shape still goes through ``gh``.

Real ``git`` repositories, a counting fake ``gh`` and a counting ``git`` wrapper
sit first on PATH; nothing here touches the network.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from omnibase_core.validators.no_unguarded_git_subprocess import (
    scrub_git_location_env,
)

from onex_change_control.scripts import contract_compliance_check as mod

if TYPE_CHECKING:
    from pathlib import Path

_REPO = "OmniNode-ai/onex_change_control"
_PR = 12717
_TICKET = "OMN-20504"

_FAKE_GH = """#!/bin/bash
echo "$*" >> "$FAKE_GH_LOG"
if [ -n "$FAKE_GH_QUOTA" ]; then
  echo '{"message": "API rate limit exceeded for installation ID 1."}' >&2
  exit 1
fi
sha=$(echo "$*" | grep -oE 'commits/[0-9a-fA-F]+' | head -1 | cut -d/ -f2)
if [ -n "$sha" ]; then
  for unknown in $FAKE_GH_UNKNOWN; do
    if [ "$unknown" = "$sha" ]; then
      echo '{"message": "No commit found for SHA", "status": "422"}' >&2
      exit 1
    fi
  done
  case "$*" in
    *"--jq .sha"*) echo "$sha"; exit 0 ;;
    *"--jq .commit.message"*) echo "message"; exit 0 ;;
  esac
fi
echo '{"state": "OPEN"}'
exit 0
"""

_FAKE_GIT = """#!/bin/bash
echo "$*" >> "$FAKE_GIT_LOG"
exec "$REAL_GIT" "$@"
"""


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
        env=scrub_git_location_env(os.environ),
    )
    return result.stdout.strip()


def _make_repo(path: Path, count: int, tag: str) -> list[str]:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    shas = []
    for i in range(count):
        _git(path, "commit", "-q", "--allow-empty", "-m", f"{tag} commit {i}")
        shas.append(_git(path, "rev-parse", "HEAD"))
    return shas


def _commit_check(sha: str, repo: str = _REPO, jq: str = ".sha") -> str:
    return f"gh api repos/{repo}/commits/{sha} --jq {jq}"


def _write_contract(contracts_dir: Path, check_values: list[str]) -> None:
    items: list[dict[str, Any]] = [
        {
            "id": f"item-{i}",
            "description": f"item {i}",
            "source": "generated",
            "checks": [{"check_type": "command", "check_value": value}],
        }
        for i, value in enumerate(check_values)
    ]
    contract = {
        "schema_version": "1.0.0",
        "ticket_id": _TICKET,
        "title": "local commit evidence fixture",
        "summary": "local commit evidence fixture",
        "is_seam_ticket": False,
        "interface_change": False,
        "interfaces_touched": [],
        "evidence_requirements": [],
        "emergency_bypass": {
            "enabled": False,
            "justification": "",
            "follow_up_ticket_id": "",
        },
        "dod_evidence": items,
    }
    contracts_dir.mkdir(parents=True, exist_ok=True)
    (contracts_dir / f"{_TICKET}.yaml").write_text(yaml.safe_dump(contract))


class _Bench:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = tmp_path
        self.gh_log = tmp_path / "gh.log"
        self.git_log = tmp_path / "git.log"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for name, body in (("gh", _FAKE_GH), ("git", _FAKE_GIT)):
            script = bin_dir / name
            script.write_text(body)
            script.chmod(script.stat().st_mode | stat.S_IEXEC)
        real_git = shutil.which("git")
        assert real_git is not None
        monkeypatch.setenv("REAL_GIT", real_git)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("FAKE_GH_LOG", str(self.gh_log))
        monkeypatch.setenv("FAKE_GIT_LOG", str(self.git_log))
        monkeypatch.delenv("FAKE_GH_UNKNOWN", raising=False)
        monkeypatch.delenv("FAKE_GH_QUOTA", raising=False)
        monkeypatch.setattr(mod, "_extract_ticket_id", lambda _pr, _repo: _TICKET)
        monkeypatch.setattr(
            mod, "_pr_changed_paths", lambda _pr, _repo: frozenset({"src/x.py"})
        )
        self.monkeypatch = monkeypatch

    @property
    def gh_calls(self) -> list[str]:
        return self.gh_log.read_text().splitlines() if self.gh_log.exists() else []

    @property
    def fetch_calls(self) -> list[str]:
        lines = self.git_log.read_text().splitlines() if self.git_log.exists() else []
        return [line for line in lines if " fetch " in f" {line} "]

    def run(self, workspace_repo: Path, check_values: list[str]) -> int:
        """Run the real check; the contract sits in the repo's contracts/, as in CI."""
        contracts_dir = workspace_repo / "contracts"
        _write_contract(contracts_dir, check_values)
        return mod.run_compliance_check(_PR, _REPO, contracts_dir, workspace_repo)


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Bench:
    return _Bench(tmp_path, monkeypatch)


@pytest.mark.unit
def test_same_repo_commit_checks_resolve_from_local_git_without_gh(
    bench: _Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC1: five commit-existence items, every sha in the checkout, zero gh calls."""
    repo = bench.root / "occ"
    shas = _make_repo(repo, 5, "occ")

    rc = bench.run(repo, [_commit_check(sha) for sha in shas])
    out = capsys.readouterr().out

    assert bench.gh_calls == []
    assert rc == 0, out
    assert f"[SUMMARY] {_TICKET}: 5/5 PASS" in out
    assert "local-git commit evidence: 5 resolved locally, 0 fetched, 0 missing" in out
    assert bench.fetch_calls == []


@pytest.mark.unit
def test_sha_in_no_repo_blocks_like_a_gh_404_and_makes_no_gh_call(
    bench: _Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC2: a sha nobody has fails the run exactly as the 404/422 does today."""
    repo = bench.root / "occ"
    origin = bench.root / "origin"
    good = _make_repo(repo, 2, "occ")
    _make_repo(origin, 1, "origin")
    _git(repo, "remote", "add", "origin", f"file://{origin}")
    nowhere = "0" * 39 + "7"
    bench.monkeypatch.setenv("FAKE_GH_UNKNOWN", nowhere)

    rc = bench.run(repo, [_commit_check(good[0]), _commit_check(nowhere)])
    out = capsys.readouterr().out

    assert bench.gh_calls == []
    assert rc == 1, out
    assert f"[SUMMARY] {_TICKET}: 1/2 PASS, 0 WARN, 1 BLOCK" in out
    blocked = [line for line in out.splitlines() if "[X] command:" in line]
    assert len(blocked) == 1
    assert f"Command failed (exit 1): {_commit_check(nowhere)[:80]}" in blocked[0]
    assert "local git" in out
    assert "1 resolved locally, 0 fetched, 1 missing" in out


@pytest.mark.unit
def test_shas_missing_locally_are_fetched_from_origin_in_one_invocation(
    bench: _Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC2: commits the checkout lacks but origin serves PASS after ONE fetch."""
    repo = bench.root / "occ"
    origin = bench.root / "origin"
    local = _make_repo(repo, 1, "occ")
    served = _make_repo(origin, 3, "origin")
    _git(repo, "remote", "add", "origin", f"file://{origin}")

    rc = bench.run(repo, [_commit_check(s) for s in [*local, *served]])
    out = capsys.readouterr().out

    assert bench.gh_calls == []
    assert rc == 0, out
    assert f"[SUMMARY] {_TICKET}: 4/4 PASS" in out
    assert "local-git commit evidence: 1 resolved locally, 3 fetched, 0 missing" in out
    assert len(bench.fetch_calls) == 1, bench.fetch_calls
    for sha in served:
        assert sha in bench.fetch_calls[0]


@pytest.mark.unit
def test_every_other_shape_still_goes_through_gh(
    bench: _Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC3: only the exact shape is intercepted; the rest reach gh as before."""
    repo = bench.root / "occ"
    sha = _make_repo(repo, 1, "occ")[0]
    (repo / "f.txt").write_text("x\n")
    gh_shapes = [
        f"gh api repos/OmniNode-ai/omnimarket/commits/{sha} --jq .sha",
        _commit_check(sha, jq=".commit.message"),
        f"{_commit_check(sha)} | grep -q .",
        _commit_check(sha.upper()),
        _commit_check(sha[:7]),
        f"gh pr view 1 --repo {_REPO} --json state",
    ]

    bench.run(repo, [*gh_shapes, "grep -q x f.txt"])
    out = capsys.readouterr().out

    assert len(bench.gh_calls) == len(gh_shapes), (bench.gh_calls, out)
    assert "local-git commit evidence" not in out


@pytest.mark.unit
def test_surrounding_whitespace_is_stripped_but_nothing_else_matches(
    bench: _Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = bench.root / "occ"
    sha = _make_repo(repo, 1, "occ")[0]

    bench.run(
        repo,
        [
            f"  {_commit_check(sha)}\n",
            f"{_commit_check(sha)} ",
            f"{_commit_check(sha)}; true",
            f"gh  api repos/{_REPO}/commits/{sha} --jq .sha",
        ],
    )
    capsys.readouterr()

    assert len(bench.gh_calls) == 2


@pytest.mark.unit
def test_quota_halt_still_governs_gh_items_and_is_never_consulted_for_local_ones(
    bench: _Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    """The OMN-20503 breaker trips on a gh item; local items pass regardless."""
    repo = bench.root / "occ"
    shas = _make_repo(repo, 3, "occ")
    bench.monkeypatch.setenv("FAKE_GH_QUOTA", "1")
    gh_items = [
        f"gh api repos/OmniNode-ai/omnimarket/commits/{sha} --jq .sha"
        for sha in ("a" * 40, "b" * 40)
    ]

    rc = bench.run(repo, [gh_items[0], *[_commit_check(s) for s in shas], gh_items[1]])
    out = capsys.readouterr().out

    assert len(bench.gh_calls) == 1
    assert "1 QUOTA_EXHAUSTED" in out
    assert f"[SUMMARY] {_TICKET}: 3/5 PASS" in out
    assert "3 resolved locally, 0 fetched, 0 missing" in out
    assert rc == 1


@pytest.mark.unit
def test_git_that_cannot_answer_falls_back_to_the_gh_path(
    bench: _Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    """A checkout with no origin cannot fetch: gh keeps answering, unchanged."""
    repo = bench.root / "occ"
    local = _make_repo(repo, 1, "occ")[0]
    absent = "1" * 40

    rc = bench.run(repo, [_commit_check(local), _commit_check(absent)])
    out = capsys.readouterr().out

    assert len(bench.gh_calls) == 1
    assert absent in bench.gh_calls[0]
    assert rc == 0, out
    assert "1 resolved locally, 0 fetched, 0 missing" in out


@pytest.mark.unit
def test_contracts_dir_outside_a_git_work_tree_uses_gh_for_everything(
    bench: _Bench, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = bench.root / "not-a-repo"
    plain.mkdir()
    sha = "2" * 40

    rc = bench.run(plain, [_commit_check(sha)])
    out = capsys.readouterr().out

    assert len(bench.gh_calls) == 1
    assert rc == 0, out
    assert "local-git commit evidence" not in out
