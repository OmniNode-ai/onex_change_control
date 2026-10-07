# SPDX-License-Identifier: MIT
"""Tests for canary tier assignments schema and content validation."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from onex_change_control.canary.schema import ModelCanaryTierAssignments

REPO_ROOT = Path(__file__).resolve().parent.parent
TIER_ASSIGNMENTS_PATH = REPO_ROOT / ".canary" / "tier-assignments.yaml"

EXPECTED_REPOS = {
    "omnibase_compat",
    "omniweb",
    "omniclaude",
    "omnibase_spi",
    "onex_change_control",
    "omnimarket",
    "omnibase_core",
    "omnibase_infra",
    "omniintelligence",
    "omnimemory",
    "omnidash",
    "omninode_infra",
    "omnigemini",
}


@pytest.fixture
def tier_data() -> dict[str, Any]:
    assert TIER_ASSIGNMENTS_PATH.exists(), f"Missing {TIER_ASSIGNMENTS_PATH}"
    raw: dict[str, Any] = yaml.safe_load(TIER_ASSIGNMENTS_PATH.read_text())
    return raw


class TestTierAssignmentsSchema:
    def test_file_exists(self) -> None:
        assert TIER_ASSIGNMENTS_PATH.exists()

    def test_valid_yaml(self, tier_data: dict[str, Any]) -> None:
        assert isinstance(tier_data, dict)

    def test_pydantic_validates(self, tier_data: dict[str, Any]) -> None:
        obj = ModelCanaryTierAssignments(**tier_data)
        assert obj.version == "1.0"

    def test_all_repos_assigned(self, tier_data: dict[str, Any]) -> None:
        obj = ModelCanaryTierAssignments(**tier_data)
        assigned = set()
        for tier in obj.tiers:
            assigned.update(tier.repos)
        assert assigned == EXPECTED_REPOS, (
            f"Missing: {EXPECTED_REPOS - assigned}, Extra: {assigned - EXPECTED_REPOS}"
        )

    def test_no_duplicate_repos(self, tier_data: dict[str, Any]) -> None:
        obj = ModelCanaryTierAssignments(**tier_data)
        all_repos: list[str] = []
        for tier in obj.tiers:
            all_repos.extend(tier.repos)
        assert len(all_repos) == len(set(all_repos)), (
            f"Duplicate repos: {[r for r in all_repos if all_repos.count(r) > 1]}"
        )

    def test_tier_order(self, tier_data: dict[str, Any]) -> None:
        obj = ModelCanaryTierAssignments(**tier_data)
        names = [t.name for t in obj.tiers]
        assert names == ["canary", "early_adopter", "ga"]

    def test_tier1_canary_repos(self, tier_data: dict[str, Any]) -> None:
        obj = ModelCanaryTierAssignments(**tier_data)
        canary = next(t for t in obj.tiers if t.name == "canary")
        assert set(canary.repos) == {"omnibase_compat", "omniweb"}

    def test_tier2_early_adopter_repos(self, tier_data: dict[str, Any]) -> None:
        obj = ModelCanaryTierAssignments(**tier_data)
        early = next(t for t in obj.tiers if t.name == "early_adopter")
        assert set(early.repos) == {
            "omniclaude",
            "omnibase_spi",
            "onex_change_control",
            "omnimarket",
        }

    def test_tier3_ga_repos(self, tier_data: dict[str, Any]) -> None:
        obj = ModelCanaryTierAssignments(**tier_data)
        ga = next(t for t in obj.tiers if t.name == "ga")
        assert set(ga.repos) == {
            "omnibase_core",
            "omnibase_infra",
            "omniintelligence",
            "omnimemory",
            "omnidash",
            "omninode_infra",
            "omnigemini",
        }

    def test_rejects_invalid_tier_name(self) -> None:
        with pytest.raises(ValidationError):
            ModelCanaryTierAssignments(
                version="1.0",
                tiers=[
                    {
                        "name": "invalid_tier",
                        "repos": ["omnibase_compat"],
                        "description": "bad",
                    }
                ],
            )

    def test_rejects_empty_repos(self) -> None:
        with pytest.raises(ValidationError):
            ModelCanaryTierAssignments(
                version="1.0",
                tiers=[{"name": "canary", "repos": [], "description": "empty"}],
            )


class TestScoreCanaryTiersScript:
    SCRIPT_PATH = REPO_ROOT / "scripts" / "score-canary-tiers.sh"

    def test_script_exists(self) -> None:
        assert self.SCRIPT_PATH.exists()
        assert self.SCRIPT_PATH.stat().st_mode & 0o111, "Script must be executable"

    def test_dry_run_exits_zero(self) -> None:
        result = subprocess.run(
            [str(self.SCRIPT_PATH), "--dry-run"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert result.returncode == 0, (
            f"Script failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_dry_run_lists_all_tiers(self) -> None:
        result = subprocess.run(
            [str(self.SCRIPT_PATH), "--dry-run"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert "canary" in result.stdout.lower()
        assert "early_adopter" in result.stdout.lower()
        assert "ga" in result.stdout.lower()

    def test_dry_run_lists_repos(self) -> None:
        result = subprocess.run(
            [str(self.SCRIPT_PATH), "--dry-run"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert "omnibase_compat" in result.stdout
        assert "omnibase_core" in result.stdout
