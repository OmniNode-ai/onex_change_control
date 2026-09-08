# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for grants/prod_promotion_grants.yaml trust anchor (OMN-13437 / OMN-13418).

DoD tests:
  - test_grant_file_codeowners_required: CODEOWNERS owns the exact path
  - test_grant_file_parses_as_valid_yaml: file is valid YAML
  - test_grant_file_entries_are_empty_or_valid_active_grants: entries: [] at rest,
    or valid active grant entries during a grant PR
  - test_schema_accepts_well_formed_entry: well-formed entry passes validation
  - test_schema_rejects_missing_required_fields: missing fields are rejected
  - test_schema_rejects_invalid_grant_id_format: bad grant_id rejected
  - test_schema_rejects_invalid_image_digest_format: bad image_digest rejected
  - test_schema_rejects_invalid_timestamp_format: bad timestamps rejected
  - test_schema_rejects_extra_fields: unexpected extra fields rejected
  - test_schema_rejects_non_list_entries: entries must be a list
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from onex_change_control.scripts.validate_prod_promotion_grants import validate_grants

# Path to the repo root relative to this test file
_REPO_ROOT = Path(__file__).parent.parent
_GRANT_FILE = _REPO_ROOT / "grants" / "prod_promotion_grants.yaml"
_CODEOWNERS_FILE = _REPO_ROOT / ".github" / "CODEOWNERS"

# Schema constants (mirroring the GHA workflow validator)
_GRANT_ID_RE = re.compile(
    r"^grant-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")
_REQUIRED_FIELDS = frozenset(
    {
        "grant_id",
        "runtime_lane",
        "image_digest",
        "promotion_batch_id",
        "approved_by",
        "expires_at",
        "created_at",
        "reason",
    }
)


def _well_formed_entry() -> dict[str, Any]:
    """Return a single well-formed grant entry for schema testing."""
    return {
        "grant_id": "grant-12345678-1234-1234-1234-123456789abc",
        "runtime_lane": "prod",
        "image_digest": "sha256:" + "a" * 64,
        "promotion_batch_id": "batch-20260621-001",
        "approved_by": "platform-lead-github-login",
        "expires_at": "2026-07-01T00:00:00Z",
        "created_at": "2026-06-21T12:00:00Z",
        "reason": "Approved production promotion for release 1.2.3",
    }


def _check_entry_fields(idx: int, entry: dict[str, Any], errors: list[str]) -> bool:
    """Check that entry has exactly the required fields. Returns True if ok."""
    present = set(entry.keys())
    missing_fields = _REQUIRED_FIELDS - present
    extra_fields = present - _REQUIRED_FIELDS
    if missing_fields:
        errors.append(
            f"Entry[{idx}]: missing required fields: {sorted(missing_fields)}"
        )
    if extra_fields:
        errors.append(f"Entry[{idx}]: unexpected fields: {sorted(extra_fields)}")
    return not (missing_fields or extra_fields)


def _parse_iso8601(ts: str) -> datetime | None:
    """Parse ISO-8601 UTC datetime string; return None on failure."""
    normalized = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _check_entry_formats(idx: int, entry: dict[str, Any], errors: list[str]) -> None:
    """Validate field formats for an entry known to have all required fields."""
    prefix = f"Entry[{idx}]"

    gid = entry["grant_id"]
    if not isinstance(gid, str) or not _GRANT_ID_RE.match(gid):
        errors.append(f"{prefix}: grant_id invalid: {gid!r}")

    digest = entry["image_digest"]
    if not isinstance(digest, str) or not _IMAGE_DIGEST_RE.match(digest):
        errors.append(f"{prefix}: image_digest invalid: {digest!r}")

    # Validate timestamp fields and enforce expires_at > created_at
    ts_parsed: dict[str, datetime | None] = {}
    for ts_field in ("expires_at", "created_at"):
        ts = entry[ts_field]
        if not isinstance(ts, str) or not _ISO8601_RE.match(ts):
            errors.append(f"{prefix}: {ts_field} invalid: {ts!r}")
            ts_parsed[ts_field] = None
        else:
            ts_parsed[ts_field] = _parse_iso8601(ts)

    created = ts_parsed.get("created_at")
    expires = ts_parsed.get("expires_at")
    if created is not None and expires is not None and expires <= created:
        errors.append(
            f"{prefix}: expires_at must be strictly after created_at "
            f"(got expires_at={entry['expires_at']!r}, "
            f"created_at={entry['created_at']!r})"
        )

    for str_field in (
        "runtime_lane",
        "promotion_batch_id",
        "approved_by",
        "reason",
    ):
        val = entry[str_field]
        if not isinstance(val, str) or not val.strip():
            errors.append(f"{prefix}: {str_field} must be non-empty string")


