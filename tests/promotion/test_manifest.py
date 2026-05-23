# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression tests for OMN-11720 promotion manifest generation."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.promotion.manifest import (
    ModelPromotionRuntimeTarget,
    generate_promotion_manifest,
    load_promotion_manifest,
    verify_promotion_manifest,
)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "--allow-empty", "-m", message)


def _create_repo(workspace: Path, name: str) -> Path:
    repo = workspace / name
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "checkout", "-b", "main")
    (repo / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "example-package"',
                'version = "1.2.3"',
                'dependencies = ["omnibase-core>=0.36.0", "pydantic>=2"]',
                "",
            ]
        )
    )
    (repo / "uv.lock").write_text("lock-v1\n")
    _commit(repo, "initial main")
    _git(repo, "checkout", "-b", "dev")
    (repo / "uv.lock").write_text("lock-v2\n")
    _commit(repo, "dev lock")
    return repo


def test_generate_manifest_contains_required_repo_fields(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = _create_repo(workspace, "example")

    manifest = generate_promotion_manifest(
        workspace=workspace,
        promotion_batch_id="promotion-2026.05.23-batch.1",
        runtime_target=ModelPromotionRuntimeTarget(lane="stability", profile="test"),
        repos=("example",),
        generated_at=datetime(2026, 5, 23, 1, 0, tzinfo=UTC),
    )

    entry = manifest.repos[0]
    assert entry.repo == "example"
    assert entry.dev_head_sha == _git(repo, "rev-parse", "dev")
    assert entry.main_base_sha == _git(repo, "rev-parse", "main")
    assert entry.package_name == "example-package"
    assert entry.package_version == "1.2.3"
    assert entry.dependency_ranges == ("omnibase-core>=0.36.0", "pydantic>=2")
    assert "uv.lock" in entry.lock_hashes
    assert manifest.manifest_sha256.startswith("sha256:")


def test_manifest_json_round_trips_with_digest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _create_repo(workspace, "example")
    output = tmp_path / "intended_promotion_manifest.json"

    manifest = generate_promotion_manifest(
        workspace=workspace,
        promotion_batch_id="promotion-2026.05.23-batch.1",
        runtime_target=ModelPromotionRuntimeTarget(),
        repos=("example",),
        generated_at=datetime(2026, 5, 23, 1, 0, tzinfo=UTC),
    )
    output.write_bytes(manifest.to_json_bytes())

    payload = json.loads(output.read_text())
    assert payload["manifest_sha256"] == manifest.manifest_sha256
    assert load_promotion_manifest(output) == manifest


def test_verify_manifest_reports_repo_mismatch(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = _create_repo(workspace, "example")
    manifest = generate_promotion_manifest(
        workspace=workspace,
        promotion_batch_id="promotion-2026.05.23-batch.1",
        runtime_target=ModelPromotionRuntimeTarget(),
        repos=("example",),
        generated_at=datetime(2026, 5, 23, 1, 0, tzinfo=UTC),
    )

    (repo / "uv.lock").write_text("lock-v3\n")
    _commit(repo, "change dev")

    assert verify_promotion_manifest(manifest, workspace=workspace) == ["example"]
