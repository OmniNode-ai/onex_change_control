# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for immutable pre-execution authorization snapshot validation."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import FrozenInstanceError, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

if TYPE_CHECKING:
    from collections.abc import Callable

from onex_change_control.scripts.validate_pre_execution_action_authorizations import (
    CANONICAL_REGISTRY_PATH,
    GIT_TIMEOUT_SECONDS,
    MAX_AUTHORIZATION_COUNT,
    MAX_CONTRACT_BLOB_BYTES,
    MAX_REASON_LENGTH,
    MAX_REGISTRY_BLOB_BYTES,
    REQUIRED_FIELDS,
    ModelPreExecutionAuthorizationValidationResult,
    _run_git,
    validate_pre_execution_authorizations,
)

_NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
_HEAD = "e" * 40
_TREE = "f" * 40
_REGISTRY_BLOB = "1" * 40
_CONTRACT_BLOB = "2" * 40
_CONTRACT_COMMIT = "a" * 40
_CONTRACT = b"schema_version: '1.0.0'\nticket_id: OMN-17462\n"
_CONTRACT_SHA = f"sha256:{hashlib.sha256(_CONTRACT).hexdigest()}"


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "authorization_id": "action-auth-12345678-1234-1234-1234-123456789abc",
        "ticket_id": "OMN-17462",
        "contract_path": "contracts/OMN-17462.yaml",
        "contract_commit_sha": _CONTRACT_COMMIT,
        "contract_sha256": _CONTRACT_SHA,
        "action_id": "postgres-push-lane-bootstrap",
        "source_sha": "b" * 40,
        "artifact_sha256": "sha256:" + "c" * 64,
        "target_database": "rsd_push_lanes",
        "target_schema": "push_lanes",
        "target_service": "rsd-push-lane-broker",
        "target_principal": "rsd_push_lane_broker",
        "execute_enabled": False,
        "issuer": "operator-governance",
        "nonce": "d" * 64,
        "issued_at": "2026-09-01T11:00:00Z",
        "expires_at": "2026-09-01T13:00:00Z",
        "one_time_use": True,
        "reason": "bounded Phase A execute-disabled verification",
    }
    entry.update(overrides)
    return entry


