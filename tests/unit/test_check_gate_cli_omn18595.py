# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Execute the check callers despite core's overlapping console command."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml


def _workflow_steps(path: str, job: str) -> list[dict[str, Any]]:
    workflow = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = workflow["jobs"][job]["steps"]
    return steps


@pytest.mark.parametrize("caller", ["ci", "pre-commit"])
@pytest.mark.parametrize("new_script", [False, True])
def test_scripts_callers_execute_inventory_guard(
    tmp_path: Path, caller: str, *, new_script: bool
) -> None:
    if caller == "ci":
        steps = _workflow_steps(".github/workflows/ci.yml", "imperative-contract-guard")
        command = next(
            step["run"] for step in steps if "--scan-scripts" in step.get("run", "")
        )
    else:
        config = yaml.safe_load(
            Path(".pre-commit-config.yaml").read_text(encoding="utf-8")
        )
        command = next(
            hook["entry"]
            for repo in config["repos"]
            for hook in repo["hooks"]
            if hook["id"] == "check-scripts-canonical-form"
        )
    args = shlex.split(command.replace("\\\n", ""))
    command_path = (
        sys.executable
        if args[2] == "python"
        else str(Path(sys.executable).with_name(args[2]))
    )
    if new_script:
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "new.py").write_text("print('unregistered')\n", encoding="utf-8")
    result = subprocess.run(
        [command_path, *args[3:]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == int(new_script), result.stdout + result.stderr
    assert "Scripts canonical-form guard" in result.stdout
    if new_script:
        assert "scripts/new.py: new_unregistered" in result.stdout


@pytest.mark.parametrize(
    "body", ["Evidence-Ticket: OMN-18595", "[skip-receipt-gate: retired-approval]"]
)
def test_receipt_caller_reaches_validation_and_rejects_bad_evidence(
    tmp_path: Path, body: str
) -> None:
    steps = _workflow_steps(".github/workflows/call-receipt-gate.yml", "verify")
    script = next(
        step["run"] for step in steps if step.get("name") == "Run Receipt-Gate"
    )
    for field, value in {
        "body": body,
        "title": "fix(OMN-18595): check caller regression",
        "branch": "landing/omn-18595-check-caller",
        "author": "regression-test",
        "number": "13749",
        "created_at": "2026-10-12T22:28:08Z",
        "commit_texts": "",
    }.items():
        (tmp_path / f"pr_{field}.txt").write_text(value, encoding="utf-8")
    allowlists = tmp_path / "allowlists"
    allowlists.mkdir()
    (allowlists / "skip_token_approvals.yaml").write_text(
        "approvals: []\n", encoding="utf-8"
    )
    script = re.sub(
        r'(/[^\s"]*/)(pr_\w+\.txt)',
        lambda match: str(tmp_path / match[2]),
        script,
    )
    for expression, value in {
        "github.repository": "OmniNode-ai/onex_change_control",
        "steps.evidence.outputs.root": str(tmp_path),
        "steps.evidence.outputs.contracts_dir": str(tmp_path / "contracts"),
        "steps.evidence.outputs.receipts_dir": str(tmp_path / "receipts"),
        "steps.branch_policy.outputs.target_branch": "dev",
        "steps.branch_policy.outputs.policy_mode": "legacy",
        "steps.resolve_evidence_source.outputs.occ_source_kind || 'self'": "self",
    }.items():
        script = script.replace("${{ " + expression + " }}", value)
    assert "${{" not in script
    result = subprocess.run(
        [
            "bash",
            "-e",
            "-c",
            "RECEIPT_GATE_PY=" + shlex.quote(sys.executable) + "\n" + script,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    output = result.stdout + result.stderr
    if body.startswith("[skip-"):
        assert "skip-receipt-gate" in output
        assert "REJECTED" in output or "RECEIPT GATE FAILED" in output
    else:
        assert "RECEIPT GATE FAILED" in output
    assert "unrecognized arguments" not in result.stderr
