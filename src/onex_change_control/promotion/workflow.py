# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Evidence helpers for the nightly dev-to-main promotion workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from onex_change_control.promotion.manifest import (
    ModelPromotionManifest,
    load_promotion_manifest,
)

COMPAT_PACKAGE_NAMES = ("omnibase_compat", "omnibase-compat")
PRODUCTION_SURFACE_FILES = {
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}
PRODUCTION_SURFACE_PARTS = (
    "runtime",
    "compose",
    "docker-compose",
    "deployment",
    "kustomization",
    "manifest",
    "policy",
)
PROMOTION_WAVES: tuple[tuple[str, ...], ...] = (
    ("omnibase_compat", "omnibase_core"),
    ("omnibase_spi", "omnibase_infra"),
    ("omnimarket", "omniclaude", "omniintelligence", "omnimemory"),
    (
        "omnidash",
        "omniweb",
        "onex_change_control",
        "omninode_infra",
        "onex-self-extending-agent",
    ),
)
PASS_STATUSES = frozenset({"pass", "passed", "ok", "healthy", "success"})


class EnumPromotionVerdict(StrEnum):
    """Promotion verdicts written into the OCC evidence bundle."""

    PLANNED = "planned"
    PASSED = "passed"
    BLOCKED = "blocked"


class EnumPromotionFailureClass(StrEnum):
    """Failure classes from the dev/main branch split plan."""

    CODE = "code"
    INTEGRATION = "integration"
    RUNTIME = "runtime"
    FLAKY_INFRA = "flaky_infra"


class ModelCompatDependencyFinding(BaseModel):
    """A production-surface reference to the compatibility shim package."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str = Field(min_length=1)
    path: str = Field(min_length=1)
    surface: str = Field(min_length=1)
    classification: str = "production_blocker"
    matched_token: str = Field(min_length=1)


class ModelCompatDependencyAudit(BaseModel):
    """Result of the weekend promotion compat dependency audit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "1.0.0"
    checked_at: datetime
    promotion_batch_id: str
    repos: tuple[str, ...]
    blocker_count: int
    findings: tuple[ModelCompatDependencyFinding, ...]

    @property
    def has_blockers(self) -> bool:
        """Return whether promotion must stop."""
        return self.blocker_count > 0


class ModelPromotionFailureEvidence(BaseModel):
    """A classified reason that blocks or annotates promotion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    failure_class: EnumPromotionFailureClass
    source: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    blocking: bool = True


class ModelPromotionGateStatus(BaseModel):
    """Gate classification produced from observed promotion artifacts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "1.0.0"
    promotion_batch_id: str
    evaluated_at: datetime
    dry_run: bool
    verdict: EnumPromotionVerdict
    promotable: bool
    blocking_failure_class: EnumPromotionFailureClass | None = None
    failures: tuple[ModelPromotionFailureEvidence, ...]


class ModelPromotionPrPlanEntry(BaseModel):
    """One planned or created promotion PR."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str
    wave: int
    blocked_by_waves: tuple[int, ...]
    base: str
    head: str
    dev_head_sha: str
    main_base_sha: str
    action: str
    url: str | None = None


class ModelPromotionVerification(BaseModel):
    """Workflow-produced verification summary for a promotion batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "1.0.0"
    promotion_batch_id: str
    verifier_identity: str
    verified_at: datetime
    dry_run: bool
    verdict: EnumPromotionVerdict
    blocking_failure_class: EnumPromotionFailureClass | None = None
    intended_manifest_digest: str
    per_repo_results_digest: str
    compat_dependency_audit_digest: str
    runtime_topology_proof_digest: str
    cross_repo_integration_result_digest: str
    gate_status_digest: str
    promotion_prs: tuple[ModelPromotionPrPlanEntry, ...]


def _json_bytes(payload: BaseModel | dict[str, Any]) -> bytes:
    if isinstance(payload, BaseModel):
        data = payload.model_dump(mode="json")
    else:
        data = payload
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def file_sha256(path: Path) -> str:
    """Return the SHA-256 digest for a file."""
    return _sha256_bytes(path.read_bytes())


def write_json(path: Path, payload: BaseModel | dict[str, Any]) -> str:
    """Write stable JSON and return its digest."""
    encoded = _json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return _sha256_bytes(encoded)


def _contains_compat_token(text: str) -> str | None:
    for token in COMPAT_PACKAGE_NAMES:
        if token in text:
            return token
    return None