@dataclass
class _SnapshotGit:
    """Deterministic Git object store; never reads mutable worktree bytes."""

    repository_root: Path
    registry_bytes: bytes
    head_values: list[str] = field(default_factory=lambda: [_HEAD, _HEAD])
    contract_bytes: bytes = _CONTRACT
    resolvable_commits: set[str] = field(default_factory=lambda: {_CONTRACT_COMMIT})
    ancestral_commits: set[str] = field(default_factory=lambda: {_CONTRACT_COMMIT})
    registry_blob_size: int | None = None
    contract_blob_size: int | None = None
    on_registry_blob: Callable[[], None] | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(  # noqa: C901, PLR0911, PLR0912
        self, arguments: tuple[str, ...], repository_root: Path
    ) -> bytes:
        assert repository_root == self.repository_root
        self.calls.append(arguments)
        if arguments == ("rev-parse", "--show-toplevel"):
            return f"{repository_root}\n".encode()
        if arguments == ("rev-parse", "--verify", "HEAD^{commit}"):
            return f"{self.head_values.pop(0)}\n".encode()
        if arguments == ("rev-parse", "--verify", f"{_HEAD}^{{tree}}"):
            return f"{_TREE}\n".encode()
        if arguments == (
            "ls-tree",
            "-z",
            _HEAD,
            "--",
            CANONICAL_REGISTRY_PATH.as_posix(),
        ):
            return (
                f"100644 blob {_REGISTRY_BLOB}\t{CANONICAL_REGISTRY_PATH.as_posix()}\0"
            ).encode()
        if arguments == ("cat-file", "-t", _REGISTRY_BLOB):
            return b"blob\n"
        if arguments == ("cat-file", "-s", _REGISTRY_BLOB):
            size = self.registry_blob_size
            return f"{len(self.registry_bytes) if size is None else size}\n".encode()
        if arguments == ("cat-file", "blob", _REGISTRY_BLOB):
            if self.on_registry_blob is not None:
                self.on_registry_blob()
            return self.registry_bytes
        if (
            len(arguments) == 3
            and arguments[:2] == ("rev-parse", "--verify")
            and arguments[2].endswith("^{commit}")
        ):
            commit_sha = arguments[2].removesuffix("^{commit}")
            if commit_sha in self.resolvable_commits:
                return f"{commit_sha}\n".encode()
            message = f"commit object {commit_sha} does not exist"
            raise ValueError(message)
        if arguments[:2] == ("merge-base", "--is-ancestor"):
            commit_sha = arguments[2]
            if commit_sha in self.ancestral_commits and arguments[3] == _HEAD:
                return b""
            message = f"commit {commit_sha} is disconnected"
            raise ValueError(message)
        if (
            arguments
            == (
                "rev-parse",
                "--verify",
                f"{_CONTRACT_COMMIT}:contracts/OMN-17462.yaml",
            )
            and _CONTRACT_COMMIT in self.resolvable_commits
        ):
            return f"{_CONTRACT_BLOB}\n".encode()
        if arguments == ("cat-file", "-t", _CONTRACT_BLOB):
            return b"blob\n"
        if arguments == ("cat-file", "-s", _CONTRACT_BLOB):
            size = self.contract_blob_size
            return f"{len(self.contract_bytes) if size is None else size}\n".encode()
        if arguments == ("cat-file", "blob", _CONTRACT_BLOB):
            return self.contract_bytes
        message = f"unexpected Git request: {arguments!r}"
        raise ValueError(message)


def _validate(
    tmp_path: Path,
    entries: list[dict[str, Any]],
    *,
    file_path: Path | None = None,
) -> ModelPreExecutionAuthorizationValidationResult:
    repository_root = tmp_path / "occ"
    repository_root.mkdir()
    snapshot_git = _SnapshotGit(
        repository_root, yaml.safe_dump({"entries": entries}).encode()
    )
    return validate_pre_execution_authorizations(
        file_path or CANONICAL_REGISTRY_PATH,
        repository_root=repository_root,
        now=_NOW,
        git_runner=snapshot_git,
    )


def _mutate_projection_nonce(
    record: object,
) -> None:
    attribute_name = "".join(("no", "nce"))
    setattr(record, attribute_name, "e" * 64)


def test_committed_registry_is_empty_at_rest() -> None:
    registry = Path(__file__).parent.parent / CANONICAL_REGISTRY_PATH
    assert yaml.safe_load(registry.read_text(encoding="utf-8")) == {"entries": []}


def test_required_field_set_covers_the_pre_execution_binding() -> None:
    expected_fields = {
        "authorization_id",
        "ticket_id",
        "contract_path",
        "contract_commit_sha",
        "contract_sha256",
        "action_id",
        "source_sha",
        "artifact_sha256",
        "target_database",
        "target_schema",
        "target_service",
        "target_principal",
        "execute_enabled",
        "issuer",
        "nonce",
        "issued_at",
        "expires_at",
        "one_time_use",
        "reason",
    }
    assert expected_fields == REQUIRED_FIELDS


def test_valid_authorization_returns_immutable_snapshot_projection(
    tmp_path: Path,
) -> None:
    result = _validate(tmp_path, [_entry()])
    assert result.passed, result.errors
    assert result.snapshot is not None
    assert result.snapshot.head_sha == _HEAD
    assert result.snapshot.tree_sha == _TREE
    assert result.records[0].contract_blob_oid == _CONTRACT_BLOB
    assert result.records[0].nonce == "d" * 64
    with pytest.raises(FrozenInstanceError):
        _mutate_projection_nonce(result.records[0])


