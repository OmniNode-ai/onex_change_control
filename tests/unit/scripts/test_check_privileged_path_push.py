# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_privileged_path_push (OMN-18804).

WHY THIS EXISTS. `check-human-authored-privileged-pr` (OMN-18327) refuses a
human-authored PR whose diff touches `src/`, `scripts/` or `.github/`. The
rule is right; nothing says so until the PR is already open. Three lanes in
one window each lost 10-15 minutes learning it from the refusal text, then
closing the PR and dispatching `open-pr-as-writer-app.yml` — OCC#10338 to
OCC#10341, OCC#10339 to OCC#10342, and the CI Summary lane.

WHAT THE SHAPE OF THIS CHECK IS, AND WHY IT IS NOT A BLANKET REFUSAL.
`open-pr-as-writer-app.yml` creates no branch and pushes nothing: it takes a
branch ALREADY PUSHED and opens a PR for it as the App ("branch does not
exist on origin - push it first", in the workflow's own words). A pre-push
hook that refused every privileged push would therefore refuse the first
half of the sanctioned path itself, and the lane would have no way to comply
with the message it was just shown. So:

  * no PR yet — the push IS the sanctioned path's first step. Advise, exit 0.
  * the open PR is App-authored — already on the sanctioned path. Silent.
  * the open PR is human-authored — this is the state the gate refuses and
    it cannot be repaired in place, because the App workflow EDITS an
    existing PR rather than re-authoring it. Refuse, exit 1, and name both
    the close and the dispatch.

An unreadable PR state advises rather than refuses. This is a hint, not a
gate: the mechanical gate is `check-human-authored-privileged-pr` in CI, and
a hook that blocked pushes whenever `gh` was unreachable would be routed
around within a day.
"""

from __future__ import annotations

import pytest

from onex_change_control.scripts.check_privileged_path_push import (
    PRIVILEGED_PATH_PREFIXES,
    RECOVERY_COMMAND_TEMPLATE,
    ModelOpenPrFacts,
    evaluate,
    privileged_paths,
    ticket_from_branch,
)

pytestmark = pytest.mark.unit

BRANCH = "jonah/omn-18804-privileged-path-prepush-hint"

#: The literal a lane greps for. Asserted as a prefix so a branch or ticket
#: substituted into the rest of the line cannot make the assertion vacuous.
RECOVERY_PREFIX = "gh workflow run open-pr-as-writer-app.yml -f branch="

UNPRIVILEGED_FILES = [
    "contracts/OMN-18804.yaml",
    "docs/tracking/notes.md",
    "grants/prod_promotion_grants.yaml",
    "evidence/receipt.json",
]


class TestPrivilegedPathsOMN18804:
    @pytest.mark.parametrize("prefix", PRIVILEGED_PATH_PREFIXES)
    def test_each_declared_prefix_is_detected(self, prefix: str) -> None:
        """Every prefix the gate refuses on must also be one this hint fires on.

        Parametrised over the module's own tuple rather than a hand-copied
        list, so a prefix added on one side cannot silently go unhinted.
        """
        assert privileged_paths([f"{prefix}some_file.py"]) == [f"{prefix}some_file.py"]

    def test_unprivileged_paths_are_not_detected(self) -> None:
        assert privileged_paths(UNPRIVILEGED_FILES) == []

    def test_the_prefix_set_matches_the_gate_it_hints_about(self) -> None:
        """A hint about a different path set than the gate refuses is a lie.

        Imported here rather than restated, so the two cannot drift.
        """
        from onex_change_control.scripts.check_human_authored_privileged_pr import (
            PRIVILEGED_PATH_PREFIXES as GATE_PREFIXES,
        )

        assert PRIVILEGED_PATH_PREFIXES == GATE_PREFIXES


class TestTicketFromBranchOMN18804:
    @pytest.mark.parametrize(
        ("branch", "expected"),
        [
            ("jonah/omn-18804-thing", "OMN-18804"),
            ("jonah/OMN-123-thing", "OMN-123"),
            ("omn-18804", "OMN-18804"),
            ("jonah/no-ticket-here", "<OMN-...>"),
            ("", "<OMN-...>"),
        ],
    )
    def test_ticket_is_read_from_the_branch_or_left_as_a_placeholder(
        self, branch: str, expected: str
    ) -> None:
        """A wrong ticket id in a copy-pasteable command is worse than a blank.

        When the branch does not name one, the command carries the visible
        placeholder rather than a guess.
        """
        assert ticket_from_branch(branch) == expected


class TestEvaluateOMN18804:
    def test_no_privileged_path_passes_silently(self) -> None:
        """AC3: an ordinary evidence or contract push says nothing at all."""
        allowed, message = evaluate(
            branch=BRANCH, changed_files=UNPRIVILEGED_FILES, open_pr=None
        )
        assert allowed is True
        assert message == ""

    @pytest.mark.parametrize("prefix", PRIVILEGED_PATH_PREFIXES)
    def test_each_privileged_prefix_prints_the_recovery_command(
        self, prefix: str
    ) -> None:
        """AC1: the command is printed for a diff under each privileged prefix."""
        allowed, message = evaluate(
            branch=BRANCH, changed_files=[f"{prefix}thing.py"], open_pr=None
        )
        assert allowed is True, "no PR yet — this push is the sanctioned first step"
        assert RECOVERY_PREFIX in message
        assert BRANCH in message
        assert "OMN-18804" in message

    @pytest.mark.parametrize("prefix", PRIVILEGED_PATH_PREFIXES)
    def test_each_privileged_prefix_refuses_under_a_human_authored_pr(
        self, prefix: str
    ) -> None:
        """AC2 + AC1: the refused state is refused, naming both commands."""
        allowed, message = evaluate(
            branch=BRANCH,
            changed_files=[f"{prefix}thing.py"],
            open_pr=ModelOpenPrFacts(number=10338, author_type="User"),
        )
        assert allowed is False
        assert "REFUSED" in message
        assert RECOVERY_PREFIX in message
        assert "gh pr close 10338" in message

    def test_an_app_authored_pr_passes_silently(self) -> None:
        """The ~70 companions a day, and every PR already on the App path."""
        allowed, message = evaluate(
            branch=BRANCH,
            changed_files=["src/onex_change_control/scripts/thing.py"],
            open_pr=ModelOpenPrFacts(number=10341, author_type="Bot"),
        )
        assert allowed is True
        assert message == ""

    @pytest.mark.parametrize("author_type", ["bot", "BOT", "  Bot  "])
    def test_bot_detection_is_case_and_whitespace_tolerant(
        self, author_type: str
    ) -> None:
        """Refusing a companion on a casing difference would stop the fleet."""
        allowed, _ = evaluate(
            branch=BRANCH,
            changed_files=["src/x.py"],
            open_pr=ModelOpenPrFacts(number=1, author_type=author_type),
        )
        assert allowed is True

    def test_an_unreadable_pr_state_advises_and_never_blocks(self) -> None:
        """A hint that blocks pushes when `gh` is unreachable gets routed around."""
        allowed, message = evaluate(
            branch=BRANCH,
            changed_files=["src/x.py"],
            open_pr=None,
            pr_state_readable=False,
        )
        assert allowed is True
        assert RECOVERY_PREFIX in message
        assert "could not be read" in message

    def test_the_template_is_the_single_source_of_the_command_text(self) -> None:
        """AC1/AC5 assert one literal; it must have exactly one definition."""
        rendered = RECOVERY_COMMAND_TEMPLATE.format(branch=BRANCH, ticket="OMN-18804")
        assert rendered.startswith(RECOVERY_PREFIX)
        _, message = evaluate(branch=BRANCH, changed_files=["src/x.py"], open_pr=None)
        assert rendered in message


class TestGateRefusalNamesTheCommandOMN18804:
    def test_the_gate_refusal_names_the_recovery_command_verbatim(self) -> None:
        """AC5: the refusal a lane actually reads carries the command itself.

        Until this ticket the gate named the workflow FILE and left the lane
        to work out the dispatch. Three lanes did, one at a time.
        """
        from onex_change_control.scripts.check_human_authored_privileged_pr import (
            evaluate as gate_evaluate,
        )

        allowed, message = gate_evaluate(
            author_type="User",
            changed_files=[".github/workflows/ci.yml"],
            body="no escape here",
        )
        assert allowed is False
        assert RECOVERY_PREFIX in message
