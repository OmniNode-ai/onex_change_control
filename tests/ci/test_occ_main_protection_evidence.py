# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""The recorded `main` branch-protection change adds a gate and drops nothing
(OMN-17437, leg 1).

Scope, stated up front so this is not mistaken for more than it is
--------------------------------------------------------------------
`onex_change_control@main` branch protection is **repo-admin configuration**,
not repo content. It was changed out-of-tree with `gh api`, and CI cannot
re-read it: `repository.branchProtectionRules` and
`repos/:owner/:repo/branches/main/protection` both require admin scope, and the
workflow `GITHUB_TOKEN` does not have it — a live query from CI returns
`FORBIDDEN`, which is exactly how the first revision of this ticket's contract
failed the Contract Compliance Check.

So this test verifies the **recorded** readbacks committed under
`evidence/OMN-17437/`, captured with operator-scope credentials at the moment of
the change:

* the BEFORE state genuinely had no review requirement at all;
* the AFTER state requires code-owner review;
* nothing else moved — every other control, and all 23 required status-check
  contexts, round-tripped unchanged.

That last assertion is the one worth having a test for. `PUT
/repos/:owner/:repo/branches/:branch/protection` **replaces the whole protection
resource**, so a hand-written body silently drops every control it omits. This
test is what proves the change was additive.

The live proof of the setting itself is the operator-scope readback captured in
`drift/dod_receipts/OMN-17437/dod-occ-main-requires-codeowner-review/`. This
test does not, and cannot, replace it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO_ROOT / "evidence" / "OMN-17437"
BEFORE = EVIDENCE_DIR / "occ-main-protection-before.json"
AFTER = EVIDENCE_DIR / "occ-main-protection-after.json"

#: The review controls leg 1 installs. CODEOWNERS on `main` carries
#: `grants/prod_promotion_grants.yaml @OmniNode-ai/platform-leads`, but without
#: `require_code_owner_reviews` that entry only auto-REQUESTS a reviewer — it
#: blocks nothing, which was the whole finding of OMN-17437.
_REQUIRED_REVIEW_CONTROLS = (
    "require_code_owner_reviews",
    "dismiss_stale_reviews",
    "require_last_push_approval",
)

#: Boolean-valued controls that must be identical BEFORE and AFTER. Each is
#: represented as `{"enabled": bool}` in the REST protection resource.
_UNCHANGED_TOGGLES = (
    "enforce_admins",
    "required_linear_history",
    "allow_force_pushes",
    "allow_deletions",
    "block_creations",
    "required_conversation_resolution",
    "lock_branch",
    "allow_fork_syncing",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        pytest.fail(
            f"{path} is missing — the OMN-17437 leg-1 branch-protection readback "
            "is the only durable record of what changed."
        )
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


@pytest.mark.unit
def test_before_state_had_no_review_requirement() -> None:
    """The finding this ticket rests on: `main` required no review at all."""
    before = _load(BEFORE)
    assert "required_pull_request_reviews" not in before, (
        "the recorded BEFORE readback already carries a review requirement, so it "
        "cannot be the pre-OMN-17437 state this ticket describes"
    )


@pytest.mark.unit
def test_after_state_requires_code_owner_review() -> None:
    """The gate the doctrine claimed already existed now actually exists."""
    after = _load(AFTER)
    reviews = after.get("required_pull_request_reviews")
    assert reviews is not None, (
        "AFTER readback has no required_pull_request_reviews — `main` still has "
        "no review requirement and the CODEOWNERS grants rule still blocks nothing"
    )
    missing = [c for c in _REQUIRED_REVIEW_CONTROLS if reviews.get(c) is not True]
    assert not missing, f"review controls not enabled on main: {missing}"


@pytest.mark.unit
def test_the_put_dropped_no_existing_control() -> None:
    """`PUT .../protection` replaces the whole resource — prove it was additive.

    This is the assertion that actually earns its keep: a whole-resource replace
    silently discards every control the request body omits, so "we added a gate"
    and "we did not quietly remove four others" are separate claims.
    """
    before, after = _load(BEFORE), _load(AFTER)

    before_checks = before["required_status_checks"]
    after_checks = after["required_status_checks"]
    assert sorted(after_checks["contexts"]) == sorted(before_checks["contexts"]), (
        "required status-check contexts changed; the PUT was not additive. "
        f"before={len(before_checks['contexts'])} after={len(after_checks['contexts'])}"
    )
    assert after_checks["strict"] == before_checks["strict"]

    drifted = {
        key: (before[key]["enabled"], after[key]["enabled"])
        for key in _UNCHANGED_TOGGLES
        if before[key]["enabled"] != after[key]["enabled"]
    }
    assert not drifted, f"controls changed as a side effect of the PUT: {drifted}"


@pytest.mark.unit
def test_admin_enforcement_survived() -> None:
    """A review gate an admin can wave through is not a gate."""
    after = _load(AFTER)
    assert after["enforce_admins"]["enabled"] is True
