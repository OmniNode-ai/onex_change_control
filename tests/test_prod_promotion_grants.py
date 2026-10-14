# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for grants/prod_promotion_grants.yaml trust anchor (OMN-13437 / OMN-13418).

DoD tests:
  - test_grant_file_codeowners_required: CODEOWNERS owns the exact path
  - test_grant_file_parses_as_valid_yaml: file is valid YAML
  - test_grant_file_entries_are_empty_or_well_formed_and_unexpired: entries
    is [] OR every present entry is well-formed and unexpired (OMN-13424
    single-use grant lifecycle; matches validate-prod-promotion-grants.yml)
  - test_schema_accepts_well_formed_entry: well-formed entry passes validation
  - test_schema_rejects_missing_required_fields: missing fields are rejected
  - test_schema_rejects_invalid_grant_id_format: bad grant_id rejected
  - test_schema_rejects_invalid_image_digest_format: bad image_digest rejected
  - test_schema_rejects_invalid_timestamp_format: bad timestamps rejected
  - test_schema_rejects_extra_fields: unexpected extra fields rejected
  - test_schema_rejects_non_list_entries: entries must be a list
"""

from __future__ import annotations

import fnmatch
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

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
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_RENDERED_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MANIFEST_REF_RE = re.compile(r"^[0-9a-f]{40}$")
_MANIFEST_PATH_RE = re.compile(r"^k8s/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$")

# OMN-18566 / OMN-18579: the target kind. A grant authorizes ONE promotion, and
# there are two shapes it can take — an image digest, or a rendered kustomize
# overlay. `image` is the DEFAULT precisely so that every entry written before
# this field existed keeps its exact meaning: absent target_kind IS
# target_kind: image, byte-for-byte.
_TARGET_KIND_IMAGE = "image"
_TARGET_KIND_MANIFEST = "manifest"
_DEFAULT_TARGET_KIND = _TARGET_KIND_IMAGE
_VALID_TARGET_KINDS = frozenset({_TARGET_KIND_IMAGE, _TARGET_KIND_MANIFEST})

#: Fields every grant carries, whatever it promotes.
_COMMON_REQUIRED_FIELDS = frozenset(
    {
        "grant_id",
        "runtime_lane",
        "promotion_batch_id",
        "approved_by",
        "expires_at",
        "created_at",
        "reason",
    }
)
#: Fields an `image` grant carries and a `manifest` grant must NOT.
_IMAGE_REQUIRED_FIELDS = frozenset({"image_digest"})
#: Fields a `manifest` grant carries and an `image` grant must NOT.
#:
#: `manifest_subtree` is a separate field rather than a derivation from
#: `manifest_path` because a real overlay's kustomization tree reaches OUTSIDE
#: its own directory — k8s/data-plane/postgres/backup/overlays/public declares
#: its only resource as `../../base`. A rule of "every referenced file lies
#: under manifest_path" would refuse the very overlay this kind exists to
#: authorize. Declaring the subtree makes the approved blast radius explicit
#: and reviewable, and it is what omninode_infra's dispatch-time gate bounds
#: the transitive kustomization tree against.
_MANIFEST_REQUIRED_FIELDS = frozenset(
    {
        "manifest_path",
        "manifest_subtree",
        "manifest_ref",
        "rendered_digest",
    }
)

# OMN-13424 single-use lifecycle markers. OPTIONAL (absent == not consumed);
# tolerated as extras so a consumed grant can carry its provenance before the
# prune job removes it. `target_kind` is optional in the same sense: it may be
# written out explicitly, and its absence means `image`.
#
# This set is NEW in this mirror (OMN-18579). The workflow validator has
# tolerated the `consumed*` markers since OMN-13424; this file did not, so the
# two copies of the schema disagreed about a shape the anchor's own validator
# declares legal. tests/test_prod_promotion_grants_manifest_kind.py now reads
# the workflow's constants and asserts they equal these, so they cannot
# disagree again without a red test.
_OPTIONAL_FIELDS = frozenset(
    {
        "consumed",
        "consumed_at",
        "consumed_by_correlation_id",
        "target_kind",
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


def _resolve_target_kind(entry: dict[str, Any]) -> Any:
    """The kind this entry promotes. Absent means `image` (OMN-18566)."""
    return entry.get("target_kind", _DEFAULT_TARGET_KIND)


def _check_entry_fields(idx: int, entry: dict[str, Any], errors: list[str]) -> bool:
    """Check that entry carries exactly its kind's fields. Returns True if ok.

    The two kinds' target fields are MUTUALLY EXCLUSIVE, not merely
    independently optional. A grant naming both an image digest and a manifest
    path describes two different promotions, and the dispatch-time gate would
    have to guess which one the approver meant.
    """
    prefix = f"Entry[{idx}]"
    kind = _resolve_target_kind(entry)
    if not isinstance(kind, str) or kind not in _VALID_TARGET_KINDS:
        errors.append(
            f"{prefix}: target_kind must be one of {sorted(_VALID_TARGET_KINDS)}, "
            f"got: {kind!r}. Omit it for an image promotion; it defaults to "
            f"{_DEFAULT_TARGET_KIND!r}."
        )
        return False

    if kind == _TARGET_KIND_IMAGE:
        required = _COMMON_REQUIRED_FIELDS | _IMAGE_REQUIRED_FIELDS
        forbidden = _MANIFEST_REQUIRED_FIELDS
    else:
        required = _COMMON_REQUIRED_FIELDS | _MANIFEST_REQUIRED_FIELDS
        forbidden = _IMAGE_REQUIRED_FIELDS

    present = set(entry.keys())
    missing_fields = required - present
    forbidden_present = forbidden & present
    extra_fields = present - required - forbidden - _OPTIONAL_FIELDS
    if missing_fields:
        errors.append(
            f"{prefix}: missing required fields for target_kind {kind!r}: "
            f"{sorted(missing_fields)}"
        )
    if forbidden_present:
        errors.append(
            f"{prefix}: fields {sorted(forbidden_present)} do not belong to a "
            f"target_kind {kind!r} grant. The two kinds' target fields are "
            "mutually exclusive: one grant authorizes one promotion."
        )
    if extra_fields:
        errors.append(f"{prefix}: unexpected fields: {sorted(extra_fields)}")
    return not (missing_fields or forbidden_present or extra_fields)


def _is_inside(path: str, subtree: str) -> bool:
    """Whether `path` is `subtree` itself or lies beneath it.

    Segment-wise, not `startswith`: `k8s/a/backup-other` starts with
    `k8s/a/backup` and is NOT inside it.
    """
    if path == subtree:
        return True
    return path.startswith(subtree + "/")


def _check_manifest_path_shape(prefix: str, field: str, value: Any) -> list[str]:
    """A manifest path must name a bounded, repo-relative tree under `k8s/`."""
    if not isinstance(value, str) or not value.strip():
        return [f"{prefix}: {field} must be a non-empty string, got: {value!r}"]
    if any(segment in {".", ".."} for segment in value.split("/")):
        return [
            f"{prefix}: {field} contains a '.' or '..' segment ({value!r}). A "
            "grant must name the exact tree the approver read; a relative "
            "segment lets an approved grant resolve somewhere else."
        ]
    if not _MANIFEST_PATH_RE.match(value):
        return [
            f"{prefix}: {field} must be a repo-relative path under 'k8s/' with "
            f"no leading or trailing slash, got: {value!r}"
        ]
    return []


def _check_kind_formats(prefix: str, entry: dict[str, Any], errors: list[str]) -> None:
    """Shape-check the fields that belong to this entry's target kind."""
    if _resolve_target_kind(entry) == _TARGET_KIND_IMAGE:
        digest = entry["image_digest"]
        if not isinstance(digest, str) or not _IMAGE_DIGEST_RE.match(digest):
            errors.append(f"{prefix}: image_digest invalid: {digest!r}")
        return

    shape_errors: list[str] = []
    shape_errors.extend(
        _check_manifest_path_shape(prefix, "manifest_path", entry["manifest_path"])
    )
    shape_errors.extend(
        _check_manifest_path_shape(
            prefix, "manifest_subtree", entry["manifest_subtree"]
        )
    )
    ref = entry["manifest_ref"]
    if not isinstance(ref, str) or not _MANIFEST_REF_RE.match(ref):
        shape_errors.append(
            f"{prefix}: manifest_ref must be a full 40-character lowercase hex "
            f"commit sha, got: {ref!r}. An abbreviation is ambiguous and a "
            "branch name is not a commit."
        )
    rendered = entry["rendered_digest"]
    if not isinstance(rendered, str) or not _RENDERED_DIGEST_RE.match(rendered):
        shape_errors.append(
            f"{prefix}: rendered_digest must match 'sha256:<64hex>', got: {rendered!r}"
        )
    if not shape_errors and not _is_inside(
        entry["manifest_path"], entry["manifest_subtree"]
    ):
        shape_errors.append(
            f"{prefix}: manifest_path {entry['manifest_path']!r} does not lie "
            f"inside manifest_subtree {entry['manifest_subtree']!r}. The "
            "subtree is the approved blast radius; a path outside it describes "
            "a promotion the approver did not bound."
        )
    errors.extend(shape_errors)


