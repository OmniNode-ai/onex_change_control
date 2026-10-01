# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Regression coverage for the onex_change_control Receipt Gate caller."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
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
    assert triggers["pull_request"]["branches"] == ["main", "dev", "hotfix/**"]


def test_receipt_gate_caller_preserves_required_check_name() -> None:
    workflow = _load_workflow()

    verify_job = workflow["jobs"]["verify"]
    assert "uses" not in verify_job
    assert verify_job["name"] == "verify / verify"


def test_receipt_gate_caller_resolves_validator_from_pr_base_ref() -> None:
    workflow = _load_workflow()

    verify_job = workflow["jobs"]["verify"]
    step_by_id = {step["id"]: step for step in verify_job["steps"] if "id" in step}
    validator_ref = step_by_id["validator_ref"]
    assert "PR_BASE_REF" in validator_ref["env"]
    assert "MERGE_GROUP_BASE_REF" in validator_ref["env"]
    assert "ref=${resolved_ref}" in validator_ref["run"]


def test_receipt_gate_caller_validates_pr_head_occ_evidence() -> None:
    workflow = _load_workflow()

    verify_job = workflow["jobs"]["verify"]
    step_by_id = {step["id"]: step for step in verify_job["steps"] if "id" in step}
    evidence_step = step_by_id["evidence"]
    assert 'contracts_input="contracts"' in evidence_step["run"]
    assert "contracts_dir=$contracts_dir" in evidence_step["run"]
    assert 'receipts_input="drift/dod_receipts"' in evidence_step["run"]

    receipt_gate_step = next(
        step for step in verify_job["steps"] if step.get("name") == "Run Receipt-Gate"
    )
    assert "--contracts-dir" in receipt_gate_step["run"]
    assert "steps.evidence.outputs.contracts_dir" in receipt_gate_step["run"]
    assert "--receipts-dir" in receipt_gate_step["run"]
    assert "steps.evidence.outputs.receipts_dir" in receipt_gate_step["run"]


@pytest.mark.parametrize(
    "commit_texts", [[], ["fix(OMN-20315): bind late ticket; $(false)"]]
)
def test_receipt_gate_forwards_authenticated_snapshot_commit_texts(
    tmp_path: Path, commit_texts: list[str]
) -> None:
    """Execute the caller step: late tickets bind through captured commit messages."""
    steps = _load_workflow()["jobs"]["verify"]["steps"]
    step = next(step for step in steps if step.get("name") == "Run Receipt-Gate")
    for field, value in {
        "body": "Evidence-Ticket: OMN-20315",
        "title": "fix(OMN-20315): bounded evidence",
        "branch": "jonah/omn-19626-caller-evidence",
        "author": "onexbot-occ-writer[bot]",
        "number": "12196",
        "created_at": "2026-10-01T16:50:37Z",
        "commit_texts": "".join(f"{text}\n" for text in commit_texts),
    }.items():
        (tmp_path / f"pr_{field}.txt").write_text(value, encoding="utf-8")
    script = re.sub(
        r'(/[^\s"]*/)(pr_\w+\.txt)',
        lambda match: str(tmp_path / match[2]),
        step["run"],
    )
    script = script.replace(
        "${{ github.repository }}", "OmniNode-ai/onex_change_control"
    )
    script = re.sub(r"\$\{\{.*?\}\}", str(tmp_path), script)
    script = script.replace(
        '"$RECEIPT_GATE_PY" -m omnibase_core.validation.validator_receipt_gate_cli',
        "capture_receipt_gate",
    )
    result = subprocess.run(
        [
            "bash",
            "-e",
            "-c",
            "capture_receipt_gate() { printf '%s\\0' \"$@\"; }\n" + script,
        ],
        capture_output=True,
        check=True,
    )
    args = result.stdout.decode().split("\0")[:-1]
    forwarded = [
        args[index + 1]
        for index, value in enumerate(args)
        if value == "--pr-commit-text"
    ]
    assert forwarded == commit_texts
    assert args[args.index("--branch-name") + 1] == "jonah/omn-19626-caller-evidence"
