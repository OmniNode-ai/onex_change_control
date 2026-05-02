# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Regression coverage for the onex_change_control Receipt Gate caller."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

WORKFLOW_PATH = Path(".github/workflows/call-receipt-gate.yml")


def _load_workflow() -> dict[str, Any]:
    loaded = cast(
        "dict[Any, Any]", yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    )
    if "on" not in loaded and True in loaded:
        loaded["on"] = loaded[True]
    return cast("dict[str, Any]", loaded)


def test_receipt_gate_caller_workflow_is_present() -> None:
    assert WORKFLOW_PATH.is_file()


def test_receipt_gate_caller_triggers_on_prs_and_merge_group() -> None:
    workflow = _load_workflow()

    triggers = workflow["on"]
    assert "pull_request" in triggers
    assert "merge_group" in triggers
    assert triggers["pull_request"]["branches"] == ["main"]


def test_receipt_gate_caller_uses_core_reusable_gate() -> None:
    workflow = _load_workflow()

    verify_job = workflow["jobs"]["verify"]
    assert (
        verify_job["uses"]
        == "OmniNode-ai/omnibase_core/.github/workflows/receipt-gate.yml@main"
    )


def test_receipt_gate_caller_validates_pr_head_occ_evidence() -> None:
    workflow = _load_workflow()

    verify_job = workflow["jobs"]["verify"]
    assert verify_job["with"]["contracts-dir"] == "contracts"
    assert verify_job["with"]["receipts-dir"] == "drift/dod_receipts"
