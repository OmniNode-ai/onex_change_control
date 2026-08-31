# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Unit tests for dispatch_claims.claim_store (OMN-8923)."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

import onex_change_control.dispatch_claims.claim_store as cs

if TYPE_CHECKING:
    from pathlib import Path

acquire_claim = cs.acquire_claim
is_claimed = cs.is_claimed
reap_expired_claims = cs.reap_expired_claims
release_claim = cs.release_claim


def _make_claim_data(
    blocker_id: str = "a" * 40,
    claimant: str = "agent-test",
    ttl_seconds: int = 300,
    claimed_at: str | None = None,
) -> dict[str, object]:
    if claimed_at is None:
        claimed_at = datetime.now(tz=UTC).isoformat()
    return {
        "blocker_id": blocker_id,
        "kind": "test",
        "host": "localhost",
        "resource": "test-resource",
        "claimant": claimant,
        "claimed_at": claimed_at,
        "ttl_seconds": ttl_seconds,
        "tool_name": "Agent",
    }


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import importlib

    monkeypatch.setenv("ONEX_STATE_DIR", str(tmp_path))
    # Reload module so _claims_dir() picks up new env
    importlib.reload(cs)
    return tmp_path


@pytest.mark.unit
def test_acquire_succeeds_when_no_existing_claim() -> None:
    data = _make_claim_data()
    result = acquire_claim(data)
    assert result is True


@pytest.mark.unit
def test_acquire_fails_when_live_claim_exists() -> None:
    data = _make_claim_data()
    assert acquire_claim(data) is True
    # Second attempt must fail
    assert acquire_claim(data) is False


@pytest.mark.unit
def test_acquire_succeeds_after_expiry() -> None:
    past = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    expired_data = _make_claim_data(ttl_seconds=1, claimed_at=past)
    # First write the expired claim manually so acquire can reap it
    claims_dir = cs._claims_dir()
    path = cs._claim_path("a" * 40, claims_dir)
    path.write_text(json.dumps(expired_data))
    # Now acquire with a live TTL — should succeed because existing claim is expired
    fresh_data = _make_claim_data(ttl_seconds=300)
    assert acquire_claim(fresh_data) is True
    assert is_claimed("a" * 40) is not None


@pytest.mark.unit
def test_release_by_owner_succeeds() -> None:
    data = _make_claim_data()
    acquire_claim(data)
    released = release_claim("a" * 40, "agent-test")
    assert released is True
    assert is_claimed("a" * 40) is None


@pytest.mark.unit
def test_release_by_non_owner_is_noop() -> None:
    data = _make_claim_data()
    acquire_claim(data)
    released = release_claim("a" * 40, "other-agent")
    assert released is False
    # Claim must still be present
    assert is_claimed("a" * 40) is not None


@pytest.mark.unit
def test_reap_expired_claims_removes_stale_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONEX_STATE_DIR", str(tmp_path))
    claims_dir = tmp_path / "dispatch_claims"
    claims_dir.mkdir()
    stale_id = "b" * 40
    past = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    (claims_dir / f"{stale_id}.json").write_text(
        json.dumps(
            {
                "blocker_id": stale_id,
                "claimant": "x",
                "claimed_at": past,
                "ttl_seconds": 1,
            }
        )
    )
    reaped = reap_expired_claims()
    assert stale_id in reaped
    assert not (claims_dir / f"{stale_id}.json").exists()


@pytest.mark.unit
def test_race_condition_exactly_one_winner() -> None:
    winners: list[bool] = []
    lock = threading.Lock()

    def try_acquire() -> None:
        data = _make_claim_data(blocker_id="c" * 40)
        result = acquire_claim(data)
        with lock:
            winners.append(result)

    threads = [threading.Thread(target=try_acquire) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert winners.count(True) == 1
    assert winners.count(False) == 9
