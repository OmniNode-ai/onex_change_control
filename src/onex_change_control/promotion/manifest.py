# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Generate and verify intended promotion manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_PROMOTION_REPOS: tuple[str, ...] = (
    "omnibase_compat",
    "omnibase_core",
    "omnibase_spi",
    "omnibase_infra",
    "omnimarket",
    "omniclaude",
    "omniintelligence",
    "omnimemory",
    "omnidash",
    "omniweb",
    "onex_change_control",
    "omninode_infra",
    "onex-self-extending-agent",
)

LOCKFILE_CANDIDATES: tuple[str, ...] = (
    "uv.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
)


class ModelPromotionRuntimeTarget(BaseModel):
    """Runtime lane/profile that must be proven before promotion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lane: str = Field(default="stability-test", min_length=1)
    profile: str = Field(default="stability-test", min_length=1)


class ModelPromotionManifestRepo(BaseModel):
    """Per-repository entry in an intended promotion manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str = Field(min_length=1)
    dev_head_sha: str = Field(min_length=7)
    main_base_sha: str = Field(min_length=7)
    package_name: str | None = None
    package_version: str | None = None
    dependency_ranges: tuple[str, ...] = Field(default_factory=tuple)
    lock_hashes: dict[str, str] = Field(default_factory=dict)