def _validate_entries(entries: list[dict[str, Any]]) -> list[str]:
    """Validate a list of grant entries; return list of error messages."""
    errors: list[str] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"Entry[{idx}]: must be a mapping")
            continue
        if _check_entry_fields(idx, entry, errors):
            _check_entry_formats(idx, entry, errors)
    return errors


class TestGrantFileCodeownersRequired:
    """CODEOWNERS owns the exact grants/prod_promotion_grants.yaml path."""

    def test_grant_file_codeowners_required(self) -> None:
        """CODEOWNERS must have a dedicated entry for grants/prod_promotion_grants.yaml.

        OMN-13437 DoD: test_grant_file_codeowners_required.
        """
        assert _CODEOWNERS_FILE.exists(), f"CODEOWNERS not found at {_CODEOWNERS_FILE}"
        content = _CODEOWNERS_FILE.read_text(encoding="utf-8")
        lines = content.splitlines()

        # Find non-comment lines referencing the exact grant file path
        matching_lines = [
            line
            for line in lines
            if line.strip()
            and not line.strip().startswith("#")
            and "grants/prod_promotion_grants.yaml" in line
        ]
        assert matching_lines, (
            "CODEOWNERS must have a dedicated line for "
            f"grants/prod_promotion_grants.yaml. Not found in {_CODEOWNERS_FILE}"
        )
        # Verify platform-leads is the owner
        assert any("@OmniNode-ai/platform-leads" in line for line in matching_lines), (
            "CODEOWNERS entry for grants/prod_promotion_grants.yaml must "
            "reference @OmniNode-ai/platform-leads. "
            f"Found lines: {matching_lines}"
        )

    def test_grant_codeowners_line_is_separate_from_skip_token_line(self) -> None:
        """grants/ entry must NOT share its CODEOWNERS line with allowlists/ entry."""
        assert _CODEOWNERS_FILE.exists(), f"CODEOWNERS not found at {_CODEOWNERS_FILE}"
        content = _CODEOWNERS_FILE.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            has_grant = "grants/prod_promotion_grants.yaml" in stripped
            has_skip = "allowlists/skip_token_approvals.yaml" in stripped
            assert not (has_grant and has_skip), (
                "grants/prod_promotion_grants.yaml and "
                "allowlists/skip_token_approvals.yaml must be on SEPARATE "
                f"CODEOWNERS lines. Found combined line: {line}"
            )


class TestGrantFileAtRest:
    """The grant file exists, parses, and is empty at rest."""

    def test_grant_file_exists(self) -> None:
        assert _GRANT_FILE.exists(), (
            f"grants/prod_promotion_grants.yaml not found at {_GRANT_FILE}"
        )

    def test_grant_file_parses_as_valid_yaml(self) -> None:
        content = _GRANT_FILE.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        assert isinstance(data, dict), (
            "grants/prod_promotion_grants.yaml must be a YAML mapping, "
            f"got {type(data)}"
        )

    def test_grant_file_has_entries_key(self) -> None:
        content = _GRANT_FILE.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        assert "entries" in data, (
            "grants/prod_promotion_grants.yaml must have top-level key 'entries'"
        )

    def test_grant_file_entries_are_empty_or_valid_active_grants(self) -> None:
        """At rest entries are empty; active grant PRs must validate canonically."""
        content = _GRANT_FILE.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        entries = data.get("entries")
        assert isinstance(entries, list), (
            f"'entries' must be a list, got {type(entries)}"
        )
        if entries:
            result = validate_grants(_GRANT_FILE)
            assert result.passed, result.errors


