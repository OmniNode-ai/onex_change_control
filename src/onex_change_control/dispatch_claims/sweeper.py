# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Dispatch claim sweeper — reaps expired claim files (OMN-8927).

Called by the onex-dispatch-claim-sweeper systemd user timer every 5 minutes.
Also importable as a library: sweep(base_dir) for unit testing without
ONEX_STATE_DIR side effects.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

_CLAIMS_SUBDIR = "dispatch_claims"


def _is_expired(data: dict[str, object]) -> bool:
    try:
        claimed_at = datetime.fromisoformat(str(data["claimed_at"]))
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=UTC)
        ttl = int(str(data.get("ttl_seconds", 300)))
        elapsed = (datetime.now(tz=UTC) - claimed_at).total_seconds()
    except (KeyError, ValueError, TypeError):
        return True
    else:
        return elapsed >= ttl


def sweep(base_dir: Path) -> int:
    """Delete expired claim files under base_dir/dispatch_claims/.

    Returns the count of reaped files. Makes no network calls — pure filesystem.
    Raises nothing; errors per-file are silently swallowed so one bad file cannot
    prevent reaping others.
    """
    claims_dir = base_dir / _CLAIMS_SUBDIR
    if not claims_dir.is_dir():
        return 0

    reaped = 0
    for f in claims_dir.glob("*.json"):
        try:
            try:
                data: dict[str, object] = json.loads(f.read_text())
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                f.unlink(missing_ok=True)
                reaped += 1
                continue
            if _is_expired(data):
                f.unlink(missing_ok=True)
                reaped += 1
        except OSError:
            pass  # Concurrent deletion between glob and unlink is fine; skip.
    return reaped


def main() -> None:
    raw = os.environ.get("ONEX_STATE_DIR", "")
    if not raw.strip():
        msg = "ONEX_STATE_DIR is not set"
        raise RuntimeError(msg)
    reaped = sweep(Path(raw).expanduser().resolve())
    sys.stdout.write(f"dispatch-claim-sweeper: reaped {reaped} expired claim(s)\n")


if __name__ == "__main__":
    main()