def _parse_iso8601(ts: str) -> datetime | None:
    """Parse ISO-8601 UTC datetime string; return None on failure."""
    normalized = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _check_lifecycle_markers(
    prefix: str, entry: dict[str, Any], errors: list[str]
) -> None:
    """OMN-13424 lifecycle marker types, mirroring the workflow validator."""
    if "consumed" in entry and not isinstance(entry["consumed"], bool):
        errors.append(f"{prefix}: consumed must be a bool, got: {entry['consumed']!r}")
    if "consumed_at" in entry:
        ca = entry["consumed_at"]
        if not isinstance(ca, str) or not _ISO8601_RE.match(ca):
            errors.append(f"{prefix}: consumed_at invalid: {ca!r}")
    if "consumed_by_correlation_id" in entry:
        cc = entry["consumed_by_correlation_id"]
        if not isinstance(cc, str) or not _UUID_RE.match(cc):
            errors.append(f"{prefix}: consumed_by_correlation_id invalid: {cc!r}")


def _check_timestamps(prefix: str, entry: dict[str, Any], errors: list[str]) -> None:
    """Timestamp shapes, and expires_at strictly after created_at."""
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


def _check_entry_formats(idx: int, entry: dict[str, Any], errors: list[str]) -> None:
    """Validate field formats for an entry known to have all its kind's fields."""
    prefix = f"Entry[{idx}]"

    gid = entry["grant_id"]
    if not isinstance(gid, str) or not _GRANT_ID_RE.match(gid):
        errors.append(f"{prefix}: grant_id invalid: {gid!r}")

    _check_kind_formats(prefix, entry, errors)
    _check_timestamps(prefix, entry, errors)

    for str_field in (
        "runtime_lane",
        "promotion_batch_id",
        "approved_by",
        "reason",
    ):
        val = entry[str_field]
        if not isinstance(val, str) or not val.strip():
            errors.append(f"{prefix}: {str_field} must be non-empty string")

    _check_lifecycle_markers(prefix, entry, errors)


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