def test_contract_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    result = _validate(tmp_path, [_entry(contract_sha256="sha256:" + "e" * 64)])
    assert not result.passed
    assert any("contract_sha256 mismatch" in error for error in result.errors)
    assert result.records == ()


def test_ticket_and_contract_path_must_match_exactly(tmp_path: Path) -> None:
    result = _validate(tmp_path, [_entry(contract_path="contracts/OMN-99999.yaml")])
    assert not result.passed
    assert any("contract_path" in error for error in result.errors)


def test_execute_enabled_true_fails_closed(tmp_path: Path) -> None:
    result = _validate(tmp_path, [_entry(execute_enabled=True)])
    assert not result.passed
    assert any("execute_enabled" in error for error in result.errors)


def test_wildcard_target_identity_fails_closed(tmp_path: Path) -> None:
    result = _validate(tmp_path, [_entry(target_principal="*")])
    assert not result.passed
    assert any("target_principal" in error for error in result.errors)


def test_expired_authorization_fails_closed(tmp_path: Path) -> None:
    result = _validate(tmp_path, [_entry(expires_at="2026-09-01T11:00:00Z")])
    assert not result.passed
    assert any("EXPIRED" in error for error in result.errors)


def test_duplicate_nonce_fails_closed(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        [
            _entry(),
            _entry(authorization_id="action-auth-87654321-4321-4321-4321-cba987654321"),
        ],
    )
    assert not result.passed
    assert any("duplicate nonce" in error for error in result.errors)


def test_consumed_authorization_requires_durable_consumption_markers(
    tmp_path: Path,
) -> None:
    result = _validate(tmp_path, [_entry(consumed=True)])
    assert not result.passed
    assert any("consumed authorization requires" in error for error in result.errors)


def test_consumed_authorization_with_markers_passes(tmp_path: Path) -> None:
    result = _validate(
        tmp_path,
        [
            _entry(
                consumed=True,
                consumed_at="2026-09-01T11:30:00Z",
                consumed_by_correlation_id="87654321-4321-4321-4321-cba987654321",
            )
        ],
    )
    assert result.passed, result.errors


def test_registry_path_escape_is_rejected_before_git_snapshot(tmp_path: Path) -> None:
    result = _validate(tmp_path, [], file_path=Path("../outside.yaml"))
    assert not result.passed
    assert any("registry path" in error for error in result.errors)


def test_registry_symlink_path_is_rejected_before_git_snapshot(tmp_path: Path) -> None:
    repository_root = tmp_path / "occ"
    registry_dir = repository_root / "grants"
    registry_dir.mkdir(parents=True)
    outside = tmp_path / "outside.yaml"
    outside.write_text("entries: []\n", encoding="utf-8")
    alias = registry_dir / "registry-alias.yaml"
    alias.symlink_to(outside)
    result = validate_pre_execution_authorizations(
        alias,
        repository_root=repository_root,
        now=_NOW,
        git_runner=_SnapshotGit(repository_root, b"entries: []\n"),
    )
    assert not result.passed
    assert any("registry path must be exactly" in error for error in result.errors)


def test_worktree_swap_cannot_change_immutable_registry_bytes(tmp_path: Path) -> None:
    repository_root = tmp_path / "occ"
    registry_path = repository_root / CANONICAL_REGISTRY_PATH
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("entries: [malicious]\n", encoding="utf-8")
    expected_bytes = yaml.safe_dump({"entries": [_entry()]}).encode()
    snapshot_git = _SnapshotGit(repository_root, expected_bytes)

    def swap_registry() -> None:
        registry_path.write_text("entries: [swapped]\n", encoding="utf-8")

    snapshot_git.on_registry_blob = swap_registry
    result = validate_pre_execution_authorizations(
        CANONICAL_REGISTRY_PATH,
        repository_root=repository_root,
        now=_NOW,
        git_runner=snapshot_git,
    )
    assert result.passed, result.errors
    assert result.snapshot is not None
    assert result.snapshot.registry_bytes == expected_bytes
    assert registry_path.read_bytes() != result.snapshot.registry_bytes


