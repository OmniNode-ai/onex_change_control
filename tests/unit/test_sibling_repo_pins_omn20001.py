# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Sibling-repo reads in CI config use a pinned version, not a live branch (OMN-20001).

Operator ruling 2026-10-01: each repo checks only itself, and any read of a
sibling repo uses the version this repo has pinned, so a merge in one repo
cannot turn another repo red. This file is the falsifier for the sites fixed in
OMN-20001: cross-repo `uses:` pins, the omnidash clone, and the composite
actions' own validator checkout. Each scanner has a positive control that
proves it flags the pre-fix shape.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
ACTIONS_DIR = REPO_ROOT / ".github" / "actions"

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SIBLING_USES = re.compile(
    r"^\s*uses:\s*OmniNode-ai/(?!onex_change_control/)(?P<repo>[\w.-]+)/"
    r"(?P<path>\S+?)@(?P<ref>\S+)",
    re.MULTILINE,
)


def floating_sibling_uses(text: str) -> list[str]:
    """Return `uses:` lines naming a sibling repo at anything but a 40-hex sha."""
    return [
        m.group(0).strip()
        for m in _SIBLING_USES.finditer(text)
        if not _SHA.match(m.group("ref"))
    ]


def omnidash_clones_without_ref(text: str) -> list[str]:
    """Return omnidash `git clone` lines that carry no --branch pin."""
    return [
        line.strip()
        for line in text.splitlines()
        if "clone" in line
        and "OmniNode-ai/omnidash.git" in line
        and "--branch" not in line
    ]


def test_scanner_flags_the_pre_fix_shapes() -> None:
    """Positive control: the scanners must catch what OMN-20001 removed."""
    assert floating_sibling_uses(
        "    uses: OmniNode-ai/omnibase_core/.github/workflows/zone-filter.yml@dev\n"
    )
    assert floating_sibling_uses(
        "    uses: OmniNode-ai/omniclaude/.github/workflows/x-reusable.yml@main\n"
    )
    assert not floating_sibling_uses(
        "    uses: OmniNode-ai/omniclaude/.github/workflows/x-reusable.yml@"
        + "a" * 40
        + "\n"
    )
    assert omnidash_clones_without_ref(
        "git clone --depth=1 https://github.com/OmniNode-ai/omnidash.git ../omnidash"
    )
    assert not omnidash_clones_without_ref(
        'git clone --branch "${PIN}" https://github.com/OmniNode-ai/omnidash.git x'
    )


@pytest.mark.unit
def test_no_workflow_uses_a_sibling_repo_at_a_floating_ref() -> None:
    files = sorted(WORKFLOWS_DIR.glob("*.yml"))
    assert files, "no workflows found; test is not looking at the repo"
    floating = {
        f.name: hits for f in files if (hits := floating_sibling_uses(f.read_text()))
    }
    assert not floating, (
        "sibling-repo reusable workflows must be pinned to a commit sha "
        f"(OMN-20001): {floating}"
    )


@pytest.mark.unit
def test_omnidash_clone_is_pinned() -> None:
    text = (WORKFLOWS_DIR / "ci.yml").read_text()
    assert "OmniNode-ai/omnidash.git" in text, "omnidash clone moved; update test"
    assert not omnidash_clones_without_ref(text)
    assert "OMNIDASH_PIN_REF:" in text


@pytest.mark.unit
@pytest.mark.parametrize("action", ["validate-boundaries", "validate-contract"])
def test_composite_action_validators_follow_the_consumer_pin(action: str) -> None:
    """The action checks out OCC at the ref the consumer pinned, not live main."""
    data = yaml.safe_load((ACTIONS_DIR / action / "action.yml").read_text())
    checkouts = [
        s["with"]
        for s in data["runs"]["steps"]
        if s.get("uses", "").startswith("actions/checkout@")
        and s["with"].get("repository") == "OmniNode-ai/onex_change_control"
    ]
    assert len(checkouts) == 1
    assert checkouts[0]["ref"] == "${{ github.action_ref }}"
