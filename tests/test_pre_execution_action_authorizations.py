# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the canonical pre-execution action-authorization registry."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from onex_change_control.scripts.validate_pre_execution_action_authorizations import (
    REQUIRED_FIELDS,
    validate_pre_execution_authorizations,
)

_NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
_CONTRACT = b"schema_version: '1.0.0'\nticket_id: OMN-17462\n"
_CONTRACT_SHA = f"sha256:{hashlib.sha256(_CONTRACT).hexdigest()}"


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "authorization_id": "action-auth-12345678-1234-1234-1234-123456789abc",
        "ticket_id": "OMN-17462",
        "contract_path": "contracts/OMN-17462.yaml",
        "contract_commit_sha": "a" * 40,
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


def _write(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    path = tmp_path / "pre_execution_action_authorizations.yaml"
    path.write_text(yaml.safe_dump({"entries": entries}), encoding="utf-8")
    return path


def _loader(commit: str, path: str) -> bytes:
    assert commit == "a" * 40
    assert path == "contracts/OMN-17462.yaml"
    return _CONTRACT


def _validate(tmp_path: Path, entries: list[dict[str, Any]]):
    return validate_pre_execution_authorizations(
        _write(tmp_path, entries), now=_NOW, contract_loader=_loader
    )


def test_committed_registry_is_empty_at_rest() -> None:
    registry = (
        Path(__file__).parent.parent
        / "grants"
        / "pre_execution_action_authorizations.yaml"
    )
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


def test_valid_authorization_passes_with_matching_contract_blob(tmp_path: Path) -> None:
    result = _validate(tmp_path, [_entry()])
    assert result.passed, result.errors


def test_contract_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    result = _validate(tmp_path, [_entry(contract_sha256="sha256:" + "e" * 64)])
    assert not result.passed
    assert any("contract_sha256 mismatch" in error for error in result.errors)


def test_contract_loader_failure_fails_closed(tmp_path: Path) -> None:
    def missing_loader(_: str, __: str) -> bytes:
        message = "unknown immutable OCC commit"
        raise ValueError(message)

    result = validate_pre_execution_authorizations(
        _write(tmp_path, [_entry()]), now=_NOW, contract_loader=missing_loader
    )
    assert not result.passed
    assert any("contract binding failed" in error for error in result.errors)


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