class TestGrantSchemaValidation:
    """Schema validator correctly accepts well-formed entries and rejects malformed."""

    def test_schema_accepts_empty_entries(self) -> None:
        errors = _validate_entries([])
        assert errors == [], f"Empty entries should produce no errors, got: {errors}"

    def test_schema_accepts_well_formed_entry(self) -> None:
        entry = _well_formed_entry()
        errors = _validate_entries([entry])
        assert errors == [], (
            f"Well-formed entry should produce no errors, got: {errors}"
        )

    def test_schema_rejects_missing_required_fields(self) -> None:
        """An entry missing required fields must produce errors."""
        # Only grant_id present; runtime_lane, image_digest, etc. missing
        entry: dict[str, Any] = {
            "grant_id": "grant-12345678-1234-1234-1234-123456789abc",
        }
        errors = _validate_entries([entry])
        assert errors, "Missing required fields must produce validation errors"
        combined = " ".join(errors)
        for field in ("runtime_lane", "image_digest", "approved_by"):
            assert field in combined, (
                f"Expected missing field '{field}' in errors, got: {errors}"
            )

    def test_schema_rejects_invalid_grant_id_format(self) -> None:
        entry = _well_formed_entry()
        entry["grant_id"] = "not-a-valid-grant-id"
        errors = _validate_entries([entry])
        assert errors, "Invalid grant_id must produce a validation error"
        assert any("grant_id" in err for err in errors), (
            f"Expected grant_id error, got: {errors}"
        )

    def test_schema_rejects_invalid_image_digest_format(self) -> None:
        entry = _well_formed_entry()
        entry["image_digest"] = "notadigest"
        errors = _validate_entries([entry])
        assert errors, "Invalid image_digest must produce a validation error"
        assert any("image_digest" in err for err in errors), (
            f"Expected image_digest error, got: {errors}"
        )

    def test_schema_rejects_invalid_timestamp_format(self) -> None:
        entry = _well_formed_entry()
        entry["created_at"] = "2026-06-21"  # date only, not ISO-8601 datetime
        errors = _validate_entries([entry])
        assert errors, "Invalid created_at must produce a validation error"
        assert any("created_at" in err for err in errors), (
            f"Expected created_at error, got: {errors}"
        )

    def test_schema_rejects_extra_fields(self) -> None:
        entry = _well_formed_entry()
        entry["unexpected_field"] = "should not be here"
        errors = _validate_entries([entry])
        assert errors, "Extra fields must produce a validation error"
        assert any("unexpected" in err for err in errors), (
            f"Expected extra-field error, got: {errors}"
        )

    def test_schema_rejects_empty_approved_by(self) -> None:
        entry = _well_formed_entry()
        entry["approved_by"] = "   "  # whitespace only
        errors = _validate_entries([entry])
        assert errors, "Empty/whitespace approved_by must produce a validation error"
        assert any("approved_by" in err for err in errors), (
            f"Expected approved_by error, got: {errors}"
        )

    def test_schema_rejects_non_list_entries(self) -> None:
        """entries must be a list; a mapping or scalar must be rejected."""
        # Simulate what the GHA workflow does on non-list entries
        data: dict[str, Any] = {"entries": {"grant_id": "should-be-a-list"}}
        entries_val = data.get("entries")
        # Confirm the isinstance check catches the non-list type
        assert not isinstance(entries_val, list)

    def test_schema_rejects_expires_at_not_after_created_at(self) -> None:
        """expires_at must be strictly after created_at."""
        entry = _well_formed_entry()
        # Set expires_at to same value as created_at (not strictly after)
        entry["expires_at"] = entry["created_at"]
        errors = _validate_entries([entry])
        assert errors, "expires_at == created_at must produce a validation error"
        assert any("expires_at" in err for err in errors), (
            f"Expected expires_at ordering error, got: {errors}"
        )

    def test_schema_rejects_expires_at_before_created_at(self) -> None:
        """expires_at must not be before created_at."""
        entry = _well_formed_entry()
        # Swap: expires before created
        entry["expires_at"] = "2026-06-01T00:00:00Z"
        entry["created_at"] = "2026-06-21T12:00:00Z"
        errors = _validate_entries([entry])
        assert errors, "expires_at < created_at must produce a validation error"
        assert any("expires_at" in err for err in errors), (
            f"Expected expires_at ordering error, got: {errors}"
        )

    def test_schema_accepts_expires_at_strictly_after_created_at(self) -> None:
        """A well-formed entry with expires_at after created_at is valid."""
        entry = _well_formed_entry()
        # expires_at = 2026-07-01, created_at = 2026-06-21 → valid
        assert entry["expires_at"] > entry["created_at"]
        errors = _validate_entries([entry])
        assert errors == [], (
            "expires_at strictly after created_at should produce no errors, "
            f"got: {errors}"
        )