def _is_production_surface(path: Path) -> bool:
    if path.name in PRODUCTION_SURFACE_FILES:
        return True
    normalized = "/".join(path.parts).lower()
    return any(part in normalized for part in PRODUCTION_SURFACE_PARTS)


def _scan_repo_for_compat(
    repo_path: Path, repo: str
) -> list[ModelCompatDependencyFinding]:
    findings: list[ModelCompatDependencyFinding] = []
    for path in repo_path.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(repo_path)
        if not _is_production_surface(relative):
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        token = _contains_compat_token(text)
        if token is None:
            continue
        findings.append(
            ModelCompatDependencyFinding(
                repo=repo,
                path=str(relative),
                surface=relative.name,
                matched_token=token,
            )
        )
    return findings


def audit_compat_dependencies(
    manifest: ModelPromotionManifest,
    *,
    workspace: Path,
) -> ModelCompatDependencyAudit:
    """Find production dependency references to ``omnibase_compat``."""
    findings: list[ModelCompatDependencyFinding] = []
    for entry in manifest.repos:
        for dependency in entry.dependency_ranges:
            token = _contains_compat_token(dependency)
            if token is not None:
                findings.append(
                    ModelCompatDependencyFinding(
                        repo=entry.repo,
                        path="intended_promotion_manifest.json",
                        surface="promotion_manifest_dependencies",
                        matched_token=token,
                    )
                )
        repo_path = workspace / entry.repo
        if repo_path.exists():
            findings.extend(_scan_repo_for_compat(repo_path, entry.repo))

    blockers = sum(
        1 for finding in findings if finding.classification == "production_blocker"
    )
    return ModelCompatDependencyAudit(
        checked_at=datetime.now(UTC),
        promotion_batch_id=manifest.promotion_batch_id,
        repos=tuple(entry.repo for entry in manifest.repos),
        blocker_count=blockers,
        findings=tuple(findings),
    )


def make_runtime_topology_placeholder(
    *,
    manifest: ModelPromotionManifest,
    reason: str,
) -> dict[str, Any]:
    """Return explicit runtime-proof absence evidence for dry-run runners."""
    return {
        "schema_version": "1.0.0",
        "promotion_batch_id": manifest.promotion_batch_id,
        "captured_at": datetime.now(UTC).isoformat(),
        "runtime_target": manifest.runtime_target.model_dump(mode="json"),
        "status": "not_collected",
        "reason": reason,
        "required_fields": [
            "runtime_profile",
            "compose_project",
            "container_names",
            "image_digest",
            "package_versions",
            "active_handler_count",
            "owned_command_topics",
            "subscribed_event_topics",
            "projection_freshness",
        ],
    }


def make_cross_repo_placeholder(
    *,
    manifest: ModelPromotionManifest,
    status: str,
    reason: str,
) -> dict[str, Any]:
    """Return cross-repo verification summary for the MVP workflow stage."""
    return {
        "schema_version": "1.0.0",
        "promotion_batch_id": manifest.promotion_batch_id,
        "verified_at": datetime.now(UTC).isoformat(),
        "status": status,
        "reason": reason,
        "repos": [entry.repo for entry in manifest.repos],
    }


def _repo_wave(repo: str) -> int:
    for index, wave in enumerate(PROMOTION_WAVES, start=1):
        if repo in wave:
            return index
    msg = f"repo is not in the promotion wave registry: {repo}"
    raise ValueError(msg)


def _blocked_by_waves(wave: int) -> tuple[int, ...]:
    return tuple(range(1, wave))


def promotion_pr_plan(
    manifest: ModelPromotionManifest,
    *,
    dry_run: bool,
    created_urls: dict[str, str] | None = None,
) -> tuple[ModelPromotionPrPlanEntry, ...]:
    """Build deterministic promotion PR records for the evidence bundle."""
    action = "planned" if dry_run else "created"
    urls = created_urls or {}
    return tuple(
        ModelPromotionPrPlanEntry(
            repo=entry.repo,
            wave=_repo_wave(entry.repo),
            blocked_by_waves=_blocked_by_waves(_repo_wave(entry.repo)),
            base=manifest.target_branch,
            head=manifest.source_branch,
            dev_head_sha=entry.dev_head_sha,
            main_base_sha=entry.main_base_sha,
            action=action,
            url=urls.get(entry.repo),
        )
        for entry in manifest.repos
    )


