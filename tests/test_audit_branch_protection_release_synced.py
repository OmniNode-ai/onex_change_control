# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Behavioural tests for ``scripts/audit_branch_protection.sh`` (OMN-17491).

Background
----------
OMN-16289 made ``main`` a release-synced boundary on a set of repos: PRs land
on ``dev`` and ``release.yml`` fast-forwards ``main`` to the published tag's
commit. As part of that, ``required_status_checks`` on those mains was
deliberately emptied — a PR-shaped context can never report on a ref that is
only ever advanced by an automated fast-forward, so a required context there
gates nothing and merely blocks the sync.

The audit script was still asserting ``"CI Summary"`` on ``main`` for those
repos, so ``omni_home``'s ``branch-protection-guard`` workflow went red on
every PR from 2026-08-24 onward while the underlying configuration was
correct.

OMN-17186 re-points the assertion. For a release-synced repo, ``main`` must
now satisfy BOTH halves of the replacement protection:

1. ``required_status_checks`` is empty, and
2. an ACTIVE branch ruleset covers ``refs/heads/main``, carries the ``update``
   rule, and names at least one bypass actor (the release automation
   identity).

These tests drive the real script with a stubbed ``gh`` on ``PATH``, so they
exercise the shell logic rather than restating it. OMN-17491 adds an explicit
exception for ``onex_change_control`` ``main``: that governance branch must
retain both approving and code-owner review enforcement. Ordinary repositories
continue to assert the solo-dev invariant.

RED/GREEN
---------
``test_release_synced_main_passes_with_empty_contexts_and_ruleset`` is the RED
case: against the pre-OMN-17186 script it fails with
``"CI Summary" not found in required status checks``, because that script
asserted the retired invariant. It passes only once the release-synced
assertion is in place.

``test_release_synced_main_fails_without_ruleset`` pins the other direction:
empty contexts with no push restriction is an UNPROTECTED main and must stay
red. It is the guard against "make it green by deleting the assertion".
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "audit_branch_protection.sh"

# A repo in RELEASE_SYNCED_MAIN_REPOS, and one that is not.
RELEASE_SYNCED_REPO = "omnibase_core"
ORDINARY_REPO = "omniclaude"

_PROTECTION_NO_CONTEXTS = {
    "required_status_checks": {"strict": False, "contexts": [], "checks": []},
    "enforce_admins": {"enabled": True},
}
_PROTECTION_CI_SUMMARY = {
    "required_status_checks": {
        "strict": False,
        "contexts": ["CI Summary", "verify / verify"],
        "checks": [
            {"context": "CI Summary"},
            {"context": "verify / verify"},
        ],
    },
    "enforce_admins": {"enabled": True},
}
_REPO_SETTINGS = {"delete_branch_on_merge": True}
_GQL_RULES = {
    "data": {
        "repository": {
            "branchProtectionRules": {
                "nodes": [
                    {
                        "pattern": "main",
                        "requiresApprovingReviews": False,
                        "requiresCodeOwnerReviews": False,
                    },
                    {
                        "pattern": "dev",
                        "requiresApprovingReviews": False,
                        "requiresCodeOwnerReviews": False,
                    },
                ]
            }
        }
    }
}

_GQL_REVIEW_GATED_MAIN = {
    "data": {
        "repository": {
            "branchProtectionRules": {
                "nodes": [
                    {
                        "pattern": "main",
                        "requiresApprovingReviews": True,
                        "requiresCodeOwnerReviews": True,
                    },
                    {
                        "pattern": "dev",
                        "requiresApprovingReviews": False,
                        "requiresCodeOwnerReviews": False,
                    },
                ]
            }
        }
    }
}

# An active ruleset that genuinely restricts who may advance refs/heads/main.
_RULESET_RESTRICTING_MAIN = {
    "id": 21864685,
    "name": "OMN-16289 release-synced main: restrict pushes to release automation",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
    "rules": [{"type": "update"}, {"type": "non_fast_forward"}],
    "bypass_actors": [
        {"actor_id": 4361937, "actor_type": "Integration", "bypass_mode": "always"}
    ],
}
# Present in every fixture: the disabled legacy Merge Queue ruleset. The
# repo-level "Merge Queue ruleset exists" check matches on NAME only, so this
# must never be mistaken for the push restriction.
_RULESET_MERGE_QUEUE_DISABLED = {
    "id": 13269695,
    "name": "Merge Queue",
    "target": "branch",
    "enforcement": "disabled",
    "conditions": {"ref_name": {"include": ["refs/heads/dev"], "exclude": []}},
    "rules": [{"type": "merge_queue"}],
    "bypass_actors": [],
}


