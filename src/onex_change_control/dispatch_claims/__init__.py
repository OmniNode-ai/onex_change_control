# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Filesystem-backed dispatch claim registry (OMN-8923).

Provides atomic claim acquisition/release using O_CREAT|O_EXCL for
kernel-level mutual exclusion. Prevents duplicate agent dispatches for
the same logical operation (cascade prevention, OMN-8921).
"""

from onex_change_control.dispatch_claims.claim_store import (
    acquire_claim,
    is_claimed,
    reap_expired_claims,
    release_claim,
)

__all__ = [
    "acquire_claim",
    "is_claimed",
    "reap_expired_claims",
    "release_claim",
]