def test_head_drift_after_contract_resolution_fails_closed(tmp_path: Path) -> None:
    repository_root = tmp_path / "occ"
    repository_root.mkdir()
    snapshot_git = _SnapshotGit(
        repository_root,
        yaml.safe_dump({"entries": [_entry()]}).encode(),
        head_values=[_HEAD, "9" * 40],
    )
    result = validate_pre_execution_authorizations(
        CANONICAL_REGISTRY_PATH,
        repository_root=repository_root,
        now=_NOW,
        git_runner=snapshot_git,
    )
    assert not result.passed
    assert result.records == ()
    assert any("HEAD changed during validation" in error for error in result.errors)


def test_contract_blob_reads_are_cached_per_immutable_commit_path(
    tmp_path: Path,
) -> None:
    second = _entry(
        authorization_id="action-auth-87654321-4321-4321-4321-cba987654321",
        nonce="e" * 64,
    )
    repository_root = tmp_path / "occ"
    repository_root.mkdir()
    snapshot_git = _SnapshotGit(
        repository_root, yaml.safe_dump({"entries": [_entry(), second]}).encode()
    )
    result = validate_pre_execution_authorizations(
        CANONICAL_REGISTRY_PATH,
        repository_root=repository_root,
        now=_NOW,
        git_runner=snapshot_git,
    )
    assert result.passed, result.errors
    assert (
        snapshot_git.calls.count(
            ("rev-parse", "--verify", f"{_CONTRACT_COMMIT}:contracts/OMN-17462.yaml")
        )
        == 1
    )
    assert (
        snapshot_git.calls.count(
            ("rev-parse", "--verify", f"{_CONTRACT_COMMIT}^{{commit}}")
        )
        == 1
    )
    assert (
        snapshot_git.calls.count(
            ("merge-base", "--is-ancestor", _CONTRACT_COMMIT, _HEAD)
        )
        == 1
    )
    assert snapshot_git.calls.count(("cat-file", "blob", _CONTRACT_BLOB)) == 1


