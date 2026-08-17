# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for validate_prod_promotion_grants (OMN-14441 / OMN-14814).

OMN-14415 found this validator runs, can fail, but cannot block a merge
(standalone workflow file, invisible to the required CI Summary rollup).
OMN-14441 folded it into the required rollup and added two integrity checks
the schema-only version never had: duplicate grant_id detection and
diff-scoped self-approval (approved_by != PR-author) detection.

OMN-14814 REMOVES the self-approval / dual-control check: @OmniNode-ai/
platform-leads has exactly one member (the sole CODEOWNER), so requiring a
second, different approver would wedge every prod-promotion grant forever.
These tests are the falsifiability proof — a self-approved grant with all
technical fields valid now PASSES, while every surviving schema/integrity
check (missing field, expired, duplicate id, bad digest) remains provably
RED.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from onex_change_control.scripts.validate_prod_promotion_grants import (
    main,
    validate_grants,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _write_grants(path: Path, entries: list[dict[str, object]]) -> None:
    path.write_text(yaml.safe_dump({"entries": entries}))


def _valid_entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "grant_id": "grant-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "runtime_lane": "prod",
        "image_digest": "sha256:" + "a" * 64,
        "promotion_batch_id": "batch-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "approved_by": "alice-lead",
        "expires_at": "2099-01-01T00:00:00Z",
        "created_at": "2026-01-01T00:00:00Z",
        "reason": "test grant",
    }
    base.update(overrides)
    return base


class TestSchemaChecksPortedFromRetiredWorkflow:
    """Pre-existing schema checks — ported verbatim, re-verified here so the
    port itself is proven, not just trusted.
    """

    def test_empty_entries_passes(self, tmp_path: Path) -> None:
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [])
        result = validate_grants(grants_file)
        assert result.passed
        assert result.entry_count == 0

    def test_valid_single_entry_passes(self, tmp_path: Path) -> None:
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [_valid_entry()])
        result = validate_grants(grants_file)
        assert result.passed, result.errors
        assert result.entry_count == 1

    def test_missing_required_field_fails(self, tmp_path: Path) -> None:
        entry = _valid_entry()
        del entry["reason"]
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("missing required fields" in e for e in result.errors)

    def test_expired_grant_fails(self, tmp_path: Path) -> None:
        entry = _valid_entry(
            expires_at="2020-01-01T00:00:00Z", created_at="2019-01-01T00:00:00Z"
        )
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("EXPIRED" in e for e in result.errors)

    def test_expires_before_created_fails(self, tmp_path: Path) -> None:
        entry = _valid_entry(
            expires_at="2026-01-01T00:00:00Z", created_at="2099-01-01T00:00:00Z"
        )
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("strictly after created_at" in e for e in result.errors)

    def test_malformed_grant_id_fails(self, tmp_path: Path) -> None:
        entry = _valid_entry(grant_id="not-a-valid-id")
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("grant_id must match" in e for e in result.errors)

    def test_malformed_image_digest_fails(self, tmp_path: Path) -> None:
        entry = _valid_entry(image_digest="not-a-digest")
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("image_digest must match" in e for e in result.errors)


class TestDuplicateGrantIdOMN14441:
    """NEW check: no two entries may share a grant_id."""

    def test_duplicate_grant_id_fails(self, tmp_path: Path) -> None:
        gid = "grant-cccccccc-cccc-cccc-cccc-cccccccccccc"
        entry_a = _valid_entry(
            grant_id=gid,
            promotion_batch_id="batch-11111111-1111-1111-1111-111111111111",
        )
        entry_b = _valid_entry(
            grant_id=gid,
            promotion_batch_id="batch-22222222-2222-2222-2222-222222222222",
        )
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry_a, entry_b])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("duplicate grant_id" in e for e in result.errors)

    def test_distinct_grant_ids_pass(self, tmp_path: Path) -> None:
        entry_a = _valid_entry(grant_id="grant-dddddddd-dddd-dddd-dddd-dddddddddddd")
        entry_b = _valid_entry(grant_id="grant-eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry_a, entry_b])
        result = validate_grants(grants_file)
        assert result.passed, result.errors


class TestDualControlRemovedOMN14814:
    """OMN-14814: the approved_by != PR-author (dual-control / self-approval)
    check is REMOVED. With a sole CODEOWNER, a self-approved grant is the
    only reachable state; every OTHER schema/integrity/freshness check must
    still fail closed so the grant stays un-forgeable.

    The would-be self-approver login here (``jonahgabriel``) is the sole
    CODEOWNER; formerly an approved_by equal to the requester/author was
    rejected, now it must PASS.
    """

    def test_self_approved_grant_with_valid_fields_passes(self, tmp_path: Path) -> None:
        """The regression the fix demands: a grant self-approved by the sole
        CODEOWNER, with every technical field valid, is now VALID.
        """
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [_valid_entry(approved_by="jonahgabriel")])
        result = validate_grants(grants_file)
        assert result.passed, result.errors
        assert result.entry_count == 1

    def test_self_approved_grant_missing_expires_at_still_fails(
        self, tmp_path: Path
    ) -> None:
        """Freshness is still enforced: a self-approved grant with no
        expires_at is missing a required field and must FAIL.
        """
        entry = _valid_entry(approved_by="jonahgabriel")
        del entry["expires_at"]
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("missing required fields" in e for e in result.errors)

    def test_expired_self_approved_grant_still_fails(self, tmp_path: Path) -> None:
        """The NO-EXPIRED-entry rule still holds for a self-approved grant."""
        entry = _valid_entry(
            approved_by="jonahgabriel",
            expires_at="2020-01-01T00:00:00Z",
            created_at="2019-01-01T00:00:00Z",
        )
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("EXPIRED" in e for e in result.errors)

    def test_self_approved_grant_bad_digest_still_fails(self, tmp_path: Path) -> None:
        """Digest pinning is still enforced for a self-approved grant."""
        entry = _valid_entry(approved_by="jonahgabriel", image_digest="not-a-digest")
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("image_digest must match" in e for e in result.errors)

    def test_duplicate_self_approved_grant_ids_still_fail(self, tmp_path: Path) -> None:
        """Duplicate grant_id integrity (OMN-14441) is preserved even when
        both entries are self-approved.
        """
        gid = "grant-cccccccc-cccc-cccc-cccc-cccccccccccc"
        entry_a = _valid_entry(
            grant_id=gid,
            approved_by="jonahgabriel",
            promotion_batch_id="batch-11111111-1111-1111-1111-111111111111",
        )
        entry_b = _valid_entry(
            grant_id=gid,
            approved_by="jonahgabriel",
            promotion_batch_id="batch-22222222-2222-2222-2222-222222222222",
        )
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry_a, entry_b])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("duplicate grant_id" in e for e in result.errors)


class TestCliMain:
    def test_main_exits_zero_on_valid_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [])
        rc = main(["--file", str(grants_file)])
        assert rc == 0
        assert "PASS" in capsys.readouterr().out

    def test_main_exits_one_on_invalid_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        entry = _valid_entry()
        del entry["reason"]
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        rc = main(["--file", str(grants_file)])
        assert rc == 1
        assert "FAIL" in capsys.readouterr().out
