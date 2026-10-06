# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""An enforced merge-queue ruleset carries no bypass actor (OMN-19929).

Background
----------
On 2026-09-28 omnibase_infra#4197 was removed from the ``dev`` merge queue for
failed checks at 14:15Z and then merged by a direct REST call at 14:27Z. GitHub
accepted it because the omnibase_infra "Merge Queue" ruleset listed one bypass
actor, the repository Admin role (``RepositoryRole`` 5) in ``pull_request``
mode, so an admin login could merge outside the queue. The bypass was removed
the same day (plan knowledge-base#98, decision D1). This audit keeps it removed.

A ruleset read with a token that cannot administer the repository comes back
with no ``bypass_actors`` key at all, which looks exactly like a clean ruleset
to a check that treats absence as empty. The audit therefore fails on an
unreadable bypass list as well as on a non-empty one: prevention is claimed
only when the list is read and shows no bypass.

Table
-----
Each case runs the real script against a stubbed ``gh``:

* one bypass actor on an active merge-queue ruleset: FAIL, names the actor;
* no ``bypass_actors`` key (unreadable): FAIL, says unreadable;
* the detail read itself fails: FAIL;
* an empty bypass list: PASS;
* a disabled merge-queue ruleset with a bypass actor: not judged (GitHub does
  not enforce it), PASS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.test_audit_branch_protection_release_synced import (
    _GQL_RULES,
    _PROTECTION_CI_SUMMARY,
    _REPO_SETTINGS,
    _RULESET_RESTRICTING_MAIN,
    _run_audit,
)

if TYPE_CHECKING:
    from pathlib import Path

REPO = "omnibase_infra"

_ADMIN_ROLE_BYPASS = {
    "actor_id": 5,
    "actor_type": "RepositoryRole",
    "bypass_mode": "pull_request",
}


def _queue_ruleset(**overrides: object) -> dict[str, object]:
    ruleset: dict[str, object] = {
        "id": 13269702,
        "name": "Merge Queue",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [{"type": "merge_queue", "parameters": {"merge_method": "SQUASH"}}],
        "bypass_actors": [],
    }
    ruleset.update(overrides)
    return ruleset


def _unreadable_queue_ruleset() -> dict[str, object]:
    ruleset = _queue_ruleset()
    del ruleset["bypass_actors"]
    return ruleset


def _fixtures(
    queue_ruleset: dict[str, object], *, detail_readable: bool = True
) -> dict[str, object]:
    base = f"repos/OmniNode-ai/{REPO}"
    rulesets = [queue_ruleset, _RULESET_RESTRICTING_MAIN]
    fx: dict[str, object] = {
        "graphql": _GQL_RULES,
        f"{base}/branches/dev/protection": _PROTECTION_CI_SUMMARY,
        base: _REPO_SETTINGS,
        # The list endpoint carries no rules and no bypass list.
        f"{base}/rulesets": [
            {k: v for k, v in rs.items() if k not in ("rules", "bypass_actors")}
            for rs in rulesets
        ],
        f"{base}/rulesets/{_RULESET_RESTRICTING_MAIN['id']}": _RULESET_RESTRICTING_MAIN,
    }
    if detail_readable:
        fx[f"{base}/rulesets/{queue_ruleset['id']}"] = queue_ruleset
    return fx


@pytest.mark.unit
def test_a_bypass_actor_on_an_active_queue_ruleset_fails(tmp_path: Path) -> None:
    """The live omnibase_infra shape before D1: admin role in pull_request mode."""
    result = _run_audit(
        tmp_path,
        REPO,
        _fixtures(_queue_ruleset(bypass_actors=[_ADMIN_ROLE_BYPASS])),
        branches="dev",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "merge-queue ruleset 13269702 has bypass actor" in result.stdout
    assert "RepositoryRole:5:pull_request" in result.stdout


@pytest.mark.unit
def test_an_unreadable_bypass_list_fails(tmp_path: Path) -> None:
    """No bypass_actors key: the token cannot read the list, so no claim."""
    result = _run_audit(
        tmp_path, REPO, _fixtures(_unreadable_queue_ruleset()), branches="dev"
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "bypass list of ruleset 13269702 is unreadable" in result.stdout


@pytest.mark.unit
def test_an_unfetchable_ruleset_detail_fails(tmp_path: Path) -> None:
    result = _run_audit(
        tmp_path,
        REPO,
        _fixtures(_queue_ruleset(), detail_readable=False),
        branches="dev",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ruleset 13269702 is unreadable" in result.stdout


@pytest.mark.unit
def test_an_empty_bypass_list_passes(tmp_path: Path) -> None:
    """The state after D1."""
    result = _run_audit(tmp_path, REPO, _fixtures(_queue_ruleset()), branches="dev")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "merge-queue ruleset 13269702 has no bypass actor" in result.stdout


@pytest.mark.unit
def test_a_disabled_queue_ruleset_is_not_judged(tmp_path: Path) -> None:
    """GitHub does not enforce a disabled ruleset, so its list gates nothing."""
    disabled = _queue_ruleset(
        enforcement="disabled", bypass_actors=[_ADMIN_ROLE_BYPASS]
    )
    result = _run_audit(tmp_path, REPO, _fixtures(disabled), branches="dev")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no enforced merge-queue ruleset" in result.stdout
