# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Unit tests for change-aware test path selection in onex_change_control."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.detect_test_paths import compute_selection, resolve_test_paths
from scripts.ci.test_selection_models import EnumFullSuiteReason

ADJACENCY = (
    Path(__file__).parent.parent.parent / "scripts/ci/test_selection_adjacency.yaml"
)


@pytest.fixture
def adjacency_path() -> Path:
    return ADJACENCY


class TestResolveTestPaths:
    def test_src_models_change_expands_to_dependents(
        self, adjacency_path: Path
    ) -> None:
        # models is a shared module, but _resolve is called directly — no escalation
        result = resolve_test_paths(
            ["src/onex_change_control/models/model_day_close.py"],
            adjacency_path,
        )
        # models → scripts, validation, validators, scanners, boundaries + itself
        assert "tests/models/" in result
        assert "tests/scripts/" in result
        assert "tests/validation/" in result

    def test_src_validators_change_maps_to_validators(
        self, adjacency_path: Path
    ) -> None:
        result = resolve_test_paths(
            ["src/onex_change_control/validators/some_validator.py"],
            adjacency_path,
        )
        assert "tests/validators/" in result

    def test_test_file_change_maps_to_own_dir(self, adjacency_path: Path) -> None:
        result = resolve_test_paths(
            ["tests/ci/test_validate_pr_contracts.py"],
            adjacency_path,
        )
        assert "tests/ci/" in result

    def test_unknown_module_not_included(self, adjacency_path: Path) -> None:
        result = resolve_test_paths(
            ["src/onex_change_control/nonexistent_module/foo.py"],
            adjacency_path,
        )
        # nonexistent_module not in adjacency — no paths added
        assert not any("nonexistent_module" in p for p in result)

    def test_doc_only_change_returns_empty(self, adjacency_path: Path) -> None:
        result = resolve_test_paths(
            ["docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md"],
            adjacency_path,
        )
        assert result == []


class TestComputeSelection:
    def test_main_branch_escalates(self, adjacency_path: Path) -> None:
        sel = compute_selection(
            changed_files=["src/onex_change_control/validators/foo.py"],
            adjacency_path=adjacency_path,
            ref_name="main",
            event_name="push",
        )
        assert sel.is_full_suite
        assert sel.full_suite_reason == EnumFullSuiteReason.MAIN_BRANCH

    def test_merge_group_escalates(self, adjacency_path: Path) -> None:
        sel = compute_selection(
            changed_files=["src/onex_change_control/validators/foo.py"],
            adjacency_path=adjacency_path,
            ref_name="jonah/omn-10763-something",
            event_name="merge_group",
        )
        assert sel.is_full_suite
        assert sel.full_suite_reason == EnumFullSuiteReason.MERGE_GROUP

    def test_feature_flag_off_escalates(self, adjacency_path: Path) -> None:
        sel = compute_selection(
            changed_files=["src/onex_change_control/validators/foo.py"],
            adjacency_path=adjacency_path,
            ref_name="jonah/branch",
            event_name="pull_request",
            feature_flag_enabled=False,
        )
        assert sel.is_full_suite
        assert sel.full_suite_reason == EnumFullSuiteReason.FEATURE_FLAG_OFF

    def test_shared_module_change_escalates(self, adjacency_path: Path) -> None:
        sel = compute_selection(
            changed_files=["src/onex_change_control/models/model_day_close.py"],
            adjacency_path=adjacency_path,
            ref_name="jonah/branch",
            event_name="pull_request",
        )
        assert sel.is_full_suite
        assert sel.full_suite_reason == EnumFullSuiteReason.SHARED_MODULE

    def test_test_infra_change_escalates(self, adjacency_path: Path) -> None:
        sel = compute_selection(
            changed_files=["pyproject.toml"],
            adjacency_path=adjacency_path,
            ref_name="jonah/branch",
            event_name="pull_request",
        )
        assert sel.is_full_suite
        assert sel.full_suite_reason == EnumFullSuiteReason.TEST_INFRASTRUCTURE

    def test_leaf_module_change_is_smart(self, adjacency_path: Path) -> None:
        sel = compute_selection(
            changed_files=["src/onex_change_control/validators/some_check.py"],
            adjacency_path=adjacency_path,
            ref_name="jonah/branch",
            event_name="pull_request",
        )
        assert not sel.is_full_suite
        assert sel.full_suite_reason is None
        assert "tests/validators/" in sel.selected_paths
        assert sel.split_count >= 1

    def test_doc_only_change_falls_back_to_full_tests(
        self, adjacency_path: Path
    ) -> None:
        sel = compute_selection(
            changed_files=["docs/design/DESIGN_DRIFT_CONTROL_SYSTEM.md"],
            adjacency_path=adjacency_path,
            ref_name="jonah/branch",
            event_name="pull_request",
        )
        assert not sel.is_full_suite
        assert sel.selected_paths == ["tests/"]

    def test_split_count_is_1_for_single_path(self, adjacency_path: Path) -> None:
        sel = compute_selection(
            changed_files=["src/onex_change_control/canary/canary_tier.py"],
            adjacency_path=adjacency_path,
            ref_name="jonah/branch",
            event_name="pull_request",
        )
        assert not sel.is_full_suite
        assert sel.split_count == 1
        assert sel.matrix == [1]
