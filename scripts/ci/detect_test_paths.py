# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Change-aware test path resolution for onex_change_control CI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.ci.test_selection_loader import (
    ModelAdjacencyMap,
    load_adjacency_map,
)
from scripts.ci.test_selection_models import (
    EnumFullSuiteReason,
    ModelTestSelection,
)

SRC_PREFIX = "src/onex_change_control/"
TEST_UNIT_PREFIX = "tests/"

FULL_SUITE_BRANCHES = {"main"}

_MIN_PARTS_FOR_TEST_DIR = 2
_SPLIT_THRESHOLD_SINGLE = 2
_SPLIT_THRESHOLD_TWO = 5
_SPLIT_THRESHOLD_THREE = 8
_FULL_SUITE_SPLIT = 10


def resolve_test_paths(
    changed_files: list[str],
    adjacency_path: Path,
) -> list[str]:
    """Map changed file paths to deterministic test directories.

    Behavior:
      - Source changes under src/onex_change_control/<module>: include
        tests/<module>/ where a matching test dir exists in the map.
      - Test-only changes under tests/: include the changed test directory.
      - Files outside src/ and tests/: no contribution.
    """
    config = load_adjacency_map(adjacency_path)
    return _resolve(changed_files, config)


def _resolve(changed_files: list[str], config: ModelAdjacencyMap) -> list[str]:
    direct_modules: set[str] = set()
    selected: set[str] = set()

    for path in changed_files:
        if path.startswith(SRC_PREFIX):
            module = path[len(SRC_PREFIX) :].split("/", 1)[0]
            if module in config.adjacency:
                direct_modules.add(module)
        elif path.startswith(TEST_UNIT_PREFIX):
            parts = path.split("/")
            if len(parts) >= _MIN_PARTS_FOR_TEST_DIR:
                selected.add(f"{TEST_UNIT_PREFIX}{parts[1]}/")

    expanded: set[str] = set(direct_modules)
    for module in direct_modules:
        expanded.update(config.adjacency[module].reverse_deps)

    for module in expanded:
        selected.add(f"{TEST_UNIT_PREFIX}{module}/")

    return sorted(selected)


def compute_selection(
    changed_files: list[str],
    adjacency_path: Path,
    ref_name: str,
    event_name: str = "pull_request",
    *,
    feature_flag_enabled: bool = True,
) -> ModelTestSelection:
    config = load_adjacency_map(adjacency_path)
    escalation = _escalation_reason(
        changed_files=changed_files,
        config=config,
        ref_name=ref_name,
        event_name=event_name,
        feature_flag_enabled=feature_flag_enabled,
    )
    if escalation is not None:
        return _full_suite(escalation)

    selected = _resolve(changed_files, config)
    if not selected:
        # Conservative fallback over the full tests/ tree. Fires for changes
        # that have no unit-test mapping (doc-only, workflow-only, etc.).
        selected = ["tests/"]
    split_count = _split_count_for(selected)
    return ModelTestSelection(
        selected_paths=selected,
        split_count=split_count,
        is_full_suite=False,
        full_suite_reason=None,
        matrix=list(range(1, split_count + 1)),
    )


def _escalation_reason(
    changed_files: list[str],
    config: ModelAdjacencyMap,
    ref_name: str,
    event_name: str,
    *,
    feature_flag_enabled: bool,
) -> EnumFullSuiteReason | None:
    if not feature_flag_enabled:
        return EnumFullSuiteReason.FEATURE_FLAG_OFF
    if ref_name in FULL_SUITE_BRANCHES:
        return EnumFullSuiteReason.MAIN_BRANCH
    if event_name == "merge_group":
        return EnumFullSuiteReason.MERGE_GROUP
    if event_name == "schedule":
        return EnumFullSuiteReason.SCHEDULED
    for changed in changed_files:
        if any(
            changed == infra or changed.startswith(infra.rstrip("/") + "/")
            for infra in config.test_infrastructure_paths
        ):
            return EnumFullSuiteReason.TEST_INFRASTRUCTURE
    changed_modules = {
        path[len(SRC_PREFIX) :].split("/", 1)[0]
        for path in changed_files
        if path.startswith(SRC_PREFIX)
    } & set(config.adjacency.keys())
    if changed_modules & set(config.shared_modules):
        return EnumFullSuiteReason.SHARED_MODULE
    if len(changed_modules) >= config.thresholds.modules_changed_for_full_suite:
        return EnumFullSuiteReason.THRESHOLD_MODULES
    return None


def _full_suite(reason: EnumFullSuiteReason) -> ModelTestSelection:
    return ModelTestSelection(
        selected_paths=["tests/"],
        split_count=_FULL_SUITE_SPLIT,
        is_full_suite=True,
        full_suite_reason=reason,
        matrix=list(range(1, _FULL_SUITE_SPLIT + 1)),
    )


def _split_count_for(selected_paths: list[str]) -> int:
    n = len(selected_paths)
    if n <= _SPLIT_THRESHOLD_SINGLE:
        return 1
    if n <= _SPLIT_THRESHOLD_TWO:
        return 2
    if n <= _SPLIT_THRESHOLD_THREE:
        return 3
    return 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve change-aware test paths")
    parser.add_argument(
        "--changed-files-from",
        type=Path,
        required=True,
        help="Path to a file with one changed-file path per line.",
    )
    parser.add_argument("--ref-name", required=True)
    parser.add_argument("--event-name", default="pull_request")
    parser.add_argument(
        "--adjacency",
        type=Path,
        default=Path(__file__).parent / "test_selection_adjacency.yaml",
    )
    parser.add_argument(
        "--feature-flag",
        choices=("on", "off"),
        default="on",
        help="'off' forces a FEATURE_FLAG_OFF full-suite selection.",
    )
    args = parser.parse_args(argv)

    changed = [
        line.strip()
        for line in args.changed_files_from.read_text().splitlines()
        if line.strip()
    ]
    selection = compute_selection(
        changed_files=changed,
        adjacency_path=args.adjacency,
        ref_name=args.ref_name,
        event_name=args.event_name,
        feature_flag_enabled=(args.feature_flag == "on"),
    )
    sys.stdout.write(selection.model_dump_json())
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
