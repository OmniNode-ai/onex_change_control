# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the session-end ritual (OMN-9785).

Asserts that docs/runbooks/session-template.md's `## Session-end checklist`
contains the manual_count / total_count formula and all required ritual items.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "docs/runbooks/session-template.md"

REQUIRED_RITUAL_ITEMS = [
    "manual_count",
    "total_count",
    "broken-skill targets",
    "handoff",
    "CronDelete",
    "verifier",
]


def test_session_end_ritual_complete() -> None:
    text = TEMPLATE.read_text()
    # Must define the manual/total ratio
    assert "manual_count / total_count" in text
    for item in REQUIRED_RITUAL_ITEMS:
        assert item in text, f"Missing ritual item: {item}"
