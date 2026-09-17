# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""The non-image target kind, on the branch a grant is actually authored on (OMN-18579).

OMN-18566 added `target_kind: image | manifest` to the prod-promotion grant
schema in two places: `omninode_infra`'s dispatch-time gate, and
`onex_change_control`'s **dev** validator module
(`src/onex_change_control/scripts/validate_prod_promotion_grants.py`).

Neither reaches this branch. Grants are resolved from `@main` and are authored
by `hotfix/*` PRs that target `main` directly, and `main` carries no such
module at all — its prod-promotion grant schema lives in exactly two places:

  1. the inline validator in `.github/workflows/validate-prod-promotion-grants.yml`, and
  2. the mirror of that validator's constants in
     `tests/test_prod_promotion_grants.py`, run by the `test` job, which the
     REQUIRED `CI Summary` context declares in `needs:`.

So a `target_kind: manifest` entry was refused twice on the only branch that
matters, and the promotion the dispatch gate is now built to authorize could
not be granted. This module is the falsifier set for closing that, and it
carries one test the two-copy arrangement has never had: a drift test that
reads the workflow's own constants out of its heredoc and asserts they equal
the mirror's, so the two copies cannot silently disagree again.

Schema semantics are ported from the dev module verbatim in meaning:

  * `target_kind` is OPTIONAL and defaults to `image`. An entry that omits it
    IS an image grant, byte-for-byte — that is what keeps every grant already
    written working unchanged.
  * An `image` grant requires `image_digest` and forbids all four manifest
    fields. A `manifest` grant is the converse. The two kinds' target fields
    are MUTUALLY EXCLUSIVE: one grant authorizes one promotion.
  * `manifest_subtree` is the approved blast radius and is DECLARED, not
    derived from `manifest_path`, because a real overlay's kustomization tree
    reaches outside its own directory.
  * `manifest_path` must lie inside `manifest_subtree`, segment-wise.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import yaml

from tests.test_prod_promotion_grants import (
    _COMMON_REQUIRED_FIELDS,
    _IMAGE_REQUIRED_FIELDS,
    _MANIFEST_REQUIRED_FIELDS,
    _OPTIONAL_FIELDS,
    _VALID_TARGET_KINDS,
    _validate_entries,
    _well_formed_entry,
)

_REPO_ROOT = Path(__file__).parent.parent
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "validate-prod-promotion-grants.yml"

#: The overlay this kind exists to authorize (OMN-18493). Its only resource is
#: `../../base`, which is exactly why the subtree is declared rather than
#: derived — see the module docstring.
_REAL_OVERLAY = "k8s/data-plane/postgres/backup/overlays/public"
_REAL_SUBTREE = "k8s/data-plane/postgres/backup"


def _well_formed_manifest_entry() -> dict[str, Any]:
    """A manifest grant that must validate cleanly."""
    return {
        "grant_id": "grant-87654321-4321-4321-4321-cba987654321",
        "runtime_lane": "prod",
        "target_kind": "manifest",
        "manifest_path": _REAL_OVERLAY,
        "manifest_subtree": _REAL_SUBTREE,
        "manifest_ref": "a" * 40,
        "rendered_digest": "sha256:" + "b" * 64,
        "promotion_batch_id": "batch-20260917-001",
        "approved_by": "platform-lead-github-login",
        "expires_at": "2099-07-01T00:00:00Z",
        "created_at": "2026-09-17T12:00:00Z",
        "reason": "OMN-18493 step 3: govern the public nightly Postgres backup.",
    }


