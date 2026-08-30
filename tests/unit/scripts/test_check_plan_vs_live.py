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


def test_uncited_work_item_row_warns_by_default(tmp_path: Path) -> None:
    plan = _write_text(
        tmp_path,
        "plan.md",
        "| A5 | **N=10 is unreachable today** -- nothing computes it. | proof |\n",
    )

    report = checker.evaluate_plan_vs_live(
        plan_paths=[plan],
        workspace_root=tmp_path,
        current_repo_root=tmp_path,
        base_ref=None,
        default_pr_repo=None,
        ticket_states={},
        require_linear=False,
    )

    # Default (two-phase rollout, OMN-15105): a warning, not a required failure --
    # the existing row backlog must not instantly wedge the gate.
    assert report["status"] == "pass"
    assert report["warning_count"] == 1
    assert report["warnings"][0]["raw_text"].startswith("A5:")


def test_uncited_work_item_row_fails_when_flag_set(tmp_path: Path) -> None:
    plan = _write_text(
        tmp_path,
        "plan.md",
        "| A7 | Composition is currently **unrepresentable**. | proof |\n",
    )

    report = checker.evaluate_plan_vs_live(
        plan_paths=[plan],
        workspace_root=tmp_path,
        current_repo_root=tmp_path,
        base_ref=None,
        default_pr_repo=None,
        ticket_states={},
        require_linear=False,
        fail_on_uncited=True,
    )

    assert report["status"] == "fail"
    assert report["failures"][0]["raw_text"].startswith("A7:")


def test_cited_work_item_row_is_not_flagged_negative_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Hermetic per the sibling test_closed_unmerged_pr_fails_with_fake_gh: the
    # row cites `omnimarket#1869`, which routes through verify_pr_reference ->
    # a real `gh pr view` subprocess. Stub gh on PATH so the assertion does not
    # depend on ambient `gh` auth/network (was CI-red with no GH_TOKEN in the
    # product-readiness-shadow job while passing locally with authenticated gh).
    gh = _write_text(
        tmp_path,
        "gh",
        """#!/usr/bin/env bash
printf '{"state":"MERGED","mergedAt":"2026-07-24T00:00:00Z",'
printf '"url":"https://example.test/pr/1869","headRefOid":"abc"}'
""",
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    plan = _write_text(
        tmp_path,
        "plan.md",
        "| A2 | Root-cause DONE. OMN-14893 Done 2026-07-24 (`omnimarket#1869` "
        "MERGED). | proof |\n",
    )

    report = checker.evaluate_plan_vs_live(
        plan_paths=[plan],
        workspace_root=tmp_path,
        current_repo_root=tmp_path,
        base_ref=None,
        default_pr_repo=None,
        ticket_states={"OMN-14893": "Done"},
        require_linear=False,
        fail_on_uncited=True,
    )

    assert report["status"] == "pass"
    assert report["warning_count"] == 0


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


def _commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True
    )


def _mutually_referencing_head_repo(tmp_path: Path) -> Path:
    """Repo whose HEAD adds two plan docs that cite each other; base has neither."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _write_text(repo, "README.md", "base\n")
    _commit_all(repo, "base")
    subprocess.run(
        ["git", "branch", "-M", "base"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "checkout", "-b", "feature"], cwd=repo, check=True, capture_output=True
    )
    _write_text(
        repo,
        "docs/plans/plan-a.md",
        "Companion plan: `docs/plans/plan-b.md`.\n",
    )
    _write_text(
        repo,
        "docs/plans/plan-b.md",
        "Parent plan: `docs/plans/plan-a.md`.\n",
    )
    _commit_all(repo, "add two mutually referencing plans")
    return repo


def test_same_pr_mutually_referencing_paths_resolve_on_head(tmp_path: Path) -> None:
    """OMN-17185 AC1: paths added by the same changeset are not 'missing'."""
    repo = _mutually_referencing_head_repo(tmp_path)

    report = checker.evaluate_plan_vs_live(
        plan_paths=[repo / "docs/plans/plan-a.md", repo / "docs/plans/plan-b.md"],
        workspace_root=tmp_path,
        current_repo_root=repo,
        base_ref="base",
        head_ref="HEAD",
        default_pr_repo=None,
        ticket_states={},
        require_linear=False,
    )

    assert report["status"] == "pass", report["failures"]
    assert report["failed_count"] == 0
    assert {finding["status"] for finding in report["findings"]} == {"pass"}


def test_same_pr_paths_still_fail_against_base_ref_negative_control(
    tmp_path: Path,
) -> None:
    """Without head-ref resolution the same references fail -- the OMN-17185 bug."""
    repo = _mutually_referencing_head_repo(tmp_path)

    report = checker.evaluate_plan_vs_live(
        plan_paths=[repo / "docs/plans/plan-a.md", repo / "docs/plans/plan-b.md"],
        workspace_root=tmp_path,
        current_repo_root=repo,
        base_ref="base",
        head_ref=None,
        default_pr_repo=None,
        ticket_states={},
        require_linear=False,
    )

    assert report["status"] == "fail"
    assert report["failed_count"] == 2
    assert report["failures"][0]["message"] == "path missing on target branch"


def test_head_ref_does_not_apply_to_sibling_repos(tmp_path: Path) -> None:
    """A sibling repo path is still checked against its own base ref."""
    repo = _mutually_referencing_head_repo(tmp_path)
    sibling = tmp_path / "omnimarket"
    sibling.mkdir()
    _init_repo(sibling)
    _write_text(sibling, "README.md", "sibling\n")
    _commit_all(sibling, "base")
    subprocess.run(
        ["git", "branch", "-M", "base"], cwd=sibling, check=True, capture_output=True
    )
    plan = _write_text(
        repo,
        "docs/plans/plan-c.md",
        "Sibling file: `omnimarket/src/absent.py`.\n",
    )
    _commit_all(repo, "add plan citing sibling repo path")

    report = checker.evaluate_plan_vs_live(
        plan_paths=[plan],
        workspace_root=tmp_path,
        current_repo_root=repo,
        base_ref="base",
        head_ref="HEAD",
        default_pr_repo=None,
        ticket_states={},
        require_linear=False,
    )

    assert report["status"] == "fail"
    assert report["failures"][0]["raw_text"] == "omnimarket/src/absent.py"


def test_cli_head_ref_defaults_to_head(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI default resolves same-changeset paths without an explicit flag."""
    repo = _mutually_referencing_head_repo(tmp_path)

    rc = checker.main(
        [
            "--workspace-root",
            str(tmp_path),
            "--current-repo-root",
            str(repo),
            "--base-ref",
            "base",
            str(repo / "docs/plans/plan-a.md"),
            str(repo / "docs/plans/plan-b.md"),
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert report["status"] == "pass", report["failures"]
