# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for OCC preflight gate workflows (OMN-10485).

Covers:
- call-occ-preflight.yml caller workflow in onex_change_control
- auto-merge.yml OCC eligibility gate (query + defer/block logic)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

PREFLIGHT_CALLER = Path(".github/workflows/call-occ-preflight.yml")
AUTO_MERGE = Path(".github/workflows/auto-merge.yml")


def _load_workflow(path: Path) -> dict[str, Any]:
    loaded = cast("dict[Any, Any]", yaml.safe_load(path.read_text(encoding="utf-8")))
    if "on" not in loaded and True in loaded:
        loaded["on"] = loaded[True]
    return cast("dict[str, Any]", loaded)


# ---------------------------------------------------------------------------
# call-occ-preflight.yml caller tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_occ_preflight_caller_workflow_is_present() -> None:
    assert PREFLIGHT_CALLER.is_file(), f"Expected {PREFLIGHT_CALLER} to exist"


@pytest.mark.unit
def test_occ_preflight_caller_triggers_on_prs_and_merge_group() -> None:
    workflow = _load_workflow(PREFLIGHT_CALLER)
    triggers = workflow["on"]
    assert "pull_request" in triggers, "caller must trigger on pull_request"
    assert "merge_group" in triggers, "caller must trigger on merge_group"


@pytest.mark.unit
def test_occ_preflight_caller_targets_main_branch() -> None:
    workflow = _load_workflow(PREFLIGHT_CALLER)
    triggers = workflow["on"]
    assert triggers["pull_request"]["branches"] == ["main"]


@pytest.mark.unit
def test_occ_preflight_caller_uses_core_reusable_workflow() -> None:
    workflow = _load_workflow(PREFLIGHT_CALLER)
    job_keys = list(workflow["jobs"].keys())
    assert len(job_keys) >= 1
    job = workflow["jobs"][job_keys[0]]
    uses = job.get("uses", "")
    assert uses.startswith(
        "OmniNode-ai/omnibase_core/.github/workflows/occ-preflight.yml"
    ), f"caller must delegate to omnibase_core occ-preflight.yml, got: {uses}"


@pytest.mark.unit
def test_occ_preflight_caller_passes_contracts_and_receipts_dirs() -> None:
    workflow = _load_workflow(PREFLIGHT_CALLER)
    job = workflow["jobs"][next(iter(workflow["jobs"].keys()))]
    with_block = job.get("with", {})
    assert with_block.get("contracts-dir") == "contracts"
    assert with_block.get("receipts-dir") == "drift/dod_receipts"


# ---------------------------------------------------------------------------
# auto-merge.yml OCC gate tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_auto_merge_workflow_is_present() -> None:
    assert AUTO_MERGE.is_file(), f"Expected {AUTO_MERGE} to exist"


@pytest.mark.unit
def test_auto_merge_has_occ_gate_step() -> None:
    """auto-merge.yml must contain an OCC eligibility gate step before arming."""
    content = AUTO_MERGE.read_text(encoding="utf-8")
    assert "occ" in content.lower(), (
        "auto-merge.yml must reference OCC eligibility gate"
    )
    assert "eligibility" in content.lower(), (
        "auto-merge.yml must check eligibility before arming"
    )


@pytest.mark.unit
def test_auto_merge_occ_step_precedes_enable_step() -> None:
    """OCC gate step must appear before the Enable auto-merge YAML step."""
    content = AUTO_MERGE.read_text(encoding="utf-8")
    # Search for YAML step names (indented `- name:` lines) rather than prose mentions
    occ_pos = content.find("Check OCC eligibility")
    enable_pos = content.find("- name: Enable auto-merge")
    assert occ_pos != -1, "auto-merge.yml must have 'Check OCC eligibility' step"
    assert enable_pos != -1, "auto-merge.yml must have '- name: Enable auto-merge' step"
    assert occ_pos < enable_pos, (
        "OCC eligibility check must precede the Enable auto-merge step"
    )


@pytest.mark.unit
def test_auto_merge_defer_on_pending() -> None:
    """auto-merge.yml must contain defer logic for pending OCC preflight."""
    content = AUTO_MERGE.read_text(encoding="utf-8")
    assert "defer" in content.lower(), (
        "auto-merge.yml must implement defer logic when OCC preflight is pending"
    )


@pytest.mark.unit
def test_auto_merge_blocks_on_occ_failure() -> None:
    """auto-merge.yml must exit 1 (not silently skip) when OCC preflight fails."""
    content = AUTO_MERGE.read_text(encoding="utf-8")
    assert (
        "OCC PREFLIGHT FAILED" in content or "occ preflight failed" in content.lower()
    ), "auto-merge.yml must emit a clear error message when OCC preflight fails"


@pytest.mark.unit
def test_auto_merge_requires_checks_read_permission() -> None:
    """auto-merge.yml must declare checks: read to query GitHub Checks API."""
    workflow = _load_workflow(AUTO_MERGE)
    permissions = workflow.get("permissions", {})
    assert permissions.get("checks") == "read", (
        "auto-merge.yml must declare 'checks: read' permission for Checks API queries"
    )


@pytest.mark.unit
def test_auto_merge_enable_step_conditional_includes_defer_guard() -> None:
    """The Enable auto-merge step condition must guard against deferred OCC gate."""
    content = AUTO_MERGE.read_text(encoding="utf-8")
    # The enable step's if: condition must reference occ_gate.outputs.defer
    assert "occ_gate" in content, (
        "Enable auto-merge step must reference occ_gate step output"
    )
    assert "defer" in content, (
        "Enable auto-merge step must check occ_gate.outputs.defer before arming"
    )
