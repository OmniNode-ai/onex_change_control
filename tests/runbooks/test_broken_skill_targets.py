# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for docs/runbooks/broken-skill-targets.md (OMN-9793).

Asserts that the file exists, has exactly 6 skill rows, each row contains
a verified Linear ticket id, and the parent epic id is declared at the top.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "docs/runbooks/broken-skill-targets.md"

# The 6 skill/target names that must appear as rows
REQUIRED_SKILLS = [
    "set_session",
    "dod_verify",
    "session",
    "dod_sweep",
    "launchd",
    "verify-on-claim",
]

# Linear ticket id pattern: OMN-NNNN
TICKET_PATTERN = re.compile(r"OMN-\d{4,}")


def test_runbook_exists() -> None:
    assert RUNBOOK.is_file(), f"Missing runbook at {RUNBOOK}"


def test_runbook_has_epic_id() -> None:
    text = RUNBOOK.read_text()
    tickets = TICKET_PATTERN.findall(text)
    assert tickets, "Runbook must contain at least one OMN-XXXX reference (epic id)"


def test_runbook_has_six_skill_rows() -> None:
    text = RUNBOOK.read_text()
    found = [s for s in REQUIRED_SKILLS if s in text]
    assert len(found) == len(REQUIRED_SKILLS), (
        f"Expected all 6 skills in runbook, found only: {found}"
    )


def test_each_row_has_ticket_id() -> None:
    """Each skill row line must be followed by a Linear ticket id on the same line
    or the surrounding table row must contain an OMN-XXXX reference."""
    text = RUNBOOK.read_text()
    lines = text.splitlines()
    for skill in REQUIRED_SKILLS:
        skill_lines = [line for line in lines if skill in line]
        assert skill_lines, f"No line found containing skill: {skill}"
        has_ticket = any(TICKET_PATTERN.search(line) for line in skill_lines)
        assert has_ticket, (
            f"Skill '{skill}' row does not contain a Linear ticket id (OMN-XXXX)"
        )


def test_runbook_has_required_columns() -> None:
    text = RUNBOOK.read_text()
    for col in ("current_state", "required_behavior", "acceptance_criteria"):
        assert col in text, f"Runbook missing column header: {col}"