def _is_pass_status(value: object) -> bool:
    return isinstance(value, str) and value.lower() in PASS_STATUSES


def _status_reason(payload: dict[str, Any]) -> str:
    reason = payload.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    status = payload.get("status")
    return f"status={status!r}"


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        msg = f"expected JSON object in {path}"
        raise TypeError(msg)
    return payload


def classify_promotion_gates(
    manifest: ModelPromotionManifest,
    *,
    compat_audit_path: Path,
    runtime_topology_path: Path,
    cross_repo_path: Path,
    dry_run: bool,
) -> ModelPromotionGateStatus:
    """Classify observed promotion evidence into pass/blocking failure classes."""
    failures: list[ModelPromotionFailureEvidence] = []
    compat = ModelCompatDependencyAudit.model_validate(
        _load_json_object(compat_audit_path)
    )
    if compat.has_blockers:
        failures.append(
            ModelPromotionFailureEvidence(
                failure_class=EnumPromotionFailureClass.CODE,
                source=str(compat_audit_path),
                reason=(
                    f"{compat.blocker_count} production compatibility dependency "
                    "blocker(s) detected"
                ),
            )
        )

    runtime_topology = _load_json_object(runtime_topology_path)
    if not _is_pass_status(runtime_topology.get("status")):
        failures.append(
            ModelPromotionFailureEvidence(
                failure_class=EnumPromotionFailureClass.RUNTIME,
                source=str(runtime_topology_path),
                reason=_status_reason(runtime_topology),
            )
        )

    cross_repo = _load_json_object(cross_repo_path)
    cross_repo_status = cross_repo.get("status")
    if not _is_pass_status(cross_repo_status):
        failure_class = (
            EnumPromotionFailureClass.FLAKY_INFRA
            if cross_repo_status == "flaky_infra"
            else EnumPromotionFailureClass.INTEGRATION
        )
        failures.append(
            ModelPromotionFailureEvidence(
                failure_class=failure_class,
                source=str(cross_repo_path),
                reason=_status_reason(cross_repo),
            )
        )

    first_failure = failures[0].failure_class if failures else None
    verdict = (
        EnumPromotionVerdict.PLANNED
        if dry_run
        else EnumPromotionVerdict.BLOCKED
        if failures
        else EnumPromotionVerdict.PASSED
    )
    return ModelPromotionGateStatus(
        promotion_batch_id=manifest.promotion_batch_id,
        evaluated_at=datetime.now(UTC),
        dry_run=dry_run,
        verdict=verdict,
        promotable=verdict == EnumPromotionVerdict.PASSED,
        blocking_failure_class=first_failure,
        failures=tuple(failures),
    )


