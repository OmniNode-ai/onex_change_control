# SPDX-License-Identifier: MIT
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPES = REPO_ROOT / "docs/runbooks/verify-recipes.md"

REQUIRED_CLAIMS = [
    "PR merged",
    "auto-merge enabled",
    "deployed",
    "test passing",
    "runner pool",
    "service healthy",
]

REQUIRED_PROBE_COMMANDS = [
    "gh pr view",
    "gh pr checks",
    "docker ps",
    "ssh jonah@192.168.86.201",
    "psql",
    "curl",
]


def test_recipes_exists() -> None:
    assert RECIPES.is_file()


def test_recipes_cover_required_claim_types() -> None:
    text = RECIPES.read_text()
    for claim in REQUIRED_CLAIMS:
        assert claim in text, f"Missing claim recipe: {claim}"


def test_recipes_use_concrete_probes() -> None:
    text = RECIPES.read_text()
    for cmd in REQUIRED_PROBE_COMMANDS:
        assert cmd in text, f"Missing probe command: {cmd}"
