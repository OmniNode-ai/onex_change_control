# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_human_authored_privileged_pr (OMN-18327).

The check exists because on 2026-09-13 a validator change to this repo
arrived under the shared human account, became un-approvable (every lane
commits as one person; GitHub blocks self-approval), and froze every product
PR whose OCC companion had to merge behind it. The operator's fix — "That's
why we have OCC writer bot" — is that privileged change-control PRs are
opened AS the `onexbot-occ-writer` App. This check makes a human-authored one
visible rather than silent.

Two properties get the most attention here, because both are ways this check
could look like it works while doing nothing:

  * the ~70 bot companions a day must pass on AUTHORSHIP ALONE, without the
    diff being read — a check that refused them would stop the fleet more
    thoroughly than the defect it replaces; and
  * the escape must match a WHOLE LINE. omni_home CLAUDE.md rule 15 records
    three incidents where a body-parsing gate fired on prose that merely
    MENTIONED its trigger, including one where documentation asserting a
    token was absent supplied it. A sentence arguing the escape is not
    present must not satisfy it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from onex_change_control.scripts.check_human_authored_privileged_pr import (
    PRIVILEGED_PATH_PREFIXES,
    escape_ticket,
    evaluate,
    is_bot_author,
    main,
    privileged_paths,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

#: Built from parts for the same reason the module builds it from parts: a
#: file that spells its own trigger literal is the rule-15 defect.
ESCAPE_LINE = "# " + "human-author" + "-ok: OMN-18327"


class TestIsBotAuthorOMN18327:
    @pytest.mark.parametrize("value", ["Bot", "bot", "BOT", "  Bot  "])
    def test_bot_variants_are_recognised(self, value: str) -> None:
        """Case and whitespace must not turn a companion into a refusal."""
        assert is_bot_author(value) is True

    @pytest.mark.parametrize("value", ["User", "user", "", "Organization"])
    def test_non_bot_values_are_not_bots(self, value: str) -> None:
        assert is_bot_author(value) is False


class TestPrivilegedPathsOMN18327:
    def test_each_declared_prefix_is_matched(self) -> None:
        paths = [f"{prefix}thing.py" for prefix in PRIVILEGED_PATH_PREFIXES]
        assert privileged_paths(paths) == paths

    def test_evidence_and_contract_paths_are_not_privileged(self) -> None:
        """The ordinary hand-authored OCC change must stay unaffected."""
        paths = [
            "contracts/OMN-18327.yaml",
            "drift/dod_receipts/OMN-18327/dod-1/command.yaml",
            "grants/prod_promotion_grants.yaml",
            "docs/whatever.md",
            "allowlists/skip_token_approvals.yaml",
        ]
        assert privileged_paths(paths) == []

    def test_a_path_merely_containing_a_prefix_is_not_matched(self) -> None:
        """Prefix match, anchored at the start — not a substring search."""
        assert privileged_paths(["docs/src/notes.md", "vendor/.github/x.yml"]) == []


class TestEscapeTicketOMN18327:
    def test_whole_line_escape_yields_its_ticket(self) -> None:
        body = f"Some preamble.\n{ESCAPE_LINE}\nMore text."
        assert escape_ticket(body) == "OMN-18327"

    def test_leading_whitespace_is_tolerated(self) -> None:
        body = f"intro\n   {ESCAPE_LINE}\n"
        assert escape_ticket(body) == "OMN-18327"

    def test_prose_mentioning_the_annotation_does_not_satisfy_it(self) -> None:
        """The rule-15 property. Documentation about the gate is not a waiver.

        This is the exact shape that failed the Receipt Gate on OCC#7213: a
        sentence asserting a token was absent contained the token, and a
        substring matcher read it as present.
        """
        body = (
            "This PR carries no waiver: there is no "
            f"{ESCAPE_LINE} line anywhere in it, and none is needed."
        )
        assert escape_ticket(body) is None

    def test_escape_without_a_ticket_id_does_not_count(self) -> None:
        body = "# " + "human-author" + "-ok:\n"
        assert escape_ticket(body) is None

    def test_absent_escape_returns_none(self) -> None:
        assert escape_ticket("an ordinary PR body") is None


class TestEvaluateDecisionLogicOMN18327:
    """Pure logic — no subprocess, no network."""

    def test_bot_author_passes_without_the_diff_being_consulted(self) -> None:
        """The ~70 companions a day are settled by authorship alone."""
        allowed, message = evaluate(
            author_type="Bot",
            changed_files=["src/x.py", ".github/workflows/ci.yml"],
            body="",
        )
        assert allowed is True
        assert "authorship alone" in message

    def test_human_author_touching_no_privileged_path_passes(self) -> None:
        allowed, message = evaluate(
            author_type="User",
            changed_files=["contracts/OMN-1.yaml", "drift/x.yaml"],
            body="",
        )
        assert allowed is True
        assert "no privileged path" in message

    @pytest.mark.parametrize("prefix", PRIVILEGED_PATH_PREFIXES)
    def test_human_author_touching_any_privileged_prefix_is_refused(
        self, prefix: str
    ) -> None:
        allowed, message = evaluate(
            author_type="User", changed_files=[f"{prefix}thing"], body=""
        )
        assert allowed is False
        assert "REFUSED" in message

    def test_refusal_names_the_sanctioned_path_not_just_the_problem(self) -> None:
        """A gate that refuses without naming the remedy gets routed around."""
        _allowed, message = evaluate(
            author_type="User", changed_files=["src/x.py"], body=""
        )
        assert "open-pr-as-writer-app.yml" in message
        assert "onexbot-occ-writer" in message

    def test_escape_line_waives_the_refusal(self) -> None:
        allowed, message = evaluate(
            author_type="User",
            changed_files=["src/x.py"],
            body=f"hotfix while CI is broken\n{ESCAPE_LINE}\n",
        )
        assert allowed is True
        assert "escape" in message.lower()
        assert "OMN-18327" in message

    def test_prose_about_the_escape_does_not_waive_the_refusal(self) -> None:
        """The false-green this check must not have."""
        allowed, _message = evaluate(
            author_type="User",
            changed_files=["src/x.py"],
            body=f"I deliberately did not add a {ESCAPE_LINE} line here.",
        )
        assert allowed is False


class TestCliMainOMN18327:
    def test_exit_0_for_a_bot_authored_privileged_pr(self, tmp_path: Path) -> None:
        files = tmp_path / "files.txt"
        files.write_text("src/x.py\n", encoding="utf-8")
        assert (
            main(
                [
                    "--pr-author-type",
                    "Bot",
                    "--changed-files-from",
                    str(files),
                ]
            )
            == 0
        )

    def test_exit_1_for_a_human_authored_privileged_pr(self, tmp_path: Path) -> None:
        files = tmp_path / "files.txt"
        files.write_text("scripts/x.sh\n", encoding="utf-8")
        assert (
            main(["--pr-author-type", "User", "--changed-files-from", str(files)]) == 1
        )

    def test_exit_0_when_the_escape_line_is_present(self, tmp_path: Path) -> None:
        files = tmp_path / "files.txt"
        files.write_text(".github/workflows/x.yml\n", encoding="utf-8")
        body = tmp_path / "body.md"
        body.write_text(f"emergency\n{ESCAPE_LINE}\n", encoding="utf-8")
        assert (
            main(
                [
                    "--pr-author-type",
                    "User",
                    "--changed-files-from",
                    str(files),
                    "--body-from",
                    str(body),
                ]
            )
            == 0
        )

    def test_unreadable_changed_files_is_inconclusive_not_a_silent_pass(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "absent.txt"
        assert (
            main(["--pr-author-type", "User", "--changed-files-from", str(missing)])
            == 2
        )
