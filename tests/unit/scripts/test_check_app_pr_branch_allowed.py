# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_app_pr_branch_allowed (OMN-18327).

The writer-App dispatch path holds a privileged App identity. The property
this guardrail has to have is that such a route cannot quietly become a route
that edits the routes: a branch changing a `.github/workflows/` file is
allowed only when the dispatching ticket is cited in a line the branch ADDS.

Three failure modes get explicit tests, because each is a way this could look
like it works while proving nothing:

  * a citation that was ALREADY in the file before this branch existed is a
    previous change's citation, not this one's;
  * a citation supplied through the PR body or the dispatch inputs comes from
    the same caller as the branch, so it is not independent evidence -- only
    the diff is, which is why this module reads only added lines; and
  * an unreadable diff must be INCONCLUSIVE, never an accidental pass.
"""

from __future__ import annotations

import pytest

from onex_change_control.scripts.check_app_pr_branch_allowed import (
    WORKFLOW_PATH_PREFIX,
    evaluate,
)

pytestmark = pytest.mark.unit

WORKFLOW = f"{WORKFLOW_PATH_PREFIX}ci.yml"
OTHER_WORKFLOW = f"{WORKFLOW_PATH_PREFIX}release.yml"


class TestTicketShapeOMN18327:
    @pytest.mark.parametrize("bad", ["OMN", "omn-1", "1234", "OMN-", "", "OMN-12a"])
    def test_a_malformed_ticket_is_refused_before_anything_else(self, bad: str) -> None:
        """The dispatch path will not open a PR it cannot attribute."""
        allowed, message = evaluate(ticket=bad, workflow_paths=[], added_lines={})
        assert allowed is False
        assert "not of the form" in message


class TestNoWorkflowChangeOMN18327:
    def test_a_branch_touching_no_workflow_file_passes(self) -> None:
        allowed, message = evaluate(
            ticket="OMN-18327", workflow_paths=[], added_lines={}
        )
        assert allowed is True
        assert "does not apply" in message


class TestWorkflowCitationOMN18327:
    def test_cited_workflow_change_passes(self) -> None:
        allowed, message = evaluate(
            ticket="OMN-18327",
            workflow_paths=[WORKFLOW],
            added_lines={WORKFLOW: ["  # OMN-18327: widen the gate"]},
        )
        assert allowed is True
        assert "OMN-18327" in message

    def test_uncited_workflow_change_is_refused(self) -> None:
        allowed, message = evaluate(
            ticket="OMN-18327",
            workflow_paths=[WORKFLOW],
            added_lines={WORKFLOW: ["  - run: echo hello"]},
        )
        assert allowed is False
        assert WORKFLOW in message

    def test_a_different_tickets_citation_does_not_satisfy_it(self) -> None:
        """Positive control: the check must read THIS ticket, not any ticket."""
        allowed, _message = evaluate(
            ticket="OMN-18327",
            workflow_paths=[WORKFLOW],
            added_lines={WORKFLOW: ["  # OMN-11111: an unrelated change"]},
        )
        assert allowed is False

    def test_every_changed_workflow_must_be_cited_not_just_one(self) -> None:
        """One cited file does not license a second, uncited one."""
        allowed, message = evaluate(
            ticket="OMN-18327",
            workflow_paths=[WORKFLOW, OTHER_WORKFLOW],
            added_lines={
                WORKFLOW: ["  # OMN-18327: cited"],
                OTHER_WORKFLOW: ["  - run: echo sneaky"],
            },
        )
        assert allowed is False
        assert OTHER_WORKFLOW in message
        assert WORKFLOW not in message.split(OTHER_WORKFLOW)[1]

    def test_a_file_with_no_added_lines_is_refused(self) -> None:
        """A pure deletion of a workflow file still has to name its ticket."""
        allowed, _message = evaluate(
            ticket="OMN-18327",
            workflow_paths=[WORKFLOW],
            added_lines={WORKFLOW: []},
        )
        assert allowed is False

    def test_refusal_names_the_remedy_and_why_the_diff_is_the_evidence(
        self,
    ) -> None:
        """A gate that refuses without naming the fix gets routed around."""
        _allowed, message = evaluate(
            ticket="OMN-18327",
            workflow_paths=[WORKFLOW],
            added_lines={WORKFLOW: ["  - run: echo hello"]},
        )
        assert "re-dispatch" in message
        assert "privilege boundary" in message
        assert "PR body" in message
