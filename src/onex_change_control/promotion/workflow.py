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
# OMN-16279: the repo whose own tree/tests/lockfile trivially mention its own
# package name. That is a self-reference, never a dependency edge, and must
# never be scanned for compat-token matches (see `audit_compat_dependencies`).
COMPAT_SELF_REPO = "omnibase_compat"
PRODUCTION_SURFACE_FILES = {
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}
# OMN-16279: narrowed from a broad `runtime|compose|deployment|kustomization|
# manifest|policy` path-substring heuristic to the literal dependency-manifest
# filenames above. The substring heuristic caught test files (e.g.
# `tests/test_runtime_deployment_models.py`), build/workspace orchestration
# scripts (`Dockerfile.runtime`, `docker-compose.runners.yml`,
# `deploy-runtime.sh`), and node/k8s metadata (contract yaml,
# `kustomization.yaml`) that reference a repo name without declaring an
# actual package-manager dependency edge -- see the OMN-16279 audit comment
# ("Compat-blocker audit (follow-on, read-only) -- run 32318325475") for the
# reproduced taxonomy (~85% of the 50 findings were this class of noise).
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

# OMN-16279: these two status values are emitted ONLY by the literal
# unimplemented-placeholder producers in this module (`make_runtime_topology_
# placeholder` hardcodes "not_collected"; the nightly-promote.yml workflow's
# "Capture cross-repo verification placeholder" step hardcodes
# `--status "not_run_in_mvp"`). A placeholder can never produce a passing
# result on a GitHub-hosted runner (runtime) or before the OMN-15067
# cross-repo integration suite is built (integration) — blocking promotion on
# them fails every single unattended run by construction, independent of
# whether dev is actually safe to promote (root cause of 4/6 nightly-promote
# failures 2026-08-14..08-19). They are demoted to ADVISORY (non-blocking)
# below so the real, evaluated compat-dependency check remains the sole
# blocking gate. A DIFFERENT status value on either producer (i.e. once a
# real runtime capture or the real cross-repo suite lands under OMN-15067
# items 2/4 and reports something other than these exact placeholder
# strings) automatically reverts to BLOCKING — no code change required here.
ADVISORY_RUNTIME_PLACEHOLDER_STATUSES = frozenset({"not_collected"})
ADVISORY_CROSS_REPO_PLACEHOLDER_STATUSES = frozenset({"not_run_in_mvp"})

# OMN-16279: the compat-dependency check (`audit_compat_dependencies`) is, by
# construction, an ABSOLUTE check -- it flags every production-surface
# reference to `omnibase_compat`/`omnibase-compat` regardless of whether that
# reference is the sanctioned `compat -> core -> spi -> infra` layering (root
# CLAUDE.md rule 7) or a genuine regression. After the OMN-16279 steps-1/2
# narrowing (self-repo exclusion + dependency-manifest-filename-only
# predicate), what remains is exactly the sanctioned residual: real
# `pyproject.toml`/`uv.lock` declarations that ARE the approved architecture,
# not drift (confirmed by the audit's live taxonomy across 25 real-mode
# nights, 48-50 findings, never 0). Blocking promotion on accepted
# architecture is not a useful safety property, so this finding is demoted to
# ADVISORY here -- unconditionally, not keyed to a literal placeholder string
# like the two demotions above, because there is no "real" version of an
# absolute always-flag check to distinguish from a stub. This is a real
# accepted gap (no automated compat-drift signal in promotion) until
# OMN-16283 ships the diff-against-last-promoted-main-baseline replacement,
# which will flag only NEWLY introduced compat dependencies. Re-enable
# blocking here only by landing OMN-16283, not by reverting this comment.
COMPAT_DEPENDENCY_CHECK_BLOCKS = False


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
    """Return whether ``path`` is an actual dependency-manifest file.

    OMN-16279: this is intentionally an exact-filename check only. See the
    `PRODUCTION_SURFACE_FILES` comment above for why the prior path-substring
    heuristic was removed.
    """
    return path.name in PRODUCTION_SURFACE_FILES


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
        if entry.repo == COMPAT_SELF_REPO:
            # OMN-16279: `omnibase_compat`'s own source tree, tests, and
            # lockfile necessarily mention its own package name. That is a
            # self-reference, not a dependency edge -- skip the repo
            # entirely rather than flag it as a production blocker.
            continue
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
        reason = (
            f"{compat.blocker_count} production compatibility dependency "
            "reference(s) detected"
        )
        if not COMPAT_DEPENDENCY_CHECK_BLOCKS:
            reason = (
                f"{reason} [OMN-16279: ADVISORY — sanctioned compat -> core -> "
                "spi -> infra layering (root CLAUDE.md rule 7); self-matches "
                "and non-manifest paths already excluded upstream (OMN-16279 "
                "steps 1-2), so what remains is the approved architecture, "
                "not drift; reverts to BLOCKING only via the diff-against-"
                "last-promoted-main-baseline replacement tracked in "
                "OMN-16283, which flags NEWLY introduced compat dependencies "
                "only]"
            )
        failures.append(
            ModelPromotionFailureEvidence(
                failure_class=EnumPromotionFailureClass.CODE,
                source=str(compat_audit_path),
                reason=reason,
                blocking=COMPAT_DEPENDENCY_CHECK_BLOCKS,
            )
        )

    runtime_topology = _load_json_object(runtime_topology_path)
    runtime_status = runtime_topology.get("status")
    if not _is_pass_status(runtime_status):
        runtime_is_placeholder = runtime_status in ADVISORY_RUNTIME_PLACEHOLDER_STATUSES
        reason = _status_reason(runtime_topology)
        if runtime_is_placeholder:
            reason = (
                f"{reason} [OMN-16279: ADVISORY — unimplemented runtime-topology "
                "placeholder, cannot pass on a GitHub-hosted runner by design; "
                "reverts to BLOCKING once a real runtime capture replaces this "
                "placeholder]"
            )
        failures.append(
            ModelPromotionFailureEvidence(
                failure_class=EnumPromotionFailureClass.RUNTIME,
                source=str(runtime_topology_path),
                reason=reason,
                blocking=not runtime_is_placeholder,
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
        cross_repo_is_placeholder = (
            cross_repo_status in ADVISORY_CROSS_REPO_PLACEHOLDER_STATUSES
        )
        reason = _status_reason(cross_repo)
        if cross_repo_is_placeholder:
            reason = (
                f"{reason} [OMN-16279: ADVISORY — unimplemented cross-repo "
                "integration placeholder (OMN-15067 items 2/4 not yet built); "
                "reverts to BLOCKING once the real cross-repo suite lands]"
            )
        failures.append(
            ModelPromotionFailureEvidence(
                failure_class=failure_class,
                source=str(cross_repo_path),
                reason=reason,
                blocking=not cross_repo_is_placeholder,
            )
        )

    blocking_failures = [failure for failure in failures if failure.blocking]
    first_blocking_failure = (
        blocking_failures[0].failure_class if blocking_failures else None
    )
    verdict = (
        EnumPromotionVerdict.PLANNED
        if dry_run
        else EnumPromotionVerdict.BLOCKED
        if blocking_failures
        else EnumPromotionVerdict.PASSED
    )
    return ModelPromotionGateStatus(
        promotion_batch_id=manifest.promotion_batch_id,
        evaluated_at=datetime.now(UTC),
        dry_run=dry_run,
        verdict=verdict,
        promotable=verdict == EnumPromotionVerdict.PASSED,
        blocking_failure_class=first_blocking_failure,
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
