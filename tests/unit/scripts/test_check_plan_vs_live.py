# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for the plan-vs-live checker."""

from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING

from onex_change_control.enums.enum_doc_reference_type import EnumDocReferenceType
from onex_change_control.models.model_doc_reference import ModelDocReference
from onex_change_control.scripts import check_plan_vs_live as checker

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_text(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def test_missing_file_path_fails_against_target_ref(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _write_text(repo, "docs/plans/plan.md", "See `src/missing.py`.\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True
    )

    report = checker.evaluate_plan_vs_live(
        plan_paths=[repo / "docs/plans/plan.md"],
        workspace_root=tmp_path,
        current_repo_root=repo,
        base_ref="HEAD",
        default_pr_repo=None,
        ticket_states={},
        require_linear=False,
    )

    assert report["status"] == "fail"
    assert report["failures"][0]["raw_text"] == "src/missing.py"
    assert report["failures"][0]["message"] == "path missing on target branch"


def test_existing_file_path_passes_against_target_ref(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _write_text(repo, "src/live.py", "VALUE = 1\n")
    plan = _write_text(repo, "docs/plans/plan.md", "See `src/live.py`.\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True
    )

    report = checker.evaluate_plan_vs_live(
        plan_paths=[plan],
        workspace_root=tmp_path,
        current_repo_root=repo,
        base_ref="HEAD",
        default_pr_repo=None,
        ticket_states={},
        require_linear=False,
    )

    assert report["status"] == "pass"
    assert report["findings"][0]["status"] == "pass"


def test_ticket_state_mismatch_fails(tmp_path: Path) -> None:
    plan = _write_text(tmp_path, "plan.md", "OMN-12691 is Done.\n")

    report = checker.evaluate_plan_vs_live(
        plan_paths=[plan],
        workspace_root=tmp_path,
        current_repo_root=tmp_path,
        base_ref=None,
        default_pr_repo=None,
        ticket_states={"OMN-12691": "In Progress"},
        require_linear=False,
    )

    assert report["status"] == "fail"
    assert (
        report["failures"][0]["message"] == "expected Done, live state is In Progress"
    )


def test_ticket_state_without_linear_skips_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_text(tmp_path, "plan.md", "OMN-12691 is Done.\n")
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)

    report = checker.evaluate_plan_vs_live(
        plan_paths=[plan],
        workspace_root=tmp_path,
        current_repo_root=tmp_path,
        base_ref=None,
        default_pr_repo=None,
        ticket_states={},
        require_linear=False,
    )

    assert report["status"] == "pass"
    assert report["skipped_count"] == 1


def test_ticket_state_without_linear_can_fail_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _write_text(tmp_path, "plan.md", "OMN-12691 is Done.\n")
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)

    report = checker.evaluate_plan_vs_live(
        plan_paths=[plan],
        workspace_root=tmp_path,
        current_repo_root=tmp_path,
        base_ref=None,
        default_pr_repo=None,
        ticket_states={},
        require_linear=True,
    )

    assert report["status"] == "fail"
    assert report["failures"][0]["message"] == "Linear state unavailable"


def test_closed_unmerged_pr_fails_with_fake_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gh = _write_text(
        tmp_path,
        "gh",
        """#!/usr/bin/env bash
printf '{"state":"CLOSED","mergedAt":null,"url":"https://example.test/pr/1033","headRefOid":"abc"}'
""",
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    ref = ModelDocReference(
        doc_path="plan.md",
        line_number=1,
        reference_type=EnumDocReferenceType.PR_NUMBER,
        raw_text="omnimarket#1033",
    )

    finding = checker.verify_pr_reference(ref, default_repo=None)

    assert finding.status == "fail"
    assert finding.message == "OmniNode-ai/omnimarket#1033 is closed unmerged"


def test_cli_emits_json_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _write_text(tmp_path, "plan.md", "OMN-12691 is Done.\n")
    states = _write_text(tmp_path, "states.json", json.dumps({"OMN-12691": "Done"}))

    rc = checker.main(
        [
            "--base-ref",
            "",
            "--ticket-state-file",
            str(states),
            str(plan),
        ]
    )

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
