# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression coverage for target-aware seam-contract comparison bases."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "validation" / "check_seam_contract_coverage.py"


def _load_checker() -> ModuleType:
    name = "check_seam_contract_coverage"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _git_output_for(
    upstream: str | None, available: set[str]
) -> Callable[[list[str]], str]:
    def fake_git_output(args: list[str]) -> str:
        if args == ["git", "rev-parse", "--abbrev-ref", "@{upstream}"]:
            if upstream is None:
                raise checker.BaseResolutionError.upstream_unavailable()
            return upstream
        if args[:4] == ["git", "rev-parse", "--verify", "--quiet"]:
            ref = args[4].removesuffix("^{commit}")
            if ref in available:
                return "a" * 40
            raise checker.BaseResolutionError.base_unavailable(ref)
        pytest.fail(f"unexpected git command: {args}")

    return fake_git_output


@pytest.mark.unit
@pytest.mark.parametrize(
    ("branch", "target"),
    [
        ("main", "origin/main"),
        ("dev", "origin/dev"),
        ("hotfix/omn-17483", "origin/main"),
        ("jonah/omn-17483-seam-base", "origin/dev"),
    ],
)
def test_default_base_uses_configured_target_upstream(
    monkeypatch: pytest.MonkeyPatch, branch: str, target: str
) -> None:
    monkeypatch.setattr(checker, "_get_current_branch", lambda: branch)
    monkeypatch.setattr(
        checker, "_git_output", _git_output_for(target, {"origin/main", "origin/dev"})
    )
    assert checker._resolve_base(None) == target


@pytest.mark.unit
def test_explicit_base_overrides_branch_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(checker, "_get_current_branch", lambda: "HEAD")
    monkeypatch.setattr(
        checker,
        "_git_output",
        _git_output_for("origin/main", {"origin/main", "origin/dev"}),
    )
    assert checker._resolve_base("origin/dev") == "origin/dev"


@pytest.mark.unit
def test_missing_upstream_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checker, "_get_current_branch", lambda: "jonah/omn-17483-fix")
    monkeypatch.setattr(checker, "_git_output", _git_output_for(None, set()))
    with pytest.raises(checker.BaseResolutionError, match="configure an upstream"):
        checker._resolve_base(None)


@pytest.mark.unit
def test_unavailable_explicit_base_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(checker, "_git_output", _git_output_for("origin/dev", set()))
    with pytest.raises(checker.BaseResolutionError, match="unavailable"):
        checker._resolve_base("origin/dev")


@pytest.mark.unit
def test_main_returns_actionable_error_for_unavailable_base(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        checker,
        "_resolve_base",
        lambda _base: (_ for _ in ()).throw(
            checker.BaseResolutionError("comparison base 'origin/dev' is unavailable")
        ),
    )
    assert checker.main(["--base", "origin/dev"]) == 2
    assert "trustworthy base" in capsys.readouterr().out


@pytest.mark.unit
def test_detached_head_requires_explicit_event_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(checker, "_get_current_branch", lambda: "HEAD")
    with pytest.raises(checker.BaseResolutionError, match="HEAD is detached"):
        checker._resolve_base(None)


@pytest.mark.unit
def test_complete_ci_event_binding_supplies_detached_comparison_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONEX_SEAM_CONTRACT_BASE", "origin/dev")
    monkeypatch.setenv("ONEX_SEAM_CONTRACT_HEAD_REF", "a" * 40)
    monkeypatch.setenv("ONEX_SEAM_CONTRACT_TICKET_ID", "jonah/omn-17483-fix")

    assert checker._event_binding_from_environment() == (
        "origin/dev",
        "a" * 40,
        "jonah/omn-17483-fix",
    )


@pytest.mark.unit
def test_detached_ci_event_binding_reaches_canonical_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONEX_SEAM_CONTRACT_BASE", "origin/dev")
    monkeypatch.setenv("ONEX_SEAM_CONTRACT_HEAD_REF", "a" * 40)
    monkeypatch.setenv("ONEX_SEAM_CONTRACT_TICKET_ID", "jonah/omn-17483-fix")
    monkeypatch.setattr(checker, "_get_current_branch", lambda: "HEAD")
    monkeypatch.setattr(
        checker,
        "_git_output",
        _git_output_for("origin/dev", {"origin/dev", "a" * 40}),
    )
    monkeypatch.setattr(checker, "_get_changed_files", lambda _base, _head: [])

    assert checker.main([]) == 0


@pytest.mark.unit
def test_partial_ci_event_binding_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONEX_SEAM_CONTRACT_BASE", "origin/dev")

    with pytest.raises(checker.BaseResolutionError, match="partial seam comparison"):
        checker._event_binding_from_environment()


@pytest.mark.unit
def test_full_precommit_job_binds_canonical_pr_event_identity() -> None:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    )
    steps = workflow["jobs"]["pre-commit"]["steps"]
    full_precommit = next(
        step for step in steps if step.get("name") == "Run full pre-commit"
    )

    assert full_precommit["env"] == {
        "ONEX_SEAM_CONTRACT_BASE": "origin/${{ github.base_ref }}",
        "ONEX_SEAM_CONTRACT_HEAD_REF": "${{ github.sha }}",
        "ONEX_SEAM_CONTRACT_TICKET_ID": "${{ github.head_ref || github.ref_name }}",
    }
    assert "pre-commit run --all-files" in full_precommit["run"]


@pytest.mark.unit
def test_self_tracking_feature_branch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "jonah/omn-17483-fix"
    monkeypatch.setattr(checker, "_get_current_branch", lambda: branch)
    monkeypatch.setattr(
        checker,
        "_git_output",
        _git_output_for(f"origin/{branch}", {f"origin/{branch}"}),
    )
    with pytest.raises(checker.BaseResolutionError, match="tracks itself"):
        checker._resolve_base(None)


@pytest.mark.unit
def test_first_commit_diff_failure_does_not_fall_back_to_head_tilde_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        returncode = 128
        stderr = "fatal: ambiguous argument 'HEAD~1'"
        stdout = ""

    monkeypatch.setattr(checker.subprocess, "run", lambda *_args, **_kwargs: Result())
    with pytest.raises(checker.BaseResolutionError, match="could not diff"):
        checker._get_changed_files("origin/dev", "HEAD~1")


@pytest.mark.unit
def test_true_seam_change_without_ticket_contract_remains_blocking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(checker, "_resolve_base", lambda _base: "origin/dev")
    monkeypatch.setattr(
        checker,
        "_get_changed_files",
        lambda _base, _head: ["src/onex_change_control/kafka/producer.py"],
    )
    monkeypatch.setattr(checker, "_get_current_branch", lambda: "jonah/omn-17483-fix")

    assert checker.main(["--contracts-dir", str(tmp_path)]) == 1
    assert "Seam ticket OMN-17483 is missing a contract" in capsys.readouterr().out