def _write_grants(path: Path, entries: list[dict[str, Any]]) -> None:
    path.write_text(yaml.safe_dump({"entries": entries}))


def _future_entry(**overrides: Any) -> dict[str, Any]:
    """A well-formed entry whose expires_at is far in the future so the
    NO-EXPIRED-entry rule doesn't fire during real-validator tests.
    """
    entry = _well_formed_entry()
    entry["expires_at"] = "2099-01-01T00:00:00Z"
    entry.update(overrides)
    return entry


class TestDualControlRemovedOMN14814:
    """OMN-14814: the real validator (validate_grants) no longer rejects a
    grant whose approved_by equals the requester/author. With a sole
    CODEOWNER, self-approval is the only reachable state — but every other
    schema/integrity/freshness check must still fail closed.
    """

    def test_self_approved_grant_passes_real_validator(self, tmp_path: Path) -> None:
        grants_file = tmp_path / "grants.yaml"
        # approved_by == the sole CODEOWNER (would formerly be self-approval).
        _write_grants(grants_file, [_future_entry(approved_by="jonahgabriel")])
        result = validate_grants(grants_file)
        assert result.passed, result.errors

    def test_self_approved_missing_field_fails_real_validator(
        self, tmp_path: Path
    ) -> None:
        entry = _future_entry(approved_by="jonahgabriel")
        del entry["reason"]
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("missing required fields" in e for e in result.errors)

    def test_self_approved_expired_fails_real_validator(self, tmp_path: Path) -> None:
        entry = _future_entry(
            approved_by="jonahgabriel",
            expires_at="2020-01-01T00:00:00Z",
            created_at="2019-01-01T00:00:00Z",
        )
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("EXPIRED" in e for e in result.errors)

    def test_self_approved_bad_digest_fails_real_validator(
        self, tmp_path: Path
    ) -> None:
        entry = _future_entry(approved_by="jonahgabriel", image_digest="not-a-digest")
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("image_digest must match" in e for e in result.errors)

    def test_self_approved_duplicate_ids_fail_real_validator(
        self, tmp_path: Path
    ) -> None:
        gid = "grant-cccccccc-cccc-cccc-cccc-cccccccccccc"
        entry_a = _future_entry(grant_id=gid, approved_by="jonahgabriel")
        entry_b = _future_entry(grant_id=gid, approved_by="jonahgabriel")
        grants_file = tmp_path / "grants.yaml"
        _write_grants(grants_file, [entry_a, entry_b])
        result = validate_grants(grants_file)
        assert not result.passed
        assert any("duplicate grant_id" in e for e in result.errors)


# ---------------------------------------------------------------------------
# OMN-16753: revoke mode for the App-authored grant path.
#
# Operator ruling 2026-09-08, firm: revocation is the same file, the same gate
# and the same lifecycle as staging, so it goes through the same App-authored
# workflow rather than a hand-opened PR. These cases pin the three fail-closed
# invariants of the removal itself, which is the only part of that workflow
# carrying real logic.
# ---------------------------------------------------------------------------

_WORKFLOW_FILE = _REPO_ROOT / ".github" / "workflows" / "stage-prod-promotion-grant.yml"