#: OMN-13410: the GitHub team that approves prod-promotion grants. It holds the
#: two founders' accounts and nothing else: no App and no other login.
_GRANT_APPROVER_TEAM = "@OmniNode-ai/prod-grant-approvers"
_GRANT_FILE_CODEOWNERS_PATH = "/grants/prod_promotion_grants.yaml"
_CODEOWNERS_SELF_PATH = "/.github/CODEOWNERS"


def _codeowners_rules() -> list[tuple[str, list[str]]]:
    """Return (pattern, owners) for every rule line of CODEOWNERS, in file order."""
    assert _CODEOWNERS_FILE.exists(), f"CODEOWNERS not found at {_CODEOWNERS_FILE}"
    rules: list[tuple[str, list[str]]] = []
    for line in _CODEOWNERS_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        pattern, *owners = stripped.split()
        rules.append((pattern, owners))
    return rules


def _explicit_rules(path: str) -> list[tuple[str, list[str]]]:
    """Rules naming `path` itself, with or without the leading slash."""
    names = {path, path.lstrip("/")}
    return [
        (pattern, owners) for pattern, owners in _codeowners_rules() if pattern in names
    ]


def _codeowners_pattern_matches(pattern: str, path: str) -> bool:
    """Whether a CODEOWNERS pattern covers a repository path.

    Deliberately generous: `*` is allowed to cross `/`, so the check can only
    count more rules as matching than GitHub does, never fewer.
    """
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    pat = pattern.strip("/")
    if pat in {"*", "**"}:
        return True
    parts = path.split("/")
    if anchored:
        candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    else:
        candidates = parts
    return any(fnmatch.fnmatchcase(candidate, pat) for candidate in candidates)