class TestManifestKindIsAccepted:
    """A well-formed manifest grant validates, and its shape rules bite."""

    def test_a_well_formed_manifest_entry_validates(self) -> None:
        errors = _validate_entries([_well_formed_manifest_entry()])
        assert errors == [], (
            f"A well-formed manifest grant must validate, got: {errors}"
        )

    def test_an_explicit_image_target_kind_validates(self) -> None:
        entry = _well_formed_entry()
        entry["target_kind"] = "image"
        errors = _validate_entries([entry])
        assert errors == [], (
            f"Spelling target_kind: image out must change nothing, got: {errors}"
        )

    def test_an_unknown_target_kind_is_refused_and_named(self) -> None:
        entry = _well_formed_manifest_entry()
        entry["target_kind"] = "helm"
        errors = _validate_entries([entry])
        assert errors, "An unknown target_kind must be refused"
        assert any("target_kind" in err for err in errors), (
            f"The refusal must name target_kind, got: {errors}"
        )

    def test_a_manifest_path_outside_its_subtree_is_refused(self) -> None:
        entry = _well_formed_manifest_entry()
        entry["manifest_path"] = "k8s/onex-prod/overlays/public"
        errors = _validate_entries([entry])
        assert errors, "A manifest_path outside manifest_subtree must be refused"
        assert any("manifest_subtree" in err for err in errors), (
            f"The refusal must name manifest_subtree, got: {errors}"
        )

    def test_the_subtree_bound_is_segment_wise_not_a_prefix_match(self) -> None:
        """`.../backup-other` starts with `.../backup` and is NOT inside it."""
        entry = _well_formed_manifest_entry()
        entry["manifest_path"] = _REAL_SUBTREE + "-other/overlays/public"
        errors = _validate_entries([entry])
        assert errors, (
            "A sibling directory sharing a name prefix must not count as inside "
            "the approved subtree"
        )

    def test_the_real_public_overlay_is_inside_its_declared_subtree(self) -> None:
        """Positive control for the two refusals above."""
        assert _REAL_OVERLAY.startswith(_REAL_SUBTREE + "/")
        errors = _validate_entries([_well_formed_manifest_entry()])
        assert errors == [], f"The real OMN-18493 overlay must pass, got: {errors}"

    def test_a_manifest_path_outside_k8s_is_refused(self) -> None:
        entry = _well_formed_manifest_entry()
        entry["manifest_path"] = "scripts/ci"
        entry["manifest_subtree"] = "scripts"
        errors = _validate_entries([entry])
        assert errors, "A grant must not be able to point outside k8s/"

    def test_a_relative_segment_in_a_manifest_path_is_refused(self) -> None:
        entry = _well_formed_manifest_entry()
        entry["manifest_path"] = "k8s/data-plane/postgres/backup/overlays/../public"
        errors = _validate_entries([entry])
        assert errors, (
            "A '..' segment lets an approved grant resolve somewhere else and "
            "must be refused"
        )

    def test_an_abbreviated_manifest_ref_is_refused(self) -> None:
        entry = _well_formed_manifest_entry()
        entry["manifest_ref"] = "a" * 12
        errors = _validate_entries([entry])
        assert errors, "An abbreviated commit sha is ambiguous and must be refused"
        assert any("manifest_ref" in err for err in errors), (
            f"The refusal must name manifest_ref, got: {errors}"
        )

    def test_a_branch_name_as_manifest_ref_is_refused(self) -> None:
        entry = _well_formed_manifest_entry()
        entry["manifest_ref"] = "dev"
        errors = _validate_entries([entry])
        assert errors, "A branch name is not a commit and must be refused"

    def test_a_malformed_rendered_digest_is_refused(self) -> None:
        entry = _well_formed_manifest_entry()
        entry["rendered_digest"] = "sha256:notahexdigest"
        errors = _validate_entries([entry])
        assert errors, "A malformed rendered_digest must be refused"
        assert any("rendered_digest" in err for err in errors), (
            f"The refusal must name rendered_digest, got: {errors}"
        )


class TestTheTwoKindsTargetFieldsAreMutuallyExclusive:
    """One grant authorizes one promotion; a grant naming both is refused."""

    def test_a_manifest_grant_carrying_an_image_digest_is_refused(self) -> None:
        entry = _well_formed_manifest_entry()
        entry["image_digest"] = "sha256:" + "a" * 64
        errors = _validate_entries([entry])
        assert errors, "A manifest grant must not carry an image_digest"
        assert any("image_digest" in err for err in errors), (
            f"The refusal must name image_digest, got: {errors}"
        )

    def test_an_image_grant_carrying_a_manifest_field_is_refused(self) -> None:
        for field, value in (
            ("manifest_path", _REAL_OVERLAY),
            ("manifest_subtree", _REAL_SUBTREE),
            ("manifest_ref", "a" * 40),
            ("rendered_digest", "sha256:" + "b" * 64),
        ):
            entry = _well_formed_entry()
            entry[field] = value
            errors = _validate_entries([entry])
            assert errors, f"An image grant must not carry {field}"
            assert any(field in err for err in errors), (
                f"The refusal must name {field}, got: {errors}"
            )

    def test_a_manifest_grant_missing_any_manifest_field_is_refused_by_name(
        self,
    ) -> None:
        for field in sorted(_MANIFEST_REQUIRED_FIELDS):
            entry = _well_formed_manifest_entry()
            del entry[field]
            errors = _validate_entries([entry])
            assert errors, f"A manifest grant missing {field} must be refused"
            assert any(field in err for err in errors), (
                f"The refusal must name the missing field {field}, got: {errors}"
            )

    def test_a_manifest_grant_missing_a_common_field_is_refused_by_name(self) -> None:
        for field in sorted(_COMMON_REQUIRED_FIELDS):
            entry = _well_formed_manifest_entry()
            del entry[field]
            errors = _validate_entries([entry])
            assert errors, f"A manifest grant missing {field} must be refused"
            assert any(field in err for err in errors), (
                f"The refusal must name the missing field {field}, got: {errors}"
            )


