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


def test_compat_audit_excludes_self_repo_matches(tmp_path: Path) -> None:
    """OMN-16279 step 1: `omnibase_compat`'s own tree/tests/lockfile
    trivially mention its own package name -- that is a self-reference, not
    a dependency edge, and must never be flagged. Covers both the
    dependency_ranges path and the file-scan path in the same repo entry.
    """
    repo = tmp_path / "omnibase_compat"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "omnibase_compat"\ndependencies = []\n'
    )
    (repo / "uv.lock").write_text('name = "omnibase_compat"\nversion = "0.4.0"\n')
    manifest = _manifest(
        ModelPromotionManifestRepo(
            repo="omnibase_compat",
            dev_head_sha="a" * 40,
            main_base_sha="b" * 40,
            dependency_ranges=("omnibase_compat==0.4.0",),
        )
    )

    audit = audit_compat_dependencies(manifest, workspace=tmp_path)

    assert audit.blocker_count == 0
    assert audit.findings == ()


def test_compat_audit_excludes_non_manifest_files(tmp_path: Path) -> None:
    """OMN-16279 step 2: the narrowed production-surface predicate matches
    only exact dependency-manifest filenames. A test file or a
    build/workspace orchestration script that happens to sit under a path
    containing 'runtime', 'compose', 'deployment', 'kustomization',
    'manifest', or 'policy' and mentions `omnibase_compat` (e.g. as a
    sibling-repo name for a multi-repo workspace build, or in an unrelated
    test fixture) is no longer flagged -- only real dependency-manifest
    filenames are.
    """
    repo = tmp_path / "omnibase_infra"
    repo.mkdir()
    scripts_dir = repo / "scripts" / "runtime_build"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "deploy-runtime.sh").write_text(
        "#!/bin/bash\n# builds omnibase_compat as a sibling-repo workspace dep\n"
    )
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_runtime_deployment_models.py").write_text(
        '"""Fixture referencing omnibase_compat in a test comment."""\n'
    )
    (repo / "kustomization.yaml").write_text("images:\n  - name: omnibase_compat\n")
    manifest = _manifest(
        ModelPromotionManifestRepo(
            repo="omnibase_infra",
            dev_head_sha="a" * 40,
            main_base_sha="b" * 40,
        )
    )

    audit = audit_compat_dependencies(manifest, workspace=tmp_path)

    assert audit.blocker_count == 0
    assert audit.findings == ()


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
    """A cross-repo status OTHER than the "not_run_in_mvp" placeholder string
    (i.e. a real, evaluated integration failure once OMN-15067 lands) still
    blocks. Runtime stays advisory here because the placeholder producer
    always writes "not_collected" (OMN-16279) — see the dedicated advisory
    test below for that half of the behavior.
    """
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
    assert status.blocking_failure_class == EnumPromotionFailureClass.INTEGRATION
    assert {failure.failure_class for failure in status.failures} == {
        EnumPromotionFailureClass.RUNTIME,
        EnumPromotionFailureClass.INTEGRATION,
    }
    failures_by_class = {failure.failure_class: failure for failure in status.failures}
    assert failures_by_class[EnumPromotionFailureClass.RUNTIME].blocking is False
    assert failures_by_class[EnumPromotionFailureClass.INTEGRATION].blocking is True


def test_gate_status_demotes_unimplemented_placeholder_findings_to_advisory(
    tmp_path: Path,
) -> None:
    """OMN-16279: the exact placeholder statuses nightly-promote.yml actually
    emits ("not_collected" for the runtime capture, "not_run_in_mvp" for the
    cross-repo suite) must not block an otherwise-clean promotion — they are
    unimplemented stubs, not real gate evaluations, and blocked 4/6 nightly
    runs by construction. Findings stay visible in the evidence bundle
    (non-blocking), they just stop vetoing the verdict.
    """
    manifest = _manifest(
        ModelPromotionManifestRepo(
            repo="omnibase_core",
            dev_head_sha="a" * 40,
            main_base_sha="b" * 40,
        )
    )
    compat_path = tmp_path / "compat.json"
    runtime_path = tmp_path / "runtime.json"
    cross_repo_path = tmp_path / "cross_repo.json"
    write_json(compat_path, audit_compat_dependencies(manifest, workspace=tmp_path))
    write_json(
        runtime_path,
        make_runtime_topology_placeholder(
            manifest=manifest,
            reason="not_collected_by_github_runner",
        ),
    )
    write_json(
        cross_repo_path,
        make_cross_repo_placeholder(
            manifest=manifest,
            status="not_run_in_mvp",
            reason="MVP workflow records manifest and per-repo state first",
        ),
    )

    status = classify_promotion_gates(
        manifest,
        compat_audit_path=compat_path,
        runtime_topology_path=runtime_path,
        cross_repo_path=cross_repo_path,
        dry_run=False,
    )

    assert status.verdict == EnumPromotionVerdict.PASSED
    assert status.promotable is True
    assert status.blocking_failure_class is None
    assert {failure.failure_class for failure in status.failures} == {
        EnumPromotionFailureClass.RUNTIME,
        EnumPromotionFailureClass.INTEGRATION,
    }
    assert all(failure.blocking is False for failure in status.failures)
    assert all("OMN-16279" in failure.reason for failure in status.failures)


def test_gate_status_compat_finding_is_advisory_with_provenance(
    tmp_path: Path,
) -> None:
    """OMN-16279 step 3: the compat-dependency check is a real, evaluated
    check (unlike the two unimplemented placeholders above) but has never
    returned 0 in real mode across 25+ nights because most of the fleet
    legitimately depends on `omnibase_compat` by the sanctioned
    compat -> core -> spi -> infra layering. What remains after the steps
    1-2 narrowing is exactly that sanctioned residual, so it is demoted to
    advisory (`blocking: false`) with an `[OMN-16279: ADVISORY]` provenance
    note citing the OMN-16283 follow-up (diff-against-last-promoted-main
    baseline), unconditionally -- not keyed to a placeholder string, because
    there is no "real" variant of this check to distinguish from a stub.
    A manifest-file compat hit is still recorded as a finding; it just no
    longer vetoes the verdict.
    """
    manifest = _manifest(
        ModelPromotionManifestRepo(
            repo="omniweb",
            dev_head_sha="a" * 40,
            main_base_sha="b" * 40,
            dependency_ranges=("omnibase-compat>=0.4.0",),
        )
    )
    compat_path = tmp_path / "compat.json"
    runtime_path = tmp_path / "runtime.json"
    cross_repo_path = tmp_path / "cross_repo.json"
    write_json(compat_path, audit_compat_dependencies(manifest, workspace=tmp_path))
    write_json(
        runtime_path,
        make_runtime_topology_placeholder(manifest=manifest, reason="not collected"),
    )
    write_json(
        cross_repo_path,
        make_cross_repo_placeholder(
            manifest=manifest,
            status="not_run_in_mvp",
            reason="MVP workflow records manifest and per-repo state first",
        ),
    )

    status = classify_promotion_gates(
        manifest,
        compat_audit_path=compat_path,
        runtime_topology_path=runtime_path,
        cross_repo_path=cross_repo_path,
        dry_run=False,
    )

    assert status.verdict == EnumPromotionVerdict.PASSED
    assert status.promotable is True
    assert status.blocking_failure_class is None
    failures_by_class = {failure.failure_class: failure for failure in status.failures}
    code_failure = failures_by_class[EnumPromotionFailureClass.CODE]
    assert code_failure.blocking is False
    assert "OMN-16279: ADVISORY" in code_failure.reason
    assert "OMN-16283" in code_failure.reason


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