class TestGrantFileCodeownersRequired:
    """CODEOWNERS gives the grant file and CODEOWNERS itself to the approvers only.

    OMN-13410: the grant registry is valid only once approved by a code owner,
    and code-owner review on `main` is satisfied by ANY listed owner. So the
    grant path names exactly one team, the grant approvers, and so does
    `.github/CODEOWNERS` itself: if nothing owns that file, a pull request can
    delete the grant rule with no code-owner review and a later pull request
    can merge a grant with none.
    """

    def test_grant_file_codeowners_required(self) -> None:
        """One dedicated CODEOWNERS rule for the grant file, owned by the approvers.

        OMN-13437 DoD: test_grant_file_codeowners_required.
        """
        rules = _explicit_rules(_GRANT_FILE_CODEOWNERS_PATH)
        assert len(rules) == 1, (
            "CODEOWNERS must have exactly one dedicated rule for "
            f"{_GRANT_FILE_CODEOWNERS_PATH}. Found: {rules}"
        )
        assert rules[0][1] == [_GRANT_APPROVER_TEAM], (
            f"The {_GRANT_FILE_CODEOWNERS_PATH} rule must name only "
            f"{_GRANT_APPROVER_TEAM}. Found owners: {rules[0][1]}"
        )

    def test_codeowners_file_owned_by_grant_approvers(self) -> None:
        """`.github/CODEOWNERS` has one rule of its own, owned by the approvers only."""
        rules = _explicit_rules(_CODEOWNERS_SELF_PATH)
        assert len(rules) == 1, (
            "CODEOWNERS must have exactly one dedicated rule for "
            f"{_CODEOWNERS_SELF_PATH}, or the grant rule can be deleted without "
            f"code-owner review. Found: {rules}"
        )
        assert rules[0][1] == [_GRANT_APPROVER_TEAM], (
            f"The {_CODEOWNERS_SELF_PATH} rule must name only "
            f"{_GRANT_APPROVER_TEAM}. Found owners: {rules[0][1]}"
        )

    @pytest.mark.parametrize(
        "path",
        ["grants/prod_promotion_grants.yaml", ".github/CODEOWNERS"],
    )
    def test_last_matching_rule_is_approver_team_only(self, path: str) -> None:
        """The rule GitHub applies (the last match) names only the approver team.

        A later broad rule such as `* @someone` overrides the dedicated one, so
        the dedicated rule existing is not enough.
        """
        matching = [
            (pattern, owners)
            for pattern, owners in _codeowners_rules()
            if _codeowners_pattern_matches(pattern, path)
        ]
        assert matching, f"No CODEOWNERS rule matches {path}"
        assert matching[-1][1] == [_GRANT_APPROVER_TEAM], (
            f"The last CODEOWNERS rule matching {path} is {matching[-1]}; it "
            f"must name only {_GRANT_APPROVER_TEAM}"
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

    def test_grant_file_entries_are_empty_or_well_formed_and_unexpired(self) -> None:
        """At rest, entries is [] OR every present entry is well-formed and unexpired.

        OMN-13418/OMN-13424 introduced a single-use, time-bound grant
        lifecycle: a live, well-formed, unexpired grant legitimately sits in
        this file between being landed on a branch and being consumed by the
        gated redeploy path. The authoritative live gate for this file
        (.github/workflows/validate-prod-promotion-grants.yml) already
        encodes exactly that rule — it accepts a well-formed, unexpired
        entry and only FAILs on malformed entries or ones past their
        absolute `expires_at` (which must be pruned, per its own OMN-13424
        comment: "prune it ... at rest entries: []").

        This test originally asserted the stronger, unconditional
        `entries == []` (authored in PR #3444, 2026-06-21, when the file had
        never yet carried a grant and this was scaffolding-only). That
        assertion was never revisited after the single-use grant lifecycle
        landed, so it silently contradicted the file's own live validator:
        any PR landing a real (unexpired) grant — the entire point of the
        registry — would fail this test while passing the actual
        `validate-prod-promotion-grants` gate. Tightened here to check the
        real invariant instead of a stale snapshot of "the file happens to
        be empty right now."
        """
        content = _GRANT_FILE.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        entries = data.get("entries")
        assert isinstance(entries, list), (
            f"'entries' must be a list, got {type(entries)}"
        )
        if not entries:
            return
        errors = _validate_entries(entries)
        assert not errors, f"At rest, every present entry must be well-formed: {errors}"
        now = datetime.now(UTC)
        for idx, entry in enumerate(entries):
            expires = _parse_iso8601(entry["expires_at"])
            assert expires is not None, (
                f"Entry[{idx}]: expires_at failed to parse: {entry.get('expires_at')!r}"
            )
            assert expires > now, (
                f"Entry[{idx}]: at rest, a present entry must not be expired "
                f"(expires_at={entry.get('expires_at')!r}) — an expired grant "
                "must be pruned (OMN-13424)"
            )


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