# A header with the shape the real registry carries: prose the approver reads,
# blank comment lines, and a trailing rule. Preserving it byte-for-byte is the
# point of the textual (never round-tripped) rewrite.
_RULE = "# " + "-" * 75
_FIXTURE_HEADER = f"""{_RULE}
# PROD-PROMOTION GRANT REGISTRY
#
# Trailing whitespace, comment ordering and blank lines below are load-bearing:
# a reviewer reads this text when approving, and a YAML round-trip would
# silently discard all of it.
{_RULE}
"""


def _fixture_entry_block(grant_id: str, expires_at: str) -> str:
    return (
        f"  - grant_id: {grant_id}\n"
        "    runtime_lane: prod\n"
        "    image_digest: sha256:" + "a" * 64 + "\n"
        "    promotion_batch_id: batch-3c59f394-5a40-4b44-b8e7-ed869d24c2cb\n"
        "    approved_by: someapprover\n"
        f'    expires_at: "{expires_at}"\n'
        '    created_at: "2026-09-06T22:26:02Z"\n'
        "    reason: >-\n"
        "      A multi-line folded reason whose exact wrapping a YAML round-trip\n"
        "      would reflow.\n"
    )


def _fixture_registry(*entries: str) -> str:
    if not entries:
        return _FIXTURE_HEADER + "entries: []\n"
    return _FIXTURE_HEADER + "entries:\n" + "".join(entries)


_GID_A = "grant-80201a19-7077-4d1d-a58f-3f7760117e68"
_GID_B = "grant-3c187f1e-b4b8-4fbe-a4cb-6bca31c1b2a1"
_GID_C = "grant-11111111-2222-3333-4444-555555555555"
_FAR_FUTURE = "2099-01-01T00:00:00Z"
_LAPSED = "2020-01-01T00:00:00Z"