def test_dangling_valid_hash_contract_reference_fails_before_blob_read(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "occ"
    repository_root.mkdir()
    dangling_commit = "b" * 40
    snapshot_git = _SnapshotGit(
        repository_root,
        yaml.safe_dump(
            {"entries": [_entry(contract_commit_sha=dangling_commit)]}
        ).encode(),
    )
    result = validate_pre_execution_authorizations(
        CANONICAL_REGISTRY_PATH,
        repository_root=repository_root,
        now=_NOW,
        git_runner=snapshot_git,
    )
    assert not result.passed
    assert any("does not exist" in error for error in result.errors)
    assert ("cat-file", "blob", _CONTRACT_BLOB) not in snapshot_git.calls


def test_disconnected_valid_hash_contract_commit_fails_before_blob_read(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "occ"
    repository_root.mkdir()
    snapshot_git = _SnapshotGit(
        repository_root,
        yaml.safe_dump({"entries": [_entry()]}).encode(),
        ancestral_commits=set(),
    )
    result = validate_pre_execution_authorizations(
        CANONICAL_REGISTRY_PATH,
        repository_root=repository_root,
        now=_NOW,
        git_runner=snapshot_git,
    )
    assert not result.passed
    assert any("not an ancestor" in error for error in result.errors)
    assert ("cat-file", "blob", _CONTRACT_BLOB) not in snapshot_git.calls


def test_oversized_registry_is_rejected_before_blob_content_read(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "occ"
    repository_root.mkdir()
    snapshot_git = _SnapshotGit(
        repository_root,
        b"entries: []\n",
        registry_blob_size=MAX_REGISTRY_BLOB_BYTES + 1,
    )
    result = validate_pre_execution_authorizations(
        CANONICAL_REGISTRY_PATH,
        repository_root=repository_root,
        now=_NOW,
        git_runner=snapshot_git,
    )
    assert not result.passed
    assert any(
        "registry" in error and "safety limit" in error for error in result.errors
    )
    assert ("cat-file", "blob", _REGISTRY_BLOB) not in snapshot_git.calls


def test_registry_count_and_reason_limits_precede_contract_git_reads(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "occ"
    repository_root.mkdir()
    entries = [_entry()] * (MAX_AUTHORIZATION_COUNT + 1)
    snapshot_git = _SnapshotGit(
        repository_root, yaml.safe_dump({"entries": entries}).encode()
    )
    count_result = validate_pre_execution_authorizations(
        CANONICAL_REGISTRY_PATH,
        repository_root=repository_root,
        now=_NOW,
        git_runner=snapshot_git,
    )
    assert not count_result.passed
    assert (
        "rev-parse",
        "--verify",
        f"{_CONTRACT_COMMIT}^{{commit}}",
    ) not in snapshot_git.calls

    oversized_reason = _SnapshotGit(
        repository_root,
        yaml.safe_dump(
            {"entries": [_entry(reason="x" * (MAX_REASON_LENGTH + 1))]}
        ).encode(),
    )
    reason_result = validate_pre_execution_authorizations(
        CANONICAL_REGISTRY_PATH,
        repository_root=repository_root,
        now=_NOW,
        git_runner=oversized_reason,
    )
    assert not reason_result.passed
    assert (
        "rev-parse",
        "--verify",
        f"{_CONTRACT_COMMIT}^{{commit}}",
    ) not in oversized_reason.calls


def test_oversized_contract_is_rejected_before_contract_content_read(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "occ"
    repository_root.mkdir()
    snapshot_git = _SnapshotGit(
        repository_root,
        yaml.safe_dump({"entries": [_entry()]}).encode(),
        contract_blob_size=MAX_CONTRACT_BLOB_BYTES + 1,
    )
    result = validate_pre_execution_authorizations(
        CANONICAL_REGISTRY_PATH,
        repository_root=repository_root,
        now=_NOW,
        git_runner=snapshot_git,
    )
    assert not result.passed
    assert any(
        "contract blob" in error and "safety limit" in error for error in result.errors
    )
    assert ("cat-file", "blob", _CONTRACT_BLOB) not in snapshot_git.calls


def test_git_runner_pins_repository_and_discards_hostile_git_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = tmp_path / "occ"
    repository_root.mkdir()
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT"):
        monkeypatch.setenv(variable, "/attacker-controlled")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/git")

    def run_git(
        command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        assert command == [
            "/usr/bin/git",
            "-C",
            str(repository_root),
            "rev-parse",
            "--show-toplevel",
        ]
        environment = kwargs["env"]
        assert all(not name.startswith("GIT_") for name in environment)
        return subprocess.CompletedProcess(command, 0, stdout=b"ok\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", run_git)
    assert _run_git(("rev-parse", "--show-toplevel"), repository_root) == b"ok\n"


@pytest.mark.parametrize("failure", ["timeout", "nonzero"])
def test_git_timeout_or_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    repository_root = tmp_path / "occ"
    repository_root.mkdir()
    monkeypatch.setattr(shutil, "which", lambda _: "git")
    if failure == "timeout":

        def run_timeout(*_: Any, **__: Any) -> subprocess.CompletedProcess[bytes]:
            command = "git"
            raise subprocess.TimeoutExpired(command, GIT_TIMEOUT_SECONDS)

        monkeypatch.setattr(subprocess, "run", run_timeout)
    else:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *_args, **_kwargs: subprocess.CompletedProcess(
                ["git"], 1, stdout=b"", stderr=b"injected failure"
            ),
        )
    result = validate_pre_execution_authorizations(
        CANONICAL_REGISTRY_PATH, repository_root=repository_root, now=_NOW
    )
    assert not result.passed
    assert result.records == ()
    assert any("immutable registry snapshot" in error for error in result.errors)