def make_per_repo_results(
    manifest: ModelPromotionManifest,
    *,
    dry_run: bool,
    created_urls: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return per-repository promotion results in wire-contract shape."""
    return {
        "schema_version": "1.0.0",
        "promotion_batch_id": manifest.promotion_batch_id,
        "repos": [
            entry.model_dump(mode="json")
            for entry in promotion_pr_plan(
                manifest,
                dry_run=dry_run,
                created_urls=created_urls,
            )
        ],
    }


def write_artifact_manifest(evidence_dir: Path, output: Path) -> None:
    """Hash every artifact in an evidence directory except the manifest itself."""
    artifacts: list[dict[str, str]] = []
    for path in sorted(evidence_dir.rglob("*")):
        if not path.is_file() or path.resolve() == output.resolve():
            continue
        artifacts.append(
            {
                "path": str(path.relative_to(evidence_dir)),
                "sha256": file_sha256(path),
                "created_at": datetime.now(UTC).isoformat(),
                "source": "workflow",
                "approval_level": "read_only",
            }
        )
    write_json(output, {"schema_version": "1.0.0", "artifacts": artifacts})


def _load_created_urls(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit-compat")
    audit.add_argument("--manifest", type=Path, required=True)
    audit.add_argument("--workspace", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)

    runtime = subparsers.add_parser("runtime-placeholder")
    runtime.add_argument("--manifest", type=Path, required=True)
    runtime.add_argument("--output", type=Path, required=True)
    runtime.add_argument("--reason", required=True)

    cross_repo = subparsers.add_parser("cross-repo-placeholder")
    cross_repo.add_argument("--manifest", type=Path, required=True)
    cross_repo.add_argument("--output", type=Path, required=True)
    cross_repo.add_argument("--status", default="not_run")
    cross_repo.add_argument("--reason", required=True)

    verify = subparsers.add_parser("verification")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--compat-audit", type=Path, required=True)
    verify.add_argument("--runtime-topology", type=Path, required=True)
    verify.add_argument("--cross-repo", type=Path, required=True)
    verify.add_argument("--per-repo-results", type=Path, required=True)
    verify.add_argument("--gate-status", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)
    verify.add_argument("--verifier-identity", required=True)
    verify.add_argument("--dry-run", action="store_true")
    verify.add_argument("--created-urls", type=Path)

    artifacts = subparsers.add_parser("artifact-manifest")
    artifacts.add_argument("--evidence-dir", type=Path, required=True)
    artifacts.add_argument("--output", type=Path, required=True)

    gate = subparsers.add_parser("gate-status")
    gate.add_argument("--manifest", type=Path, required=True)
    gate.add_argument("--compat-audit", type=Path, required=True)
    gate.add_argument("--runtime-topology", type=Path, required=True)
    gate.add_argument("--cross-repo", type=Path, required=True)
    gate.add_argument("--output", type=Path, required=True)
    gate.add_argument("--dry-run", action="store_true")

    per_repo = subparsers.add_parser("per-repo-results")
    per_repo.add_argument("--manifest", type=Path, required=True)
    per_repo.add_argument("--output", type=Path, required=True)
    per_repo.add_argument("--dry-run", action="store_true")
    per_repo.add_argument("--created-urls", type=Path)
    return parser.parse_args()


def main() -> int:  # noqa: PLR0911
    """CLI entrypoint for promotion workflow evidence helpers."""
    args = _parse_args()

    if args.command == "audit-compat":
        manifest = load_promotion_manifest(args.manifest)
        audit = audit_compat_dependencies(manifest, workspace=args.workspace)
        write_json(args.output, audit)
        return 1 if audit.has_blockers else 0

    if args.command == "runtime-placeholder":
        manifest = load_promotion_manifest(args.manifest)
        write_json(
            args.output,
            make_runtime_topology_placeholder(manifest=manifest, reason=args.reason),
        )
        return 0

    if args.command == "cross-repo-placeholder":
        manifest = load_promotion_manifest(args.manifest)
        write_json(
            args.output,
            make_cross_repo_placeholder(
                manifest=manifest,
                status=args.status,
                reason=args.reason,
            ),
        )
        return 0

    if args.command == "verification":
        manifest = load_promotion_manifest(args.manifest)
        gate_status = ModelPromotionGateStatus.model_validate(
            _load_json_object(args.gate_status)
        )
        per_repo_results = _load_json_object(args.per_repo_results)
        verification = ModelPromotionVerification(
            promotion_batch_id=manifest.promotion_batch_id,
            verifier_identity=args.verifier_identity,
            verified_at=datetime.now(UTC),
            dry_run=args.dry_run,
            verdict=gate_status.verdict,
            blocking_failure_class=gate_status.blocking_failure_class,
            intended_manifest_digest=file_sha256(args.manifest),
            per_repo_results_digest=file_sha256(args.per_repo_results),
            compat_dependency_audit_digest=file_sha256(args.compat_audit),
            runtime_topology_proof_digest=file_sha256(args.runtime_topology),
            cross_repo_integration_result_digest=file_sha256(args.cross_repo),
            gate_status_digest=file_sha256(args.gate_status),
            promotion_prs=tuple(
                ModelPromotionPrPlanEntry.model_validate(entry)
                for entry in per_repo_results["repos"]
            ),
        )
        write_json(args.output, verification)
        return 0

    if args.command == "artifact-manifest":
        write_artifact_manifest(args.evidence_dir, args.output)
        return 0

    if args.command == "gate-status":
        manifest = load_promotion_manifest(args.manifest)
        status = classify_promotion_gates(
            manifest,
            compat_audit_path=args.compat_audit,
            runtime_topology_path=args.runtime_topology,
            cross_repo_path=args.cross_repo,
            dry_run=args.dry_run,
        )
        write_json(args.output, status)
        return 0

    if args.command == "per-repo-results":
        manifest = load_promotion_manifest(args.manifest)
        write_json(
            args.output,
            make_per_repo_results(
                manifest,
                dry_run=args.dry_run,
                created_urls=_load_created_urls(args.created_urls),
            ),
        )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
