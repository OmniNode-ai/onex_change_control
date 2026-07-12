# SPDX-License-Identifier: MIT
"""Regression test for OMN-14434 (fan-out from OMN-14401).

Pins the current duplicate-grant_id violation count in
grants/prod_promotion_grants.yaml at zero, so a future PR that introduces a
duplicate grant_id (the field's own schema comment: "unique UUID identifying
this grant") is caught here even before the check-duplicate-registry-ids
pre-commit hook runs. Self-contained — does not import omnibase_core, so it
has no coupling to that package's version pin in this repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_GRANTS_FILE = (
    Path(__file__).parent.parent.parent / "grants" / "prod_promotion_grants.yaml"
)


def _duplicate_grant_ids(entries: list[dict[str, object]]) -> dict[str, int]:
    """Return {grant_id: count} for every grant_id appearing more than once."""
    counts: dict[str, int] = {}
    for entry in entries:
        grant_id = str(entry["grant_id"])
        counts[grant_id] = counts.get(grant_id, 0) + 1
    return {gid: count for gid, count in counts.items() if count > 1}


@pytest.mark.unit
def test_no_duplicate_grant_ids_in_live_registry() -> None:
    """grant_id must be unique across every entry — no legitimate disambiguator.

    Measured 2026-07-12: 0 violations (entries: [] at rest). This test pins
    that count so it can only ratchet down, never silently up.
    """
    data = yaml.safe_load(_GRANTS_FILE.read_text(encoding="utf-8"))
    entries = data["entries"]
    duplicates = _duplicate_grant_ids(entries)
    assert duplicates == {}, (
        f"Duplicate grant_id(s) found in {_GRANTS_FILE}: {duplicates}. "
        "grant_id must be unique — see the file's own schema comment."
    )


@pytest.mark.unit
def test_detector_actually_catches_a_duplicate() -> None:
    """Adversarial check: the detector helper must fire on a real duplicate.

    Guards against a vacuous test — proves _duplicate_grant_ids is not a
    no-op that would pass on any input.
    """
    entries: list[dict[str, object]] = [
        {"grant_id": "grant-aaaa", "image_digest": "sha256:aaa"},
        {"grant_id": "grant-aaaa", "image_digest": "sha256:bbb"},
        {"grant_id": "grant-bbbb", "image_digest": "sha256:ccc"},
    ]
    assert _duplicate_grant_ids(entries) == {"grant-aaaa": 2}