class TestRevokeModeRemoval:
    """src/onex_change_control/scripts/revoke_prod_promotion_grants.py."""

    def test_removes_every_named_entry_and_returns_to_rest(self) -> None:
        from onex_change_control.scripts.revoke_prod_promotion_grants import (
            revoke_entries,
        )

        current = _fixture_registry(
            _fixture_entry_block(_GID_A, _FAR_FUTURE),
            _fixture_entry_block(_GID_B, _FAR_FUTURE),
        )
        result = revoke_entries(current, [_GID_A, _GID_B])
        assert result.at_rest
        assert result.remaining_grant_ids == ()
        assert result.text.endswith("entries: []\n")
        assert _GID_A not in result.text
        assert _GID_B not in result.text
        assert yaml.safe_load(result.text) == {"entries": []}

    def test_header_bytes_are_identical_after_removal(self) -> None:
        from onex_change_control.scripts.revoke_prod_promotion_grants import (
            revoke_entries,
        )

        current = _fixture_registry(
            _fixture_entry_block(_GID_A, _FAR_FUTURE),
            _fixture_entry_block(_GID_B, _FAR_FUTURE),
        )
        result = revoke_entries(current, [_GID_A, _GID_B])
        assert result.header == _FIXTURE_HEADER
        assert current.startswith(_FIXTURE_HEADER)
        assert result.text.startswith(_FIXTURE_HEADER)
        # The removal is a pure deletion: exactly the entry lines go, and the
        # `entries:` key collapses to the at-rest form. Nothing above it moves.
        assert result.text[: len(_FIXTURE_HEADER)] == current[: len(_FIXTURE_HEADER)]

    def test_removes_only_the_named_entry_and_keeps_the_other_verbatim(self) -> None:
        from onex_change_control.scripts.revoke_prod_promotion_grants import (
            revoke_entries,
        )

        kept_block = _fixture_entry_block(_GID_C, _FAR_FUTURE)
        current = _fixture_registry(
            _fixture_entry_block(_GID_A, _FAR_FUTURE), kept_block
        )
        result = revoke_entries(current, [_GID_A])
        assert result.remaining_grant_ids == (_GID_C,)
        assert not result.at_rest
        assert result.text == _FIXTURE_HEADER + "entries:\n" + kept_block
        # The surviving entry's folded scalar is byte-identical, not re-dumped.
        assert kept_block in result.text

    def test_refuses_when_a_named_entry_is_absent(self) -> None:
        from onex_change_control.scripts.revoke_prod_promotion_grants import (
            RevocationRefusedError,
            revoke_entries,
        )

        current = _fixture_registry(_fixture_entry_block(_GID_A, _FAR_FUTURE))
        # A revocation that silently no-ops on an absent id reads exactly like one
        # that worked, so it refuses rather than removing what it can.
        with pytest.raises(RevocationRefusedError) as excinfo:
            revoke_entries(current, [_GID_A, _GID_B])
        assert _GID_B in str(excinfo.value)
        assert "NOT present" in str(excinfo.value)

    def test_refuses_when_the_registry_is_already_at_rest(self) -> None:
        from onex_change_control.scripts.revoke_prod_promotion_grants import (
            RevocationRefusedError,
            revoke_entries,
        )

        with pytest.raises(RevocationRefusedError) as excinfo:
            revoke_entries(_fixture_registry(), [_GID_A])
        assert "NOT present" in str(excinfo.value)

    def test_refuses_when_an_expired_entry_would_remain(self) -> None:
        """OMN-13424: an expired entry left at rest turns every subsequent PR to
        main red. Removing the live entries and leaving a lapsed one behind
        trades one problem for a permanently failing branch."""
        from onex_change_control.scripts.revoke_prod_promotion_grants import (
            RevocationRefusedError,
            revoke_entries,
        )

        current = _fixture_registry(
            _fixture_entry_block(_GID_A, _FAR_FUTURE),
            _fixture_entry_block(_GID_B, _LAPSED),
        )
        with pytest.raises(RevocationRefusedError) as excinfo:
            revoke_entries(current, [_GID_A])
        assert _GID_B in str(excinfo.value)
        assert "expired" in str(excinfo.value)
        assert "OMN-13424" in str(excinfo.value)

    def test_removing_the_expired_entry_too_is_permitted(self) -> None:
        """The refusal above is about what REMAINS, not about what is removed:
        revoking the lapsed entry in the same action is the correct repair."""
        from onex_change_control.scripts.revoke_prod_promotion_grants import (
            revoke_entries,
        )

        current = _fixture_registry(
            _fixture_entry_block(_GID_A, _FAR_FUTURE),
            _fixture_entry_block(_GID_B, _LAPSED),
        )
        result = revoke_entries(current, [_GID_A, _GID_B])
        assert result.at_rest

    def test_refuses_an_empty_grant_id_list(self) -> None:
        from onex_change_control.scripts.revoke_prod_promotion_grants import (
            RevocationRefusedError,
            revoke_entries,
        )

        current = _fixture_registry(_fixture_entry_block(_GID_A, _FAR_FUTURE))
        with pytest.raises(RevocationRefusedError) as excinfo:
            revoke_entries(current, [])
        assert "names no grant ids" in str(excinfo.value)

    def test_refuses_duplicate_grant_ids(self) -> None:
        from onex_change_control.scripts.revoke_prod_promotion_grants import (
            RevocationRefusedError,
            revoke_entries,
        )

        current = _fixture_registry(_fixture_entry_block(_GID_A, _FAR_FUTURE))
        with pytest.raises(RevocationRefusedError) as excinfo:
            revoke_entries(current, [_GID_A, _GID_A])
        assert "duplicate" in str(excinfo.value)

    def test_result_still_validates_against_the_real_schema_validator(
        self, tmp_path: Path
    ) -> None:
        from onex_change_control.scripts.revoke_prod_promotion_grants import (
            revoke_entries,
        )

        current = _fixture_registry(
            _fixture_entry_block(_GID_A, _FAR_FUTURE),
            _fixture_entry_block(_GID_B, _FAR_FUTURE),
        )
        result = revoke_entries(current, [_GID_A])
        out = tmp_path / "grants.yaml"
        out.write_text(result.text, encoding="utf-8")
        assert validate_grants(out).passed


