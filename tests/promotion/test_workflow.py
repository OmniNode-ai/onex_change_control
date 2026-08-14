# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression tests for OMN-11732 promotion workflow evidence helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.promotion.manifest import (
    ModelPromotionManifest,
    ModelPromotionManifestRepo,
    ModelPromotionRuntimeTarget,
)
from onex_change_control.promotion.workflow import (
    EnumPromotionFailureClass,
    EnumPromotionVerdict,
    audit_compat_dependencies,
    classify_promotion_gates,
    make_cross_repo_placeholder,
    make_per_repo_results,
    make_runtime_topology_placeholder,
    promotion_pr_plan,
    write_artifact_manifest,
    write_json,
)


def _manifest(*repos: ModelPromotionManifestRepo) -> ModelPromotionManifest:
    return ModelPromotionManifest(
        promotion_batch_id="promotion-2026.05.23-batch.1",
        generated_at=datetime(2026, 5, 23, 12, 0, tzinfo=UTC),
        runtime_target=ModelPromotionRuntimeTarget(),
        repos=repos,
    )


def test_compat_audit_blocks_production_dependency_ranges(tmp_path: Path) -> None:
    manifest = _manifest(
        ModelPromotionManifestRepo(
            repo="omniweb",
            dev_head_sha="a" * 40,
            main_base_sha="b" * 40,
            dependency_ranges=("omnibase-compat>=0.4.0",),
        )
    )

    audit = audit_compat_dependencies(manifest, workspace=tmp_path)

    assert audit.has_blockers
    assert audit.findings[0].path == "intended_promotion_manifest.json"
    assert audit.findings[0].classification == "production_blocker"


def test_compat_audit_scans_runtime_surfaces(tmp_path: Path) -> None:
    repo = tmp_path / "omniweb"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\ndependencies = ["omnibase_compat>=0.4.0"]\n'
    )
    manifest = _manifest(
        ModelPromotionManifestRepo(
            repo="omniweb",
            dev_head_sha="a" * 40,
            main_base_sha="b" * 40,
        )
    )

    audit = audit_compat_dependencies(manifest, workspace=tmp_path)

    assert audit.blocker_count == 1
    assert audit.findings[0].path == "pyproject.toml"


def test_promotion_pr_plan_preserves_base_head_and_shas() -> None:
    manifest = _manifest(
        ModelPromotionManifestRepo(
            repo="omnibase_core",
            dev_head_sha="a" * 40,
            main_base_sha="b" * 40,
        )
    )

    plan = promotion_pr_plan(manifest, dry_run=True)

    assert plan[0].repo == "omnibase_core"
    assert plan[0].wave == 1
    assert plan[0].blocked_by_waves == ()
    assert plan[0].base == "main"
    assert plan[0].head == "dev"
    assert plan[0].action == "planned"
    assert plan[0].dev_head_sha == "a" * 40


def test_promotion_pr_plan_records_dependency_waves() -> None:
    manifest = _manifest(
        ModelPromotionManifestRepo(
            repo="omniweb",
            dev_head_sha="a" * 40,
            main_base_sha="b" * 40,
        )
    )

    plan = promotion_pr_plan(manifest, dry_run=True)

    assert plan[0].wave == 4
    assert plan[0].blocked_by_waves == (1, 2, 3)


def test_gate_status_classifies_runtime_and_integration_blockers(
    tmp_path: Path,
) -> None:
    manifest = _manifest(
        ModelPromotionManifestRepo(
            repo="omnibase_core",
            dev_head_sha="a" * 40,
            main_base_sha="b" * 40,
        )
    )
    manifest_path = tmp_path / "manifest.json"
    compat_path = tmp_path / "compat.json"
    runtime_path = tmp_path / "runtime.json"
    cross_repo_path = tmp_path / "cross_repo.json"
    manifest_path.write_bytes(manifest.to_json_bytes())
    write_json(compat_path, audit_compat_dependencies(manifest, workspace=tmp_path))
    write_json(
        runtime_path,
        make_runtime_topology_placeholder(manifest=manifest, reason="not collected"),
    )
    write_json(
        cross_repo_path,
        make_cross_repo_placeholder(
            manifest=manifest,
            status="not_run",
            reason="integration suite not run",
        ),
    )

    status = classify_promotion_gates(
        manifest,
        compat_audit_path=compat_path,
        runtime_topology_path=runtime_path,
        cross_repo_path=cross_repo_path,
        dry_run=False,
    )

    assert status.verdict == EnumPromotionVerdict.BLOCKED
    assert status.promotable is False
    assert status.blocking_failure_class == EnumPromotionFailureClass.RUNTIME
    assert {failure.failure_class for failure in status.failures} == {
        EnumPromotionFailureClass.RUNTIME,
        EnumPromotionFailureClass.INTEGRATION,
    }


def test_per_repo_results_use_wire_contract_shape() -> None:
    manifest = _manifest(
        ModelPromotionManifestRepo(
            repo="omnibase_core",
            dev_head_sha="a" * 40,
            main_base_sha="b" * 40,
        )
    )

    results = make_per_repo_results(
        manifest,
        dry_run=False,
        created_urls={"omnibase_core": "https://github.com/OmniNode-ai/example/pull/1"},
    )

    assert results["promotion_batch_id"] == manifest.promotion_batch_id
    assert results["repos"][0]["action"] == "created"
    assert results["repos"][0]["url"].endswith("/pull/1")


def test_artifact_manifest_hashes_existing_artifacts(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "promotion_manifest.json").write_text('{"ok": true}\n')
    output = evidence_dir / "artifact_manifest.json"

    write_artifact_manifest(evidence_dir, output)

    payload = json.loads(output.read_text())
    assert payload["artifacts"][0]["path"] == "promotion_manifest.json"
    assert payload["artifacts"][0]["sha256"].startswith("sha256:")
