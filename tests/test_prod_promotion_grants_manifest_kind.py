# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""The non-image (``target_kind: manifest``) grant kind (OMN-18566).

Operator ruling 2026-09-17T10:33:11Z: non-image production changes on the
public cluster are authorized by a non-image target kind in the prod-promotion
grant, under the same anti-self-issuance controls as an image promotion --
never by a third gate and never by leaving the surface ungoverned.

These tests are the AUTHORING-TIME half. They pin the schema this repo's
trust anchor accepts. The DISPATCH-TIME half (recomputing the rendered digest,
binding the ref to the checkout, bounding the kustomization tree, and the
staging premise) lives in ``omninode_infra``; nothing here can substitute for
it, and nothing here weakens it.

Two properties are load-bearing and are each pinned by their own test:

  * an entry with no ``target_kind`` validates EXACTLY as it did before this
    change -- the default is ``image``, so every grant already written, and
    every grant written by a tool that has never heard of this field, keeps
    its meaning byte-for-byte; and
  * the two kinds' fields are mutually exclusive. A grant that names both an
    image digest and a manifest path describes two different promotions, and
    a downstream gate would have to guess which one the approver meant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from onex_change_control.scripts.validate_prod_promotion_grants import (
    SELF_GRANTED_REASON,
    validate_grants,
)

_FUTURE = "2099-01-01T00:00:00Z"
_CREATED = "2026-09-17T10:00:00Z"
_SUBTREE = "k8s/data-plane/postgres/backup"
_OVERLAY = "k8s/data-plane/postgres/backup/overlays/public"


def _image_entry(**overrides: Any) -> dict[str, Any]:
    """A well-formed IMAGE grant, in the shape written before this change."""
    entry: dict[str, Any] = {
        "grant_id": "grant-12345678-1234-1234-1234-123456789abc",
        "runtime_lane": "prod",
        "image_digest": "sha256:" + "a" * 64,
        "promotion_batch_id": "batch-20260917-001",
        "approved_by": "a-platform-lead",
        "expires_at": _FUTURE,
        "created_at": _CREATED,
        "reason": "Approved production promotion for release 1.2.3",
    }
    entry.update(overrides)
    return entry


def _manifest_entry(**overrides: Any) -> dict[str, Any]:
    """A well-formed MANIFEST grant, in the shape this ticket introduces."""
    entry: dict[str, Any] = {
        "grant_id": "grant-87654321-4321-4321-4321-cba987654321",
        "runtime_lane": "prod",
        "target_kind": "manifest",
        "manifest_path": _OVERLAY,
        "manifest_subtree": _SUBTREE,
        "manifest_ref": "b" * 40,
        "rendered_digest": "sha256:" + "c" * 64,
        "promotion_batch_id": "batch-20260917-002",
        "approved_by": "a-platform-lead",
        "expires_at": _FUTURE,
        "created_at": _CREATED,
        "reason": "Approved production apply of the public backup overlay",
    }
    entry.update(overrides)
    return entry


def _write(tmp_path: Path, *entries: dict[str, Any]) -> Path:
    path = tmp_path / "prod_promotion_grants.yaml"
    path.write_text(yaml.safe_dump({"entries": list(entries)}), encoding="utf-8")
    return path


def _errors(tmp_path: Path, *entries: dict[str, Any], **kwargs: Any) -> list[str]:
    return validate_grants(_write(tmp_path, *entries), **kwargs).errors


