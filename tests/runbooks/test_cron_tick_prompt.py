# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the CronCreate tick prompt template (OMN-9782).

Asserts the runbook exists and contains the F92 active-drive language
phrases that distinguish a verifying tick from a passive monitoring tick.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT = REPO_ROOT / "docs/runbooks/cron-tick-prompt.md"

# Active-drive language (F92 lesson) that MUST appear in the prompt body.
REQUIRED_PHRASES = [
    "verify before silent",
    "terminal state",
    "MERGED",
    "ADVISORY",
    "verifier",
    "runner",
    "do not trust",
]


def test_prompt_exists() -> None:
    assert PROMPT.is_file(), f"Missing runbook: {PROMPT}"


def test_prompt_has_active_drive_language() -> None:
    text = PROMPT.read_text().lower()
    for phrase in REQUIRED_PHRASES:
        assert phrase.lower() in text, f"Missing required phrase: {phrase}"
