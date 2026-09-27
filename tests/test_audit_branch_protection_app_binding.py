# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Behavioural tests for the required-check App-binding assertion (OMN-19511).

Background
----------
``required_status_checks.checks[]`` carries an ``app_id`` per required
context: the specific GitHub App whose check runs GitHub will accept as
satisfying that context. A null ``app_id`` is GitHub's "Any source" setting —
ANY App or Action with ``checks:write`` on the repository can post a check
run with that context and satisfy the requirement, not only the intended CI
producer. That is a required check in name only: it cannot be trusted to have
come from the pipeline it is meant to gate.

Live state on ``omniclaude``'s ``dev`` (read 2026-09-24, ``gh api
repos/OmniNode-ai/omniclaude/branches/dev/protection``): every required
context resolves to ``app_id: 15368`` except ``"advisory-job-gate /
advisory-job-gate"``, which reads ``app_id: null``. The audit script had no
assertion that would have caught this drift — it checked specific named
contexts for presence, never every context's binding — so the gap shipped
silently.

RED/GREEN
---------
``test_dev_with_unbound_required_check_fails`` is the RED case: reproduces
the live omniclaude/dev shape (one context with ``app_id: null`` among
several bound ones) and pins that the audit must fail it and name the
context. Against the pre-OMN-19511 script this passes trivially, because no
assertion existed to fail.

``test_dev_with_all_checks_bound_passes`` is the GREEN partner: the same
context list with every ``app_id`` populated must be compliant. The pair
together is what stops the assertion being satisfied by deleting it.

``test_release_synced_main_is_unaffected`` pins that the new assertion does
not reach a release-synced main, whose ``required_status_checks`` is empty by
design (OMN-16289) — an empty ``checks[]`` list has nothing to be unbound.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.test_audit_branch_protection_release_synced import (
    _GQL_RULES,
    _REPO_SETTINGS,
    _RULESET_MERGE_QUEUE_DISABLED,
    _RULESET_RESTRICTING_MAIN,
    _run_audit,
)

if TYPE_CHECKING:
    from pathlib import Path

# omniclaude: release-synced main (OMN-16642), dev carries the live gap.
REPO = "omniclaude"

_DEV_WITH_UNBOUND_CHECK = {
    "required_status_checks": {
        "strict": False,
        "contexts": [
            "CI Summary",
            "verify / verify",
            "Lane Identity Gate",
            "advisory-job-gate / advisory-job-gate",
        ],
        "checks": [
            {"context": "CI Summary", "app_id": 15368},
            {"context": "verify / verify", "app_id": 15368},
            {"context": "Lane Identity Gate", "app_id": 15368},
            {"context": "advisory-job-gate / advisory-job-gate", "app_id": None},
        ],
    },
    "enforce_admins": {"enabled": True},
}
_DEV_WITH_ALL_CHECKS_BOUND = {
    "required_status_checks": {
        "strict": False,
        "contexts": [
            "CI Summary",
            "verify / verify",
            "Lane Identity Gate",
            "advisory-job-gate / advisory-job-gate",
        ],
        "checks": [
            {"context": "CI Summary", "app_id": 15368},
            {"context": "verify / verify", "app_id": 15368},
            {"context": "Lane Identity Gate", "app_id": 15368},
            {"context": "advisory-job-gate / advisory-job-gate", "app_id": 15368},
        ],
    },
    "enforce_admins": {"enabled": True},
}
# Release-synced main: empty by design, nothing to be unbound.
_MAIN_RELEASE_SYNCED = {
    "required_status_checks": {"strict": False, "contexts": [], "checks": []},
    "enforce_admins": {"enabled": True},
}


def _fixtures(dev_protection: dict[str, object]) -> dict[str, object]:
    base = f"repos/OmniNode-ai/{REPO}"
    rulesets = [_RULESET_MERGE_QUEUE_DISABLED, _RULESET_RESTRICTING_MAIN]
    fx: dict[str, object] = {
        "graphql": _GQL_RULES,
        f"{base}/branches/main/protection": _MAIN_RELEASE_SYNCED,
        f"{base}/branches/dev/protection": dev_protection,
        base: _REPO_SETTINGS,
        f"{base}/rulesets": rulesets,
    }
    for rs in rulesets:
        fx[f"{base}/rulesets/{rs['id']}"] = rs
    return fx


@pytest.mark.unit
def test_dev_with_unbound_required_check_fails(tmp_path: Path) -> None:
    """Reproduces the live omniclaude/dev gap: a required context whose
    app_id reads null (GitHub's "Any source") is satisfiable by any App, not
    just the intended CI producer, and must fail the audit by name."""
    result = _run_audit(
        tmp_path,
        REPO,
        _fixtures(_DEV_WITH_UNBOUND_CHECK),
        branches="dev",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "advisory-job-gate / advisory-job-gate" in result.stdout
    assert "app_id=null" in result.stdout
    assert "Any source" in result.stdout


@pytest.mark.unit
def test_dev_with_all_checks_bound_passes(tmp_path: Path) -> None:
    """The remediated state: every required context names a reporting App."""
    result = _run_audit(
        tmp_path,
        REPO,
        _fixtures(_DEV_WITH_ALL_CHECKS_BOUND),
        branches="dev",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "every required status check is bound to a reporting App" in result.stdout


@pytest.mark.unit
def test_release_synced_main_is_unaffected(tmp_path: Path) -> None:
    """Release-synced main's required_status_checks is empty by design
    (OMN-16289); an empty checks[] has nothing to be unbound, so the new
    assertion must not leak a failure onto a compliant release-synced main."""
    result = _run_audit(
        tmp_path,
        REPO,
        _fixtures(_DEV_WITH_ALL_CHECKS_BOUND),
        branches="main",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "app_id" not in result.stdout
