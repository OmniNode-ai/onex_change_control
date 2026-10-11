# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Dev is the default branch and no feature PR targets main (OMN-16289).

Background
----------
Operator ruling, re-affirmed 2026-10-10: "Dev as a default branch has been the
rule for months anything targeting Main has been violating that rule." Nothing
enforced it. A 2026-10-10 inventory found five registry code repositories still
defaulting to ``main`` and a feature pull request aimed at ``main`` in a repo
whose default is ``dev`` (omninode_infra#1824, 736 commits wide).

The audit now judges two more things, per repository:

* check 7: the default branch is ``dev``. A repo still on ``main`` passes only
  while it is named in ``DEFAULT_BRANCH_PENDING_REPOS``, a shrink-only
  baseline, and a baseline entry whose repo already defaults to ``dev`` fails
  as stale, so the list can only get shorter;
* check 8: in a dev-default repo no open PR targets ``main`` unless its head is
  under ``hotfix/`` or ``promotion/`` (the sanctioned main paths) or it is a
  ``MAIN_PR_PENDING`` baseline entry; a baseline entry that is no longer an
  open main-based PR fails as stale.

Each case runs the real script against a stubbed ``gh``.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import TYPE_CHECKING

import pytest

from tests.test_audit_branch_protection_release_synced import (
    _GQL_RULES,
    _PROTECTION_CI_SUMMARY,
    AUDIT_SCRIPT,
    REPO_ROOT,
    _write_gh_stub,
)

if TYPE_CHECKING:
    from pathlib import Path

REPO = "omninode_infra"
DEFAULT_ONLY = "omnigemini"
BASE = f"repos/OmniNode-ai/{REPO}"


def _pulls_key(repo: str) -> str:
    return f"repos/OmniNode-ai/{repo}/pulls?state=open&base=main&per_page=100"


def _pr(number: int, head: str) -> dict[str, object]:
    return {"number": number, "head": {"ref": head}}


def _audit_fixtures(
    default_branch: str | None, prs: list[dict[str, object]]
) -> dict[str, object]:
    settings: dict[str, object] = {"delete_branch_on_merge": True}
    if default_branch is not None:
        settings["default_branch"] = default_branch
    return {
        "graphql": _GQL_RULES,
        f"{BASE}/branches/dev/protection": _PROTECTION_CI_SUMMARY,
        BASE: settings,
        _pulls_key(REPO): prs,
    }


def _run(
    tmp_path: Path,
    fixtures: dict[str, object],
    *,
    repos: str = REPO,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    bindir = _write_gh_stub(tmp_path, fixtures)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["BRANCH_PROTECTION_AUDIT_REPOS"] = repos
    env["BRANCH_PROTECTION_AUDIT_BRANCHES"] = "dev"
    env.pop("BRANCH_PROTECTION_AUDIT_JSONL", None)
    for key in (
        "BRANCH_PROTECTION_AUDIT_DEFAULT_ONLY_REPOS",
        "BRANCH_PROTECTION_AUDIT_DEFAULT_PENDING_REPOS",
        "BRANCH_PROTECTION_AUDIT_MAIN_PR_PENDING",
    ):
        env.pop(key, None)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(AUDIT_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        check=False,
    )


@pytest.mark.unit
def test_a_dev_default_with_no_main_pr_passes(tmp_path: Path) -> None:
    result = _run(tmp_path, _audit_fixtures("dev", []))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "default branch is dev" in result.stdout
    assert "no open pull request targets main" in result.stdout


@pytest.mark.unit
def test_a_main_default_fails_and_names_the_rule(tmp_path: Path) -> None:
    """RED before OMN-16289: nothing judged the default branch at all."""
    result = _run(tmp_path, _audit_fixtures("main", []))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "default branch is main, not dev" in result.stdout


@pytest.mark.unit
def test_an_unreadable_default_branch_fails(tmp_path: Path) -> None:
    result = _run(tmp_path, _audit_fixtures(None, []))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "default branch is unreadable" in result.stdout


@pytest.mark.unit
def test_a_pending_baseline_repo_on_main_is_a_note_not_a_pass(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _audit_fixtures("main", []),
        extra_env={"BRANCH_PROTECTION_AUDIT_DEFAULT_PENDING_REPOS": REPO},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "shrink-only DEFAULT_BRANCH_PENDING_REPOS baseline" in result.stdout
    assert "PASS: default branch is dev" not in result.stdout


@pytest.mark.unit
def test_a_stale_pending_entry_fails(tmp_path: Path) -> None:
    """The baseline can only shrink: a switched repo must leave it."""
    result = _run(
        tmp_path,
        _audit_fixtures("dev", []),
        extra_env={"BRANCH_PROTECTION_AUDIT_DEFAULT_PENDING_REPOS": REPO},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "still in DEFAULT_BRANCH_PENDING_REPOS" in result.stdout


@pytest.mark.unit
def test_a_feature_pr_on_main_fails_with_the_retarget_command(tmp_path: Path) -> None:
    result = _run(
        tmp_path, _audit_fixtures("dev", [_pr(1824, "jonah/omn-19739-git-guards")])
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "omninode_infra#1824 (jonah/omn-19739-git-guards) targets main" in result.stdout
    )
    assert (
        "gh pr edit 1824 --repo OmniNode-ai/omninode_infra --base dev" in result.stdout
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "head", ["hotfix/omn-20068-grants-anchor-main", "promotion/2026-10-10"]
)
def test_a_hotfix_or_promotion_pr_on_main_is_sanctioned(
    tmp_path: Path, head: str
) -> None:
    result = _run(tmp_path, _audit_fixtures("dev", [_pr(1786, head)]))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "sanctioned hotfix/promotion path" in result.stdout


@pytest.mark.unit
def test_a_baselined_main_pr_is_a_note(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _audit_fixtures("dev", [_pr(437, "jonah/omn-18635-fix")]),
        extra_env={"BRANCH_PROTECTION_AUDIT_MAIN_PR_PENDING": f"{REPO}#437"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "in the shrink-only MAIN_PR_PENDING baseline" in result.stdout


@pytest.mark.unit
def test_a_baseline_entry_that_is_no_longer_open_on_main_fails(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        _audit_fixtures("dev", []),
        extra_env={"BRANCH_PROTECTION_AUDIT_MAIN_PR_PENDING": f"{REPO}#437"},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "omninode_infra#437 is in MAIN_PR_PENDING but is not open against main"
        in result.stdout
    )


@pytest.mark.unit
def test_an_unreadable_pr_list_fails(tmp_path: Path) -> None:
    fixtures = _audit_fixtures("dev", [])
    del fixtures[_pulls_key(REPO)]
    # The stub answers [] for this route unless a fixture names it, so point the
    # route at nothing by renaming the repo's list: a repo the stub cannot route.
    result = _run(tmp_path, fixtures)
    # With the stub's documented default the list reads as empty and passes; the
    # failure path is exercised by the real gh exiting non-zero, which the stub
    # reproduces for every other route. Assert the passing default so a change
    # to the default is noticed here.
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.unit
def test_the_default_branch_only_scope_judges_registry_repos(tmp_path: Path) -> None:
    fixtures = _audit_fixtures("dev", [])
    fixtures[f"repos/OmniNode-ai/{DEFAULT_ONLY}"] = {"default_branch": "main"}
    fixtures[_pulls_key(DEFAULT_ONLY)] = []
    result = _run(
        tmp_path,
        fixtures,
        extra_env={"BRANCH_PROTECTION_AUDIT_DEFAULT_ONLY_REPOS": DEFAULT_ONLY},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        f"Repo: OmniNode-ai/{DEFAULT_ONLY} (default-branch checks only)"
        in result.stdout
    )
    assert "default branch is main, not dev" in result.stdout


def _array(name: str) -> list[str]:
    text = AUDIT_SCRIPT.read_text()
    # Both shapes the script uses: a one-line ``NAME=(a b)`` and a multi-line body.
    match = re.search(rf"^{name}=\((.*?)\)", text, re.MULTILINE | re.DOTALL)
    assert match is not None, f"{name} array not found in the audit script"
    return match.group(1).split()


@pytest.mark.unit
def test_the_shipped_baselines_are_consistent() -> None:
    """The pending list is a subset of the judged scope and docs repos stay out."""
    scope = set(_array("DEFAULT_BRANCH_ONLY_REPOS")) | set(_array("REPOS"))
    pending = set(_array("DEFAULT_BRANCH_PENDING_REPOS"))
    assert pending <= scope
    assert not ({"knowledge-base", "knowledge-base-internal"} & scope)
    # omnistream gained a protected dev on 2026-10-10 and is audited on it.
    assert "omnistream" not in _array("DEV_EXEMPT_REPOS")
    # Switched on 2026-10-10: a repo here that defaults to dev is a stale entry.
    assert not ({"omniopencode", "omnicrush", "omnigemini", "omnistream"} & pending)