class ModelPromotionManifest(BaseModel):
    """Intended dev-to-main promotion manifest bound into OCC evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "1.0.0"
    promotion_batch_id: str = Field(min_length=1)
    generated_at: datetime
    source_branch: str = Field(default="dev", min_length=1)
    target_branch: str = Field(default="main", min_length=1)
    runtime_target: ModelPromotionRuntimeTarget
    repos: tuple[ModelPromotionManifestRepo, ...]

    @model_validator(mode="after")
    def _validate_manifest(self) -> ModelPromotionManifest:
        if self.source_branch == self.target_branch:
            msg = "promotion source and target branches must differ"
            raise ValueError(msg)
        repo_names = [entry.repo for entry in self.repos]
        if len(repo_names) != len(set(repo_names)):
            msg = "promotion manifest contains duplicate repos"
            raise ValueError(msg)
        return self

    @property
    def manifest_sha256(self) -> str:
        """Return the canonical SHA-256 digest for this manifest."""
        payload = self.model_dump(mode="json", exclude={"generated_at"})
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def to_json_bytes(self) -> bytes:
        """Serialize the manifest in stable, human-readable JSON form."""
        payload = self.model_dump(mode="json")
        payload["manifest_sha256"] = self.manifest_sha256
        return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()


def _run_git(repo_path: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo_path), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _branch_sha(repo_path: Path, branch: str) -> str:
    candidates = (f"origin/{branch}", branch)
    for candidate in candidates:
        try:
            return _run_git(repo_path, "rev-parse", candidate)
        except subprocess.CalledProcessError:
            continue
    msg = f"could not resolve {branch!r} in {repo_path}"
    raise RuntimeError(msg)


def _load_pyproject_metadata(
    repo_path: Path,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    pyproject = repo_path / "pyproject.toml"
    if not pyproject.exists():
        return None, None, ()

    data = tomllib.loads(pyproject.read_text())
    project = data.get("project", {})
    if not isinstance(project, dict):
        return None, None, ()

    dependencies = project.get("dependencies", ())
    if not isinstance(dependencies, list):
        dependencies = ()

    return (
        _optional_str(project.get("name")),
        _optional_str(project.get("version")),
        tuple(sorted(str(item) for item in dependencies)),
    )


def _load_package_json_metadata(
    repo_path: Path,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    package_json = repo_path / "package.json"
    if not package_json.exists():
        return None, None, ()

    data = json.loads(package_json.read_text())
    dependencies: list[str] = []
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        values = data.get(section, {})
        if isinstance(values, dict):
            dependencies.extend(
                f"{name}{specifier}" for name, specifier in values.items()
            )

    return (
        _optional_str(data.get("name")),
        _optional_str(data.get("version")),
        tuple(sorted(dependencies)),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _package_metadata(
    repo_path: Path,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    py_name, py_version, py_deps = _load_pyproject_metadata(repo_path)
    pkg_name, pkg_version, pkg_deps = _load_package_json_metadata(repo_path)
    return (
        py_name or pkg_name,
        py_version or pkg_version,
        tuple(sorted({*py_deps, *pkg_deps})),
    )


def _hash_lockfiles(repo_path: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for candidate in LOCKFILE_CANDIDATES:
        path = repo_path / candidate
        if path.exists():
            hashes[candidate] = (
                f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
            )
    return hashes


def _repo_entry(
    workspace: Path, repo: str, source_branch: str, target_branch: str
) -> ModelPromotionManifestRepo:
    repo_path = workspace / repo
    if not repo_path.exists():
        msg = f"repo path missing: {repo_path}"
        raise FileNotFoundError(msg)

    package_name, package_version, dependencies = _package_metadata(repo_path)
    return ModelPromotionManifestRepo(
        repo=repo,
        dev_head_sha=_branch_sha(repo_path, source_branch),
        main_base_sha=_branch_sha(repo_path, target_branch),
        package_name=package_name,
        package_version=package_version,
        dependency_ranges=dependencies,
        lock_hashes=_hash_lockfiles(repo_path),
    )


def generate_promotion_manifest(  # noqa: PLR0913
    *,
    workspace: Path,
    promotion_batch_id: str,
    runtime_target: ModelPromotionRuntimeTarget,
    repos: tuple[str, ...] = DEFAULT_PROMOTION_REPOS,
    source_branch: str = "dev",
    target_branch: str = "main",
    generated_at: datetime | None = None,
) -> ModelPromotionManifest:
    """Generate a manifest from local repository checkouts."""
    entries = tuple(
        _repo_entry(workspace, repo, source_branch, target_branch) for repo in repos
    )
    return ModelPromotionManifest(
        promotion_batch_id=promotion_batch_id,
        generated_at=generated_at or datetime.now(UTC),
        source_branch=source_branch,
        target_branch=target_branch,
        runtime_target=runtime_target,
        repos=entries,
    )


def load_promotion_manifest(path: Path) -> ModelPromotionManifest:
    """Load a manifest JSON file."""
    data: dict[str, Any] = json.loads(path.read_text())
    data.pop("manifest_sha256", None)
    return ModelPromotionManifest.model_validate(data)


def verify_promotion_manifest(
    manifest: ModelPromotionManifest,
    *,
    workspace: Path,
) -> list[str]:
    """Return mismatch descriptions between a manifest and current repo state."""
    current = generate_promotion_manifest(
        workspace=workspace,
        promotion_batch_id=manifest.promotion_batch_id,
        runtime_target=manifest.runtime_target,
        repos=tuple(entry.repo for entry in manifest.repos),
        source_branch=manifest.source_branch,
        target_branch=manifest.target_branch,
        generated_at=manifest.generated_at,
    )

    mismatches: list[str] = []
    expected_by_repo = {entry.repo: entry for entry in manifest.repos}
    for actual in current.repos:
        expected = expected_by_repo[actual.repo]
        if actual != expected:
            mismatches.append(actual.repo)
    return mismatches


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--promotion-batch-id", required=True)
    parser.add_argument("--runtime-lane", default="stability-test")
    parser.add_argument("--runtime-profile", default="stability-test")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-manifest", type=Path)
    parser.add_argument("--repo", action="append", dest="repos")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    runtime_target = ModelPromotionRuntimeTarget(
        lane=args.runtime_lane,
        profile=args.runtime_profile,
    )
    repos = tuple(args.repos or DEFAULT_PROMOTION_REPOS)

    if args.verify_manifest:
        manifest = load_promotion_manifest(args.verify_manifest)
        mismatches = verify_promotion_manifest(manifest, workspace=args.workspace)
        if mismatches:
            sys.stderr.write(f"Manifest mismatch: {', '.join(mismatches)}\n")
            return 1
        sys.stdout.write(f"Manifest verified: {manifest.manifest_sha256}\n")
        return 0

    manifest = generate_promotion_manifest(
        workspace=args.workspace,
        promotion_batch_id=args.promotion_batch_id,
        runtime_target=runtime_target,
        repos=repos,
    )
    payload = manifest.to_json_bytes()
    if args.output:
        args.output.write_bytes(payload)
    else:
        sys.stdout.write(payload.decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