def _write_gh_stub(tmp_path: Path, fixtures: dict[str, object]) -> Path:
    """Write a fake ``gh`` that answers from ``fixtures`` and put it on PATH.

    Keys are the API paths the script requests (or the literal ``graphql``).
    An unknown path exits non-zero, exactly as the real ``gh`` does, so a test
    can never pass by accident on a route the script did not expect.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    payload = tmp_path / "fixtures.json"
    payload.write_text(json.dumps(fixtures))

    stub = bindir / "gh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "# Stubbed gh for audit_branch_protection.sh tests.\n"
        'if [[ "$1" != "api" ]]; then exit 1; fi\n'
        "shift\n"
        'key="$1"\n'
        f'python3 - "$key" "{payload}" <<\'PY\'\n'
        "import json, sys\n"
        "key, path = sys.argv[1], sys.argv[2]\n"
        "data = json.load(open(path))\n"
        "if key not in data:\n"
        "    sys.stderr.write('stub gh: no fixture for %s\\n' % key)\n"
        "    raise SystemExit(1)\n"
        "print(json.dumps(data[key]))\n"
        "PY\n"
    )
    stub.chmod(0o755)
    return bindir


def _run_audit(
    tmp_path: Path,
    repo: str,
    fixtures: dict[str, object],
    branches: str = "main",
) -> subprocess.CompletedProcess[str]:
    bindir = _write_gh_stub(tmp_path, fixtures)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["BRANCH_PROTECTION_AUDIT_REPOS"] = repo
    env["BRANCH_PROTECTION_AUDIT_BRANCHES"] = branches
    env.pop("BRANCH_PROTECTION_AUDIT_JSONL", None)
    return subprocess.run(
        ["bash", str(AUDIT_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        check=False,
    )


def _fixtures(
    repo: str,
    protection: dict[str, object],
    rulesets: list[dict[str, object]],
    gql_rules: Mapping[str, object] | None = None,
) -> dict[str, object]:
    base = f"repos/OmniNode-ai/{repo}"
    fx: dict[str, object] = {
        "graphql": gql_rules if gql_rules is not None else _GQL_RULES,
        f"{base}/branches/main/protection": protection,
        f"{base}/branches/dev/protection": _PROTECTION_CI_SUMMARY,
        base: _REPO_SETTINGS,
        f"{base}/rulesets": rulesets,
    }
    for rs in rulesets:
        fx[f"{base}/rulesets/{rs['id']}"] = rs
    return fx


@pytest.mark.unit
def test_release_synced_main_passes_with_empty_contexts_and_ruleset(
    tmp_path: Path,
) -> None:
    """RED before OMN-17186: the old script demanded "CI Summary" here."""
    result = _run_audit(
        tmp_path,
        RELEASE_SYNCED_REPO,
        _fixtures(
            RELEASE_SYNCED_REPO,
            _PROTECTION_NO_CONTEXTS,
            [_RULESET_MERGE_QUEUE_DISABLED, _RULESET_RESTRICTING_MAIN],
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "required_status_checks is empty" in result.stdout
    assert "restricts updates to refs/heads/main" in result.stdout
    # The retired invariant must no longer be asserted on this main.
    assert '"CI Summary" not found' not in result.stdout


@pytest.mark.unit
def test_release_synced_main_fails_without_ruleset(tmp_path: Path) -> None:
    """Empty contexts with no push restriction is an unprotected main."""
    result = _run_audit(
        tmp_path,
        RELEASE_SYNCED_REPO,
        _fixtures(
            RELEASE_SYNCED_REPO,
            _PROTECTION_NO_CONTEXTS,
            [_RULESET_MERGE_QUEUE_DISABLED],
        ),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "no active ruleset restricts updates to refs/heads/main" in result.stdout


@pytest.mark.unit
def test_release_synced_main_fails_when_ruleset_has_no_bypass_actor(
    tmp_path: Path,
) -> None:
    """A restriction nobody can bypass freezes the release boundary."""
    frozen = dict(_RULESET_RESTRICTING_MAIN)
    frozen["bypass_actors"] = []
    result = _run_audit(
        tmp_path,
        RELEASE_SYNCED_REPO,
        _fixtures(
            RELEASE_SYNCED_REPO,
            _PROTECTION_NO_CONTEXTS,
            [_RULESET_MERGE_QUEUE_DISABLED, frozen],
        ),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "no active ruleset restricts updates to refs/heads/main" in result.stdout


@pytest.mark.unit
def test_release_synced_main_fails_when_contexts_are_reintroduced(
    tmp_path: Path,
) -> None:
    """A required PR context on a fast-forward-only ref blocks the sync."""
    result = _run_audit(
        tmp_path,
        RELEASE_SYNCED_REPO,
        _fixtures(
            RELEASE_SYNCED_REPO,
            _PROTECTION_CI_SUMMARY,
            [_RULESET_MERGE_QUEUE_DISABLED, _RULESET_RESTRICTING_MAIN],
        ),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "release-synced main carries" in result.stdout


@pytest.mark.unit
def test_ordinary_repo_still_requires_ci_summary_on_main(tmp_path: Path) -> None:
    """The compliant set keeps the original main assertion — unchanged."""
    passing = _run_audit(
        tmp_path / "pass",
        ORDINARY_REPO,
        _fixtures(
            ORDINARY_REPO,
            _PROTECTION_CI_SUMMARY,
            [_RULESET_MERGE_QUEUE_DISABLED],
        ),
    )
    assert passing.returncode == 0, passing.stdout + passing.stderr
    assert '"CI Summary" is a required status check' in passing.stdout

    failing = _run_audit(
        tmp_path / "fail",
        ORDINARY_REPO,
        _fixtures(
            ORDINARY_REPO,
            _PROTECTION_NO_CONTEXTS,
            [_RULESET_MERGE_QUEUE_DISABLED, _RULESET_RESTRICTING_MAIN],
        ),
    )
    assert failing.returncode == 1, failing.stdout + failing.stderr
    assert '"CI Summary" not found in required status checks' in failing.stdout


@pytest.mark.unit
def test_review_gated_occ_main_requires_approving_and_code_owner_reviews(
    tmp_path: Path,
) -> None:
    """The OCC governance main keeps ordinary checks plus review enforcement."""
    result = _run_audit(
        tmp_path,
        "onex_change_control",
        _fixtures(
            "onex_change_control",
            _PROTECTION_CI_SUMMARY,
            [_RULESET_MERGE_QUEUE_DISABLED],
            gql_rules=_GQL_REVIEW_GATED_MAIN,
        ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "approving and code-owner reviews are enforced" in result.stdout


@pytest.mark.unit
def test_review_gated_occ_main_fails_without_code_owner_reviews(
    tmp_path: Path,
) -> None:
    """Approving reviews without the code-owner gate are insufficient."""
    gql_rules = json.loads(json.dumps(_GQL_REVIEW_GATED_MAIN))
    gql_rules["data"]["repository"]["branchProtectionRules"]["nodes"][0][
        "requiresCodeOwnerReviews"
    ] = False
    result = _run_audit(
        tmp_path,
        "onex_change_control",
        _fixtures(
            "onex_change_control",
            _PROTECTION_CI_SUMMARY,
            [_RULESET_MERGE_QUEUE_DISABLED],
            gql_rules=gql_rules,
        ),
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "review-gated main requires approving and code-owner reviews" in result.stdout
    )


@pytest.mark.unit
def test_main_audit_exempt_repo_skips_main_but_keeps_dev(tmp_path: Path) -> None:
    """omnibase_compat is a temporary repo: main is not audited, dev still is."""
    repo = "omnibase_compat"
    result = _run_audit(
        tmp_path,
        repo,
        _fixtures(repo, _PROTECTION_NO_CONTEXTS, [_RULESET_MERGE_QUEUE_DISABLED]),
        branches="main,dev",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[main] SKIP: main not audited" in result.stdout
    assert '[dev] PASS: "CI Summary" is a required status check' in result.stdout


@pytest.mark.unit
def test_release_synced_and_exempt_sets_are_disjoint() -> None:
    """A repo cannot be both release-synced-asserted and unaudited on main."""
    text = AUDIT_SCRIPT.read_text()

    def _array(name: str) -> set[str]:
        start = text.index(f"{name}=(")
        end = text.index(")", start)
        return set(text[start + len(name) + 2 : end].split())

    synced = _array("RELEASE_SYNCED_MAIN_REPOS")
    exempt = _array("MAIN_AUDIT_EXEMPT_REPOS")
    assert synced, "release-synced set must not be empty"
    assert not (synced & exempt), f"repo in both sets: {sorted(synced & exempt)}"