class TestManifestKindAccepted:
    """The new kind validates, and its own fields are shape-checked."""

    def test_a_well_formed_manifest_entry_passes(self, tmp_path: Path) -> None:
        assert _errors(tmp_path, _manifest_entry()) == []

    def test_an_explicit_image_target_kind_passes(self, tmp_path: Path) -> None:
        """Writing the default out is allowed; it must not read as an extra."""
        assert _errors(tmp_path, _image_entry(target_kind="image")) == []

    def test_an_unknown_target_kind_is_refused(self, tmp_path: Path) -> None:
        errors = _errors(tmp_path, _manifest_entry(target_kind="helm"))
        assert any("target_kind" in error for error in errors), errors

    @pytest.mark.parametrize(
        "field",
        ["manifest_path", "manifest_subtree", "manifest_ref", "rendered_digest"],
    )
    def test_each_manifest_field_is_required(self, tmp_path: Path, field: str) -> None:
        entry = _manifest_entry()
        del entry[field]
        errors = _errors(tmp_path, entry)
        assert any(field in error for error in errors), errors

    def test_manifest_ref_must_be_a_full_40_hex_sha(self, tmp_path: Path) -> None:
        """An abbreviated sha is ambiguous and a branch name is not a commit."""
        for bad in ("b" * 7, "main", "B" * 40, "b" * 41):
            errors = _errors(tmp_path, _manifest_entry(manifest_ref=bad))
            assert any("manifest_ref" in error for error in errors), (bad, errors)

    def test_rendered_digest_must_be_a_sha256_digest(self, tmp_path: Path) -> None:
        for bad in ("c" * 64, "sha256:" + "c" * 63, "sha512:" + "c" * 64):
            errors = _errors(tmp_path, _manifest_entry(rendered_digest=bad))
            assert any("rendered_digest" in error for error in errors), (bad, errors)

    @pytest.mark.parametrize(
        "bad_path",
        [
            "/k8s/data-plane",
            "k8s/data-plane/../../etc",
            "k8s/data-plane/backup/",
            "data-plane/backup",
            "",
            "k8s/data-plane/back up",
        ],
    )
    def test_manifest_path_must_be_a_bounded_repo_relative_k8s_path(
        self, tmp_path: Path, bad_path: str
    ) -> None:
        """Absolute, escaping, trailing-slash and non-``k8s/`` paths are refused.

        The field names a tree a production apply will render. A ``..`` segment
        or a leading ``/`` would let an approved grant point somewhere the
        approver did not read.
        """
        errors = _errors(
            tmp_path,
            _manifest_entry(manifest_path=bad_path, manifest_subtree=bad_path),
        )
        assert errors, bad_path

    def test_manifest_path_must_lie_inside_the_declared_subtree(
        self, tmp_path: Path
    ) -> None:
        """The subtree is the approved blast radius; the path must be in it."""
        errors = _errors(
            tmp_path,
            _manifest_entry(manifest_subtree="k8s/onex-prod"),
        )
        assert any("manifest_subtree" in error for error in errors), errors

    def test_a_sibling_prefix_is_not_inside_the_subtree(self, tmp_path: Path) -> None:
        """``k8s/data-plane/postgres/backup-other`` is not under ``.../backup``.

        A naive ``startswith`` would accept it. This is the falsifier for that
        implementation.
        """
        errors = _errors(
            tmp_path,
            _manifest_entry(
                manifest_subtree="k8s/data-plane/postgres/backup",
                manifest_path="k8s/data-plane/postgres/backup-other/overlays/public",
            ),
        )
        assert any("manifest_subtree" in error for error in errors), errors


class TestKindsAreMutuallyExclusive:
    """A grant describes ONE promotion. Mixed fields describe two."""

    def test_a_manifest_entry_may_not_carry_an_image_digest(
        self, tmp_path: Path
    ) -> None:
        errors = _errors(tmp_path, _manifest_entry(image_digest="sha256:" + "a" * 64))
        assert any("image_digest" in error for error in errors), errors

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("manifest_path", _OVERLAY),
            ("manifest_subtree", _SUBTREE),
            ("manifest_ref", "b" * 40),
            ("rendered_digest", "sha256:" + "c" * 64),
        ],
    )
    def test_an_image_entry_may_not_carry_a_manifest_field(
        self, tmp_path: Path, field: str, value: str
    ) -> None:
        errors = _errors(tmp_path, _image_entry(**{field: value}))
        assert any(field in error for error in errors), errors

    def test_a_manifest_entry_missing_image_digest_is_not_refused_for_it(
        self, tmp_path: Path
    ) -> None:
        """The pre-change validator required ``image_digest`` unconditionally.

        That is the exact coupling this change breaks: a manifest grant has no
        image, and refusing it for the absence of one would make the new kind
        unusable.
        """
        errors = _errors(tmp_path, _manifest_entry())
        assert not any("image_digest" in error for error in errors), errors


