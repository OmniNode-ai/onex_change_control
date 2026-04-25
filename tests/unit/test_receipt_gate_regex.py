# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Adversarial tests for receipt-gate regex specificity (OMN-9574 / OMN-9722).

Guards against the greedy-regex regression: bare OMN-XXXX tokens anywhere in
a PR body (epic backrefs, commit-history notes, prose mentions, code blocks)
must NOT trigger receipt checks. Only closing-keyword citations
(Closes/Fixes/Resolves/Implements OMN-XXXX) are authoritative.

These tests are the downstream-consumer view: onex_change_control depends on
omnibase_core's receipt_gate module, so regressions in omnibase_core's regex
handling would break this repo's PR merge gate.
"""

from __future__ import annotations

import re

import pytest

try:
    from omnibase_core.validation.receipt_gate import (  # type: ignore[attr-defined, unused-ignore]
        CLOSING_KEYWORD_PATTERN,
        _extract_ticket_ids,
    )

    _HAS_SPECIFICITY_FIX = True
except ImportError:
    _HAS_SPECIFICITY_FIX = False

_specificity_fix = pytest.mark.skipif(
    not _HAS_SPECIFICITY_FIX,
    reason=(
        "omnibase_core does not expose CLOSING_KEYWORD_PATTERN — "
        "upgrade to the version that includes OMN-9574 fix"
    ),
)


# ---------------------------------------------------------------------------
# CLOSING_KEYWORD_PATTERN — direct regex tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClosingKeywordPatternMatches:
    """Verify CLOSING_KEYWORD_PATTERN matches valid closing-keyword forms."""

    @_specificity_fix
    def test_closes_with_period(self) -> None:
        assert CLOSING_KEYWORD_PATTERN.findall("Closes OMN-1234.") == ["1234"]

    @_specificity_fix
    def test_fixes_with_period(self) -> None:
        assert CLOSING_KEYWORD_PATTERN.findall("Fixes OMN-9574.") == ["9574"]

    @_specificity_fix
    def test_resolves_without_period(self) -> None:
        assert CLOSING_KEYWORD_PATTERN.findall("Resolves OMN-5678") == ["5678"]

    @_specificity_fix
    def test_implements_keyword(self) -> None:
        assert CLOSING_KEYWORD_PATTERN.findall("Implements OMN-9999") == ["9999"]

    @_specificity_fix
    def test_closes_lowercase(self) -> None:
        assert CLOSING_KEYWORD_PATTERN.findall("closes OMN-1234") == ["1234"]

    @_specificity_fix
    def test_fixes_lowercase(self) -> None:
        assert CLOSING_KEYWORD_PATTERN.findall("fixes omn-1234") == ["1234"]

    @_specificity_fix
    def test_colon_separator(self) -> None:
        """Some bots emit 'Closes: OMN-XXXX' — colon separator must be accepted."""
        assert CLOSING_KEYWORD_PATTERN.findall("Closes: OMN-4321") == ["4321"]

    @_specificity_fix
    def test_trailing_whitespace_after_ticket(self) -> None:
        """Trailing whitespace or newline after ticket number is fine."""
        body = "Closes OMN-1234   \nsome other line"
        assert CLOSING_KEYWORD_PATTERN.findall(body) == ["1234"]

    @_specificity_fix
    def test_multiline_body_with_closing_keyword(self) -> None:
        body = (
            "## Summary\n"
            "This PR adds the new feature.\n"
            "\n"
            "Closes OMN-9574.\n"
            "\n"
            "## Test plan\n"
            "- [ ] run tests\n"
        )
        assert CLOSING_KEYWORD_PATTERN.findall(body) == ["9574"]

    @_specificity_fix
    def test_multi_ticket_multiple_closing_keywords(self) -> None:
        """Both tickets must be captured when each has its own closing keyword."""
        body = "Closes OMN-1234. Also closes OMN-5678."
        matches = CLOSING_KEYWORD_PATTERN.findall(body)
        assert sorted(matches) == ["1234", "5678"]


@pytest.mark.unit
class TestClosingKeywordPatternNonMatches:
    """Verify CLOSING_KEYWORD_PATTERN does NOT match bare OMN refs."""

    @_specificity_fix
    def test_bare_omn_ref_in_prose(self) -> None:
        assert (
            CLOSING_KEYWORD_PATTERN.findall(
                "OMN-1234 was a parent epic, not closed by this PR"
            )
            == []
        )

    @_specificity_fix
    def test_references_keyword_not_matched(self) -> None:
        assert (
            CLOSING_KEYWORD_PATTERN.findall("references OMN-1234 in commit history")
            == []
        )

    @_specificity_fix
    def test_see_also_not_matched(self) -> None:
        assert CLOSING_KEYWORD_PATTERN.findall("See also OMN-9574") == []

    @_specificity_fix
    def test_omn_in_code_block_not_matched(self) -> None:
        body = "## Changes\n```\n# ticket: OMN-9574\n```\n"
        assert CLOSING_KEYWORD_PATTERN.findall(body) == []

    @_specificity_fix
    def test_omn_in_inline_code_not_matched(self) -> None:
        body = "See `OMN-9574` for background"
        assert CLOSING_KEYWORD_PATTERN.findall(body) == []

    @_specificity_fix
    def test_prose_epic_ref_not_matched(self) -> None:
        """Epic backreference prose must not be extracted."""
        body = "This work is part of the OMN-9695 epic."
        assert CLOSING_KEYWORD_PATTERN.findall(body) == []

    @_specificity_fix
    def test_commit_history_note_not_matched(self) -> None:
        body = (
            "The root cause was introduced in OMN-9561; "
            "this PR does not close that ticket."
        )
        assert CLOSING_KEYWORD_PATTERN.findall(body) == []


# ---------------------------------------------------------------------------
# _extract_ticket_ids — integration-level specificity tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractTicketIdsSpecificity:
    """Adversarial tests for _extract_ticket_ids closing-keyword-only extraction.

    These tests document the OMN-9574 regression class and assert the fix holds.
    Each test name encodes the failure mode being guarded against.
    """

    @_specificity_fix
    def test_closing_keyword_body_extracts_correct_ticket(self) -> None:
        ids = _extract_ticket_ids("Closes OMN-9574.")
        assert ids == ["OMN-9574"]

    @_specificity_fix
    def test_bare_omn_ref_in_body_extracts_nothing(self) -> None:
        """Bare OMN-XXXX without closing keyword must yield empty list."""
        ids = _extract_ticket_ids("See also OMN-9574")
        assert ids == []

    @_specificity_fix
    def test_omn_in_code_block_extracts_nothing(self) -> None:
        body = "```\nOMN-9574\n```"
        ids = _extract_ticket_ids(body)
        assert ids == []

    @_specificity_fix
    def test_epic_ref_prose_plus_closing_keyword_extracts_only_closed_ticket(
        self,
    ) -> None:
        """Epic ref in prose + closing keyword → only the closed ticket is extracted."""
        body = "This PR is part of the OMN-9695 epic.\n\nCloses OMN-9574.\n"
        ids = _extract_ticket_ids(body)
        assert ids == ["OMN-9574"]
        assert "OMN-9695" not in ids

    @_specificity_fix
    def test_commit_history_ref_plus_closing_keyword_extracts_only_closed_ticket(
        self,
    ) -> None:
        body = (
            "Root cause introduced in OMN-9561; not closing that here.\n"
            "\n"
            "Closes OMN-9574.\n"
        )
        ids = _extract_ticket_ids(body)
        assert ids == ["OMN-9574"]
        assert "OMN-9561" not in ids

    @_specificity_fix
    def test_multi_closing_keywords_both_extracted(self) -> None:
        body = "Closes OMN-1234. Also closes OMN-5678."
        ids = _extract_ticket_ids(body)
        assert ids == ["OMN-1234", "OMN-5678"]

    @_specificity_fix
    def test_title_fallback_when_body_empty(self) -> None:
        """Empty body falls back to title OMN token extraction."""
        ids = _extract_ticket_ids("", pr_title="fix(OMN-9084): some fix")
        assert ids == ["OMN-9084"]

    @_specificity_fix
    def test_title_fallback_not_used_when_body_has_closing_keyword(self) -> None:
        """Closing keyword in body takes precedence — title OMN token is NOT added."""
        ids = _extract_ticket_ids(
            "Closes OMN-9574.",
            pr_title="fix(OMN-9695): something else",
        )
        assert ids == ["OMN-9574"]
        assert "OMN-9695" not in ids

    @_specificity_fix
    def test_no_match_in_body_or_title_returns_empty(self) -> None:
        ids = _extract_ticket_ids("no tickets here", pr_title="also no tickets")
        assert ids == []

    @_specificity_fix
    def test_trailing_whitespace_and_multiline_body_does_not_affect_extraction(
        self,
    ) -> None:
        body = "  \n  Closes OMN-9574.  \n  \n  Some other text.  \n"
        ids = _extract_ticket_ids(body)
        assert ids == ["OMN-9574"]


# ---------------------------------------------------------------------------
# Pattern structure guard — regression against future regex loosening
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClosingKeywordPatternStructure:
    """Guard the structural contract of CLOSING_KEYWORD_PATTERN itself."""

    @_specificity_fix
    def test_pattern_requires_keyword_prefix(self) -> None:
        """Pattern must NOT match a bare ticket number without a keyword."""
        pattern = CLOSING_KEYWORD_PATTERN
        assert not pattern.search("OMN-1234"), (
            "CLOSING_KEYWORD_PATTERN must require a closing keyword prefix. "
            "Bare OMN-1234 without Closes/Fixes/Resolves/Implements must not match."
        )

    @_specificity_fix
    def test_pattern_is_case_insensitive(self) -> None:
        pattern = CLOSING_KEYWORD_PATTERN
        assert pattern.flags & re.IGNORECASE, (
            "CLOSING_KEYWORD_PATTERN must be case-insensitive so 'closes OMN-1234' "
            "and 'CLOSES OMN-1234' both match."
        )

    @_specificity_fix
    def test_pattern_covers_all_four_keywords(self) -> None:
        for kw in ("Closes", "Fixes", "Resolves", "Implements"):
            assert CLOSING_KEYWORD_PATTERN.search(f"{kw} OMN-1234"), (
                f"CLOSING_KEYWORD_PATTERN must match keyword '{kw}'"
            )

    @_specificity_fix
    def test_pattern_does_not_match_see_also_or_references(self) -> None:
        for prefix in ("See also", "References", "Related to", "Mentioned in"):
            assert not CLOSING_KEYWORD_PATTERN.search(f"{prefix} OMN-1234"), (
                f"CLOSING_KEYWORD_PATTERN must not match prefix '{prefix}'"
            )