class TestImageEntriesAreUnchangedInMeaning:
    """The default is what keeps every grant already written working."""

    def test_an_entry_with_no_target_kind_still_validates(self) -> None:
        errors = _validate_entries([_well_formed_entry()])
        assert errors == [], (
            f"An entry with no target_kind IS an image grant, got: {errors}"
        )

    def test_an_image_entry_missing_its_digest_is_still_refused(self) -> None:
        entry = _well_formed_entry()
        del entry["image_digest"]
        errors = _validate_entries([entry])
        assert errors, "image_digest stays required for an image grant"
        assert any("image_digest" in err for err in errors), (
            f"The refusal must name image_digest, got: {errors}"
        )

    def test_a_manifest_entry_alongside_an_image_entry_changes_no_verdict(self) -> None:
        image_only = _validate_entries([_well_formed_entry()])
        together = _validate_entries(
            [_well_formed_entry(), _well_formed_manifest_entry()]
        )
        assert image_only == [], f"control failed: {image_only}"
        assert together == [], (
            f"A manifest entry in the same anchor must not change the image "
            f"entry's verdict, got: {together}"
        )

    def test_an_unrelated_extra_field_is_still_refused(self) -> None:
        """Positive control: widening the schema did not open it."""
        entry = _well_formed_manifest_entry()
        entry["unexpected_field"] = "should not be here"
        errors = _validate_entries([entry])
        assert errors, "An unknown field must still be refused"
        assert any("unexpected" in err for err in errors), (
            f"Expected an extra-field refusal, got: {errors}"
        )

    def test_the_lifecycle_markers_are_still_tolerated(self) -> None:
        """OMN-13424 `consumed*` markers are extras the anchor may carry."""
        entry = _well_formed_entry()
        entry["consumed"] = True
        entry["consumed_at"] = "2026-09-17T13:00:00Z"
        entry["consumed_by_correlation_id"] = "12345678-1234-1234-1234-123456789abc"
        errors = _validate_entries([entry])
        assert errors == [], (
            f"A consumed grant must validate before the prune job removes it, "
            f"got: {errors}"
        )


def _workflow_validator_source() -> str:
    """The inline Python the grants-validation workflow runs, as source text.

    The workflow embeds its validator as a `python - <<'PYEOF' ... PYEOF`
    heredoc. Reading it out of the parsed YAML rather than off the raw file
    means a change to the surrounding workflow shape cannot silently make this
    test read nothing.
    """
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["validate-prod-promotion-grants"]["steps"]
    bodies = [step["run"] for step in steps if "run" in step]
    for body in bodies:
        match = re.search(r"<<'PYEOF'\n(.*?)\n\s*PYEOF", body, re.DOTALL)
        if match:
            return match.group(1)
    message = (
        "No PYEOF heredoc found in validate-prod-promotion-grants.yml. The "
        "drift test below cannot pass by reading nothing."
    )
    raise AssertionError(message)


def _workflow_constant(name: str) -> set[str]:
    """One module-level set constant from the workflow's inline validator.

    Read with `ast`, never `exec`: the heredoc runs the whole validation at
    import time, so executing it here would validate the live anchor as a side
    effect of a schema test.
    """
    tree = ast.parse(_workflow_validator_source())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if name in targets:
            value = ast.literal_eval(node.value)
            assert isinstance(value, (set, frozenset)), (
                f"{name} in the workflow validator is {type(value).__name__}, not a set"
            )
            return set(value)
    message = (
        f"The workflow's inline validator declares no {name}. This branch keeps "
        "the grant schema in two copies; this test exists so they cannot "
        "disagree, and a missing constant is a disagreement."
    )
    raise AssertionError(message)


class TestTheTwoCopiesOfTheSchemaDoNotDrift:
    """`main` keeps the grant schema in the workflow AND in this test suite.

    That duplication is pre-existing and is what OMN-14441 retired on `dev` by
    moving the schema into one module. It is not retired here, so it is pinned
    instead: these tests read the workflow's own constants and assert the
    mirror equals them. Without this, the two copies could disagree with no
    signal — and they already did, which is the positive control below.
    """

    def test_common_required_fields_match(self) -> None:
        assert _workflow_constant("COMMON_REQUIRED_FIELDS") == _COMMON_REQUIRED_FIELDS

    def test_image_required_fields_match(self) -> None:
        assert _workflow_constant("IMAGE_REQUIRED_FIELDS") == _IMAGE_REQUIRED_FIELDS

    def test_manifest_required_fields_match(self) -> None:
        assert (
            _workflow_constant("MANIFEST_REQUIRED_FIELDS") == _MANIFEST_REQUIRED_FIELDS
        )

    def test_optional_fields_match(self) -> None:
        """The copies DID disagree here before OMN-18579.

        The workflow tolerated the OMN-13424 `consumed*` lifecycle markers as
        extras; the mirror in `tests/test_prod_promotion_grants.py` carried no
        optional set at all, so a consumed grant written to the anchor would
        have passed `validate-prod-promotion-grants` and failed the `test` job
        — the required `CI Summary` going red for a shape the anchor's own
        validator declares legal.
        """
        assert _workflow_constant("OPTIONAL_FIELDS") == _OPTIONAL_FIELDS

    def test_valid_target_kinds_match(self) -> None:
        assert _workflow_constant("VALID_TARGET_KINDS") == _VALID_TARGET_KINDS

    def test_the_reader_finds_a_real_heredoc(self) -> None:
        """Positive control: a zero-length read would make every test above vacuous."""
        source = _workflow_validator_source()
        assert "REQUIRED_FIELDS" in source
        assert len(source.splitlines()) > 50, (
            f"The workflow heredoc read back as {len(source.splitlines())} lines; "
            "the constants tests above would pass on an empty parse"
        )
