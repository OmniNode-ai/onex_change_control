# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for validate_prod_promotion_grants (OMN-14441).

OMN-14415 found this validator runs, can fail, but cannot block a merge
(standalone workflow file, invisible to the required CI Summary rollup).
OMN-14441 folds it into the required rollup and adds two integrity checks
the schema-only version never had: duplicate grant_id detection and
diff-scoped self-approval detection. These tests are the falsifiability
proof the fix demands — each violation class must be independently
provable RED, and the "no false positive on historical entries" guard is
what makes the self-approval check safe to land unconditionally.
"""

from __future__ import annotations

import subprocess
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


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=root, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)


def _git_commit_all(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


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


class TestSelfApprovalOMN14441:
    """NEW check: approved_by must not equal the PR author, but ONLY for
    entries newly added by this PR — never for pre-existing entries, which
    would false-positive an unrelated PR from someone who happens to share
    a login with a historical approver.
    """

    def test_self_approval_on_new_entry_fails(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [])
        _git_commit_all(tmp_path, "base: empty grants")

        new_entry = _valid_entry(approved_by="mallory")
        _write_grants(grants_file, [new_entry])
        _git_commit_all(tmp_path, "head: mallory self-approves")

        result = validate_grants(grants_file, pr_author="mallory", base_ref="HEAD~1")
        assert not result.passed
        assert any("SELF-APPROVAL REJECTED" in e for e in result.errors)

    def test_different_approver_on_new_entry_passes(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [])
        _git_commit_all(tmp_path, "base: empty grants")

        new_entry = _valid_entry(approved_by="alice-lead")
        _write_grants(grants_file, [new_entry])
        _git_commit_all(tmp_path, "head: alice approves mallory's request")

        result = validate_grants(grants_file, pr_author="mallory", base_ref="HEAD~1")
        assert result.passed, result.errors

    def test_preexisting_entry_not_flagged_for_unrelated_pr(
        self, tmp_path: Path
    ) -> None:
        """The false-positive guard: an entry approved_by=mallory that
        ALREADY existed before this PR must not be flagged just because
        mallory happens to be opening some unrelated PR today.
        """
        _init_git_repo(tmp_path)
        grants_file = tmp_path / "grants.yaml"
        preexisting_entry = _valid_entry(approved_by="mallory")
        _write_grants(grants_file, [preexisting_entry])
        _git_commit_all(
            tmp_path, "base: mallory's grant, approved by someone else previously"
        )

        # HEAD == base: this PR doesn't touch the grants file at all.
        result = validate_grants(grants_file, pr_author="mallory", base_ref="HEAD")
        assert result.passed, result.errors

    def test_self_approval_skipped_without_pr_context(self, tmp_path: Path) -> None:
        """Without --pr-author/--base-ref (e.g. a push event with no PR
        context), self-approval checking is skipped entirely rather than
        guessing — confirmed here so the "skip" is a deliberate, tested
        branch, not silent.
        """
        entry = _valid_entry(approved_by="mallory")
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        result = validate_grants(grants_file)  # no pr_author, no base_ref
        assert result.passed, result.errors


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