class TestRevokeModeWorkflowWiring:
    """The workflow declares revoke mode and routes it through the tested script.

    These are shape assertions on the workflow YAML rather than a render: the
    render itself needs an App token and a real `main`, so what is testable
    offline is that revoke mode exists, that it does not silently accept the
    staging honesty interlock's inputs, and that the removal is delegated to the
    module the cases above exercise.
    """

    def _workflow(self) -> dict[str, Any]:
        data = yaml.safe_load(_WORKFLOW_FILE.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        return data

    def _inputs(self) -> dict[str, Any]:
        workflow = self._workflow()
        # PyYAML resolves the bare `on:` key to the boolean True, not the string
        # "on", so the trigger block is looked up under whichever key is present.
        trigger = next(
            (v for k, v in workflow.items() if k in ("on", True)),
            None,
        )
        assert isinstance(trigger, dict), "workflow declares no trigger block"
        inputs = trigger["workflow_dispatch"]["inputs"]
        assert isinstance(inputs, dict)
        return inputs

    def _steps(self) -> list[dict[str, Any]]:
        steps = self._workflow()["jobs"]["stage"]["steps"]
        assert isinstance(steps, list)
        return steps

    def test_mode_input_offers_stage_and_revoke_and_defaults_to_stage(self) -> None:
        mode = self._inputs()["mode"]
        assert mode["default"] == "stage", (
            "revoke must be opt-in; a mis-typed dispatch must not remove grants"
        )
        assert sorted(mode["options"]) == ["revoke", "stage"]

    def test_approved_by_and_expires_at_are_not_required_inputs(self) -> None:
        """They are the STAGING honesty interlock and have no revocation meaning.
        Requiredness moved into the assert step, which enforces them in stage
        mode and REFUSES them in revoke mode."""
        inputs = self._inputs()
        for name in ("approved_by", "expires_at"):
            assert inputs[name]["required"] is False, name

    def test_revoke_step_delegates_to_the_tested_removal_script(self) -> None:
        steps = self._steps()
        revoke_steps = [s for s in steps if s.get("if") == "inputs.mode == 'revoke'"]
        assert len(revoke_steps) == 1, (
            "exactly one step performs the removal, and it is revoke-mode-gated"
        )
        assert "revoke_prod_promotion_grants.py" in revoke_steps[0]["run"]

    def test_the_removal_script_is_staged_off_the_dispatch_ref(self) -> None:
        """The branch step checks the work tree out at origin/main, which is a
        deliberately divergent long-lived branch that does not carry this script.
        Invoking it from the work tree after that checkout would fail; it is staged
        to RUNNER_TEMP first, exactly like the request bundle and for the same
        reason."""
        steps = self._steps()
        bundle_step = next(s for s in steps if s.get("id") == "bundle")
        assert (
            "src/onex_change_control/scripts/revoke_prod_promotion_grants.py"
            in bundle_step["run"]
        )
        revoke_step = next(s for s in steps if s.get("if") == "inputs.mode == 'revoke'")
        assert '"${RUNNER_TEMP}/revoke_prod_promotion_grants.py"' in revoke_step["run"]
        assert (
            "python3 src/onex_change_control/scripts/revoke_prod_promotion_grants.py"
            not in revoke_step["run"]
        )

    def test_the_removal_script_exists_where_the_workflow_stages_it_from(self) -> None:
        assert (
            _REPO_ROOT
            / "src"
            / "onex_change_control"
            / "scripts"
            / "revoke_prod_promotion_grants.py"
        ).is_file()

    def test_the_add_only_render_step_is_skipped_in_revoke_mode(self) -> None:
        steps = self._steps()
        add_only = [
            s
            for s in steps
            if "entries: []" in s.get("run", "")
            and "rendered = current" in s.get("run", "")
        ]
        assert len(add_only) == 1
        assert add_only[0].get("if") == "inputs.mode != 'revoke'", (
            "the add-only splice must not run on a revocation; it refuses unless "
            "the registry is already at rest, which is exactly what a revocation "
            "is trying to restore"
        )

    def test_workflow_still_performs_no_prod_mutation(self) -> None:
        text = _WORKFLOW_FILE.read_text(encoding="utf-8")
        assert "deploy-onex-prod.yml" in text, "the no-mutation statement is present"
        assert "workflow run deploy-onex-prod" not in text
        assert "gh workflow run" not in text