class TestImageEntriesAreByteEquivalentInMeaning:
    """The golden property: nothing already written changes verdict."""

    def test_an_entry_with_no_target_kind_still_validates(self, tmp_path: Path) -> None:
        assert _errors(tmp_path, _image_entry()) == []

    def test_an_entry_with_no_target_kind_still_requires_image_digest(
        self, tmp_path: Path
    ) -> None:
        entry = _image_entry()
        del entry["image_digest"]
        errors = _errors(tmp_path, entry)
        assert any("image_digest" in error for error in errors), errors

    def test_an_entry_with_no_target_kind_still_refuses_a_bad_digest(
        self, tmp_path: Path
    ) -> None:
        errors = _errors(tmp_path, _image_entry(image_digest="sha256:nope"))
        assert any("image_digest" in error for error in errors), errors

    def test_a_manifest_entry_alongside_an_image_entry_changes_no_image_verdict(
        self, tmp_path: Path
    ) -> None:
        """Adding the new kind to the anchor must not disturb the old one.

        The dispatch-time gate parses the WHOLE file before resolving one
        digest, so a manifest entry that made the anchor read as malformed
        would block every image promotion in the fleet.
        """
        alone = _errors(tmp_path, _image_entry())
        together = _errors(tmp_path, _image_entry(), _manifest_entry())
        assert alone == together == []

    def test_lifecycle_markers_still_apply_to_a_manifest_entry(
        self, tmp_path: Path
    ) -> None:
        """Single-use is kind-agnostic; a manifest grant is spent like any other."""
        assert (
            _errors(
                tmp_path,
                _manifest_entry(
                    consumed=True,
                    consumed_at="2026-09-17T11:00:00Z",
                    consumed_by_correlation_id="12345678-1234-4123-8123-123456789abc",
                ),
            )
            == []
        )

    def test_an_expired_manifest_entry_is_refused(self, tmp_path: Path) -> None:
        errors = _errors(tmp_path, _manifest_entry(expires_at="2020-01-01T00:00:00Z"))
        assert any("EXPIRED" in error for error in errors), errors

    def test_duplicate_grant_ids_span_the_two_kinds(self, tmp_path: Path) -> None:
        """Identity uniqueness is a property of the file, not of one kind."""
        shared = "grant-11111111-1111-4111-8111-111111111111"
        errors = _errors(
            tmp_path,
            _image_entry(grant_id=shared),
            _manifest_entry(grant_id=shared),
        )
        assert any("duplicate grant_id" in error for error in errors), errors


class TestSelfApprovalCoversTheManifestKind:
    """OMN-17157's refusal is kind-agnostic, and must stay so.

    This is the authoring half of AC7. The dispatch half is pinned in
    omninode_infra against a different identity (the deploy dispatcher), and
    neither replaces the other.
    """

    def test_a_self_approved_manifest_grant_is_refused(self, tmp_path: Path) -> None:
        errors = _errors(
            tmp_path,
            _manifest_entry(approved_by="the-requester"),
            requester="the-requester",
        )
        assert any(SELF_GRANTED_REASON in error for error in errors), errors

    def test_a_peer_approved_manifest_grant_is_accepted(self, tmp_path: Path) -> None:
        """Positive control: the check refuses self-approval, not every grant."""
        errors = _errors(
            tmp_path,
            _manifest_entry(approved_by="somebody-else"),
            requester="the-requester",
        )
        assert errors == []

    def test_the_refusal_is_casefolded_for_a_manifest_grant(
        self, tmp_path: Path
    ) -> None:
        errors = _errors(
            tmp_path,
            _manifest_entry(approved_by="The-Requester"),
            requester="the-requester",
        )
        assert any(SELF_GRANTED_REASON in error for error in errors), errors


class TestAnchorDocumentsTheKind:
    """The anchor's own header is what a human authoring a grant reads."""

    def test_the_grants_file_header_describes_the_manifest_kind(self) -> None:
        anchor = Path(__file__).parent.parent / "grants" / "prod_promotion_grants.yaml"
        text = anchor.read_text(encoding="utf-8")
        for token in (
            "target_kind",
            "manifest_path",
            "manifest_subtree",
            "manifest_ref",
            "rendered_digest",
        ):
            assert token in text, token
