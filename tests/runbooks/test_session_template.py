# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the session runbook template (OMN-9781).

Asserts that docs/runbooks/session-template.md exists, contains all required
H2 headings, and declares the per-step executor table columns.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "docs/runbooks/session-template.md"

REQUIRED_HEADINGS = [
    "## Session metadata",
    "## Headline tickets",
    "## DoD evidence cache",
    "## Per-step executor table",
    "## Tick log",
    "## Session-end checklist",
]


def test_template_exists() -> None:
    assert TEMPLATE.is_file(), f"Missing template at {TEMPLATE}"


def test_template_has_required_headings() -> None:
    text = TEMPLATE.read_text()
    for h in REQUIRED_HEADINGS:
        assert h in text, f"Template missing heading: {h}"


def test_template_has_executor_columns() -> None:
    text = TEMPLATE.read_text()
    # Per-step table must declare current_executor, target_executor, tracking_ticket
    for col in ("current_executor", "target_executor", "tracking_ticket"):
        assert col in text, f"Template missing column: {col}"
