# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Behavioural tests for the Lane Identity Gate assertion (OMN-18288).

Background
----------
``Lane Identity Gate`` reported on ``omniclaude``'s ``dev`` from 2026-09-13 and
could not block anything: the context was absent from
``required_status_checks``. A gate that reports and cannot block is advisory
detection, which is the enforcement gap ``omni_home/CLAUDE.md`` rule 5 names
directly, and lane friction-p5 left it as an unclaimed residual at its TERMINAL
row (``docs/tracking/ROLLING_WORK_LEDGER.md:7153``).

Acceptance criterion (a) asks for the context to be added to live branch
protection "with the audit's own expectation updated in the **same** change so
the guard does not immediately flag the new required context as drift". The
audit is the expectation surface, so this file is that half.

RED/GREEN
---------
``test_dev_missing_lane_identity_gate_fails`` is the RED case: against the
pre-OMN-18288 script it passes trivially, because no assertion existed to fail.
It is meaningful only once the assertion is in place.

``test_dev_with_lane_identity_gate_passes`` is its GREEN partner, and the two
together are what stops the assertion being satisfied by deleting it.

``test_lane_identity_gate_is_not_asserted_on_main`` and
``test_a_repo_outside_the_list_is_not_asserted`` pin the two deliberate
narrowings. Both matter for the same reason: asserting a context on a branch or
in a repo that cannot produce it wedges every pull request there, waiting on a
check that will never report. omniclaude's ``main`` is release-synced, so its
required set is empty by design (OMN-16289 / OMN-16642); and the module the
gate protects lives only in omniclaude.
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

# The one repo in LANE_IDENTITY_GATE_REQUIRED_REPOS. Its main is release-synced
# (OMN-16642), which is exactly why the assertion below is dev-only.
LANE_IDENTITY_REPO = "omniclaude"
# A repo that is NOT in the list and does not produce the context.
OTHER_REPO = "omnibase_core"

_DEV_WITHOUT_LANE_IDENTITY = {
    "required_status_checks": {
        "strict": False,
        "contexts": ["CI Summary", "verify / verify"],
        "checks": [{"context": "CI Summary"}, {"context": "verify / verify"}],
    },
    "enforce_admins": {"enabled": True},
}
_DEV_WITH_LANE_IDENTITY = {
    "required_status_checks": {
        "strict": False,
        "contexts": ["CI Summary", "verify / verify", "Lane Identity Gate"],
        "checks": [
            {"context": "CI Summary"},
            {"context": "verify / verify"},
            {"context": "Lane Identity Gate"},
        ],
    },
    "enforce_admins": {"enabled": True},
}
# Release-synced main: empty by design, and the assertion must not reach it.
_MAIN_RELEASE_SYNCED = {
    "required_status_checks": {"strict": False, "contexts": [], "checks": []},
    "enforce_admins": {"enabled": True},
}


def _fixtures(repo: str, dev_protection: dict[str, object]) -> dict[str, object]:
    base = f"repos/OmniNode-ai/{repo}"
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
def test_dev_with_lane_identity_gate_passes(tmp_path: Path) -> None:
    """The state this change puts omniclaude's dev into."""
    result = _run_audit(
        tmp_path,
        LANE_IDENTITY_REPO,
        _fixtures(LANE_IDENTITY_REPO, _DEV_WITH_LANE_IDENTITY),
        branches="dev",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"Lane Identity Gate" is a required status check' in result.stdout


@pytest.mark.unit
def test_dev_missing_lane_identity_gate_fails(tmp_path: Path) -> None:
    """The regression this assertion exists to catch: the context silently
    dropped back out of branch protection, leaving the gate advisory again with
    nothing anywhere reporting the change."""
    result = _run_audit(
        tmp_path,
        LANE_IDENTITY_REPO,
        _fixtures(LANE_IDENTITY_REPO, _DEV_WITHOUT_LANE_IDENTITY),
        branches="dev",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert '"Lane Identity Gate" not found in required status checks' in result.stdout
    assert "reports but cannot block" in result.stdout


@pytest.mark.unit
def test_lane_identity_gate_is_not_asserted_on_main(tmp_path: Path) -> None:
    """omniclaude's main is release-synced, so its required set is empty by
    design and a PR-shaped context asserted there would block the release sync
    rather than gate anything.

    The compliant release-synced main must therefore still exit 0 with the new
    assertion in place -- an assertion that leaked onto main would reproduce
    the 2026-08-24 nine-day red window on the Branch Protection Guard.
    """
    result = _run_audit(
        tmp_path,
        LANE_IDENTITY_REPO,
        _fixtures(LANE_IDENTITY_REPO, _DEV_WITH_LANE_IDENTITY),
        branches="main",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Lane Identity Gate" not in result.stdout


@pytest.mark.unit
def test_a_repo_outside_the_list_is_not_asserted(tmp_path: Path) -> None:
    """A repo that does not produce the context must not be asked for it.

    Asserting it there would demand a context that can never report, which
    wedges every pull request in that repo on a check nothing will ever post.
    A repo joins the list in the same change that gives it the workflow.
    """
    result = _run_audit(
        tmp_path,
        OTHER_REPO,
        _fixtures(OTHER_REPO, _DEV_WITHOUT_LANE_IDENTITY),
        branches="dev",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Lane Identity Gate" not in result.stdout
