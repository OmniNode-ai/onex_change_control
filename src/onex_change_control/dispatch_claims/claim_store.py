# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Atomic filesystem dispatch claim store (OMN-8923).

Claim files live at $ONEX_STATE_DIR/dispatch_claims/<blocker_hash>.json.
Acquisition uses os.open(O_CREAT|O_EXCL) for kernel-level atomicity — two
concurrent writers cannot both succeed. All operations perform lazy TTL reaping
before acting so expired claims are never observed as live.

Design constraints:
  - No database, no Redis — hooks run as shell subprocesses with no shared state
  - O_CREAT|O_EXCL is atomic on POSIX filesystems (local or NFS with lockd)
  - Claim files are JSON; content is omnibase_core.models.dispatch.ModelDispatchClaim
  - On any unexpected error, operations fail open (do not block) except acquire,
    which propagates the error to let the caller decide
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

_CLAIMS_SUBDIR = "dispatch_claims"
_BLOCKER_HASH_RE = re.compile(r"^[0-9a-f]{40}$")


def _claims_dir() -> Path:
    raw = os.environ.get("ONEX_STATE_DIR", "")
    if not raw.strip():
        msg = "ONEX_STATE_DIR is not set — dispatch claim store requires it"
        raise RuntimeError(msg)
    p = Path(raw).expanduser().resolve() / _CLAIMS_SUBDIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _claim_path(blocker_hash: str, claims_dir: Path | None = None) -> Path:
    if not _BLOCKER_HASH_RE.fullmatch(blocker_hash):
        msg = "blocker_hash must be a 40-character lowercase hex SHA1"
        raise ValueError(msg)
    d = claims_dir if claims_dir is not None else _claims_dir()
    return d / f"{blocker_hash}.json"


def _is_expired(claim_data: dict[str, object]) -> bool:
    """Return True if the claim's TTL has elapsed."""
    try:
        claimed_at = datetime.fromisoformat(str(claim_data["claimed_at"]))
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=UTC)
        ttl = int(cast("int", claim_data.get("ttl_seconds", 300)))
        elapsed = (datetime.now(tz=UTC) - claimed_at).total_seconds()
    except (KeyError, ValueError, TypeError):
        return True
    else:
        return elapsed >= ttl


def reap_expired_claims() -> list[str]:
    """Delete all expired claim files. Returns list of reaped blocker_hashes."""
    reaped: list[str] = []
    try:
        d = _claims_dir()
    except RuntimeError:
        return reaped
    for f in d.glob("*.json"):
        try:
            data: dict[str, object] = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            # Unparseable file can never represent a live claim; remove it.
            try:
                f.unlink(missing_ok=True)
                reaped.append(f.stem)
            except OSError:
                pass  # Concurrent deletion is fine; skip this file.
            continue
        try:
            if _is_expired(data):
                f.unlink(missing_ok=True)
                reaped.append(f.stem)
        except OSError:
            pass  # Concurrent deletion is fine; skip this file.
    return reaped


def is_claimed(blocker_hash: str) -> dict[str, object] | None:
    """Return claim data dict if a live (non-expired) claim exists, else None.

    Performs lazy reaping: expired claim files are deleted before returning None.
    """
    try:
        p = _claim_path(blocker_hash)
        if not p.exists():
            return None
        try:
            data: dict[str, object] = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            # Cannot determine live/expired status — treat as not claimed.
            # Do not delete: the file may be an in-progress write; reap_expired_claims
            # handles corrupt file removal before the next acquire attempt.
            return None
        if _is_expired(data):
            p.unlink(missing_ok=True)
            return None
    except (OSError, ValueError):
        return None
    else:
        return data


def acquire_claim(claim_data: dict[str, object]) -> bool:
    """Atomically acquire a claim. Returns True on success, False if already claimed.

    Uses os.open(O_CREAT|O_EXCL) for kernel-level mutual exclusion.
    Performs lazy reaping before attempting acquisition.

    Args:
        claim_data: Dict representation of ModelDispatchClaim
            (must include blocker_id, claimant, claimed_at, ttl_seconds)

    Returns:
        True if the claim was successfully acquired.
        False if a live claim already exists (another agent holds the lock).

    Raises:
        RuntimeError: If ONEX_STATE_DIR is unset.
        KeyError: If claim_data is missing required fields.
    """
    blocker_hash = str(claim_data["blocker_id"])
    reap_expired_claims()

    p = _claim_path(blocker_hash)
    payload = json.dumps(claim_data, default=str).encode()

    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # Another agent holds the claim — check if it's expired
        existing = is_claimed(str(blocker_hash))
        if existing is None:
            # Was expired and reaped by is_claimed — retry once
            try:
                fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                return False
        else:
            return False

    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return True


def release_claim(blocker_hash: str, claimant: str) -> bool:
    """Release a claim. Only the owning claimant may release.

    Returns True if the file was deleted, False on mismatch or missing file.
    Claimant mismatch is a no-op (not an error).
    """
    try:
        p = _claim_path(blocker_hash)
        if not p.exists():
            return False
        try:
            data: dict[str, object] = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        if data.get("claimant") != claimant:
            return False
        p.unlink(missing_ok=True)
    except (OSError, ValueError):
        return False
    else:
        return True
