# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Unit tests for dispatch_claims.sweeper (OMN-8927)."""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from onex_change_control.dispatch_claims.sweeper import sweep

if TYPE_CHECKING:
    from pathlib import Path


def _write_claim(
    claims_dir: Path, hex_id: str, claimed_at: str, ttl: int = 300
) -> Path:
    claims_dir.mkdir(parents=True, exist_ok=True)
    p = claims_dir / f"{hex_id}.json"
    p.write_text(
        json.dumps(
            {
                "blocker_id": hex_id,
                "claimant": "test-agent",
                "claimed_at": claimed_at,
                "ttl_seconds": ttl,
            }
        )
    )
    return p


@pytest.mark.unit
def test_sweep_removes_expired_claim(tmp_path: Path) -> None:
    past = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    claims_dir = tmp_path / "dispatch_claims"
    p = _write_claim(claims_dir, "a" * 40, past, ttl=1)
    count = sweep(tmp_path)
    assert count == 1
    assert not p.exists()


@pytest.mark.unit
def test_sweep_preserves_live_claim(tmp_path: Path) -> None:
    now = datetime.now(tz=UTC).isoformat()
    claims_dir = tmp_path / "dispatch_claims"
    p = _write_claim(claims_dir, "b" * 40, now, ttl=300)
    count = sweep(tmp_path)
    assert count == 0
    assert p.exists()


@pytest.mark.unit
def test_sweep_empty_dir_returns_zero(tmp_path: Path) -> None:
    (tmp_path / "dispatch_claims").mkdir()
    count = sweep(tmp_path)
    assert count == 0


@pytest.mark.unit
def test_sweep_no_network_calls_pure_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify sweep makes no network calls — only filesystem ops."""
    err_msg = "sweep() must not make network calls"

    def deny_connect(*_: object, **__: object) -> None:
        raise AssertionError(err_msg)

    monkeypatch.setattr(socket.socket, "connect", deny_connect)

    past = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    claims_dir = tmp_path / "dispatch_claims"
    _write_claim(claims_dir, "c" * 40, past, ttl=1)
    count = sweep(tmp_path)
    assert count == 1
