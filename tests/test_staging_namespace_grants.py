# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for grants/staging_namespace_grants.yaml — the OMN-16702 staging anchor.

The 2026-08-30 operator ruling exempts the PUBLIC cluster's staging-class
namespaces (``dev``, ``onex-dev`` on ``omninode-k3s-system-public``) from the
dispatcher != approver rule. Prod RUNTIME promotion is UNCHANGED.

This anchor is DELIBERATELY a second file rather than a ``scope:`` field inside
``grants/prod_promotion_grants.yaml``: the prod schema validator rejects any
field outside ``REQUIRED_FIELDS | OPTIONAL_FIELDS``
(``validate_prod_promotion_grants._validate_entry``), and omninode_infra's
``scripts/validate_prod_promotion_grant.py`` raises ``ValueError`` -> MALFORMED
-> exit 1 for EVERY prod promotion when any entry is missing ``image_digest`` /
``promotion_batch_id``. A staging entry in the prod anchor would therefore
BRICK prod promotion. ``test_prod_anchor_rejects_a_staging_entry`` pins that
the two schemas stay non-interchangeable in both directions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from onex_change_control.scripts.validate_prod_promotion_grants import (
    validate_grants as validate_prod_grants,
)
from onex_change_control.scripts.validate_staging_namespace_grants import (
    ALLOWED_NAMESPACES,
    ALLOWED_SCOPE,
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    validate_staging_grants,
)

_REPO_ROOT = Path(__file__).parent.parent
_STAGING_GRANT_FILE = _REPO_ROOT / "grants" / "staging_namespace_grants.yaml"
_CODEOWNERS_FILE = _REPO_ROOT / ".github" / "CODEOWNERS"


def _well_formed_entry(**overrides: Any) -> dict[str, Any]:
    """A single well-formed staging-namespace grant entry."""
    created = datetime.now(UTC) - timedelta(hours=1)
    expires = datetime.now(UTC) + timedelta(hours=6)
    entry: dict[str, Any] = {
        "grant_id": "grant-12345678-1234-1234-1234-123456789abc",
        "scope": "staging-namespace",
        "runtime_lane": "staging-namespace",
        "cluster": "omninode-k3s-system-public",
        "namespaces": ["dev", "onex-dev"],
        "authorization": "in-session-operator-go",
        "operator_quote": 'Amend doctrine: in-session operator "go" counts',
        "operator_quote_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "authorization_record": "omni_home@" + "a" * 40,
        "approved_by": "jonahgabriel",
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": "OMN-16702 public-cluster staging-namespace convergence.",
    }
    entry.update(overrides)
    return entry


def _write(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    path = tmp_path / "staging_namespace_grants.yaml"
    path.write_text(yaml.safe_dump({"entries": entries}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The committed anchor
# ---------------------------------------------------------------------------


def test_staging_grant_file_exists_and_parses() -> None:
    assert _STAGING_GRANT_FILE.exists(), (
        f"{_STAGING_GRANT_FILE} must exist — it is the OMN-16702 authorization anchor"
    )
    data = yaml.safe_load(_STAGING_GRANT_FILE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert set(data.keys()) == {"entries"}
    assert isinstance(data["entries"], list)


def test_committed_staging_anchor_validates() -> None:
    result = validate_staging_grants(_STAGING_GRANT_FILE)
    assert result.passed, result.errors


def test_staging_grant_file_is_codeowned() -> None:
    codeowners = _CODEOWNERS_FILE.read_text(encoding="utf-8")
    assert "grants/staging_namespace_grants.yaml" in codeowners


def test_committed_staging_anchor_never_names_a_non_staging_namespace() -> None:
    data = yaml.safe_load(_STAGING_GRANT_FILE.read_text(encoding="utf-8"))
    for entry in data["entries"]:
        assert set(entry["namespaces"]) <= ALLOWED_NAMESPACES


# ---------------------------------------------------------------------------
# Schema — positive
# ---------------------------------------------------------------------------


def test_empty_entries_passes(tmp_path: Path) -> None:
    result = validate_staging_grants(_write(tmp_path, []))
    assert result.passed, result.errors
    assert result.entry_count == 0


def test_well_formed_entry_passes(tmp_path: Path) -> None:
    result = validate_staging_grants(_write(tmp_path, [_well_formed_entry()]))
    assert result.passed, result.errors
    assert result.entry_count == 1


def test_self_approved_entry_passes(tmp_path: Path) -> None:
    """The entire relaxation: approved_by MAY be the dispatcher.

    This is the 2026-08-30 ruling. It applies ONLY to this anchor.
    """
    entry = _well_formed_entry(approved_by="jonahgabriel")
    result = validate_staging_grants(_write(tmp_path, [entry]))
    assert result.passed, result.errors


def test_consumed_lifecycle_markers_are_tolerated(tmp_path: Path) -> None:
    entry = _well_formed_entry(
        consumed=True,
        consumed_at="2026-08-30T06:00:00Z",
        consumed_by_correlation_id="12345678-1234-1234-1234-123456789abc",
    )
    result = validate_staging_grants(_write(tmp_path, [entry]))
    assert result.passed, result.errors


# ---------------------------------------------------------------------------
# Schema — negative
# ---------------------------------------------------------------------------


def test_required_field_set_is_the_omn_16702_schema() -> None:
    assert {
        "grant_id",
        "scope",
        "runtime_lane",
        "cluster",
        "namespaces",
        "authorization",
        "operator_quote",
        "operator_quote_at",
        "authorization_record",
        "approved_by",
        "expires_at",
        "created_at",
        "reason",
    } == REQUIRED_FIELDS
    assert {
        "consumed",
        "consumed_at",
        "consumed_by_correlation_id",
    } == OPTIONAL_FIELDS
    # The staging schema promotes NO image and carries NO batch id.
    assert "image_digest" not in REQUIRED_FIELDS | OPTIONAL_FIELDS
    assert "promotion_batch_id" not in REQUIRED_FIELDS | OPTIONAL_FIELDS


@pytest.mark.parametrize("field", sorted(REQUIRED_FIELDS))
def test_missing_required_field_fails(tmp_path: Path, field: str) -> None:
    entry = _well_formed_entry()
    del entry[field]
    result = validate_staging_grants(_write(tmp_path, [entry]))
    assert not result.passed
    assert any("missing required fields" in err for err in result.errors)


def test_extra_field_fails(tmp_path: Path) -> None:
    entry = _well_formed_entry()
    entry["image_digest"] = "sha256:" + "a" * 64
    result = validate_staging_grants(_write(tmp_path, [entry]))
    assert not result.passed
    assert any("unexpected fields" in err for err in result.errors)


def test_namespaces_containing_onex_prod_fails(tmp_path: Path) -> None:
    entry = _well_formed_entry(namespaces=["onex-dev", "onex-prod"])
    result = validate_staging_grants(_write(tmp_path, [entry]))
    assert not result.passed
    assert any("onex-prod" in err for err in result.errors)


def test_empty_namespaces_fails(tmp_path: Path) -> None:
    result = validate_staging_grants(
        _write(tmp_path, [_well_formed_entry(namespaces=[])])
    )
    assert not result.passed


def test_namespaces_must_be_a_list(tmp_path: Path) -> None:
    result = validate_staging_grants(
        _write(tmp_path, [_well_formed_entry(namespaces="onex-dev")])
    )
    assert not result.passed


def test_wrong_scope_fails(tmp_path: Path) -> None:
    result = validate_staging_grants(
        _write(tmp_path, [_well_formed_entry(scope="prod")])
    )
    assert not result.passed
    assert any("scope" in err for err in result.errors)


def test_runtime_lane_prod_fails(tmp_path: Path) -> None:
    """A staging entry can never claim runtime_lane: prod.

    omninode_infra's prod resolver matches on ``runtime_lane == "prod"``. If a
    staging entry could carry that value and were ever pasted into the prod
    anchor, it could shadow the prod match key.
    """
    result = validate_staging_grants(
        _write(tmp_path, [_well_formed_entry(runtime_lane="prod")])
    )
    assert not result.passed
    assert any("runtime_lane" in err for err in result.errors)


def test_wrong_cluster_fails(tmp_path: Path) -> None:
    result = validate_staging_grants(
        _write(tmp_path, [_well_formed_entry(cluster="omninode-k3s-system-private")])
    )
    assert not result.passed
    assert any("cluster" in err for err in result.errors)


def test_wrong_authorization_fails(tmp_path: Path) -> None:
    result = validate_staging_grants(
        _write(tmp_path, [_well_formed_entry(authorization="codeowners-review")])
    )
    assert not result.passed
    assert any("authorization" in err for err in result.errors)


def test_blank_operator_quote_fails(tmp_path: Path) -> None:
    result = validate_staging_grants(
        _write(tmp_path, [_well_formed_entry(operator_quote="   ")])
    )
    assert not result.passed
    assert any("operator_quote" in err for err in result.errors)


def test_operator_quote_at_after_expires_at_fails(tmp_path: Path) -> None:
    entry = _well_formed_entry(
        operator_quote_at=(datetime.now(UTC) + timedelta(days=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )
    result = validate_staging_grants(_write(tmp_path, [entry]))
    assert not result.passed
    assert any("operator_quote_at" in err for err in result.errors)


def test_bad_authorization_record_fails(tmp_path: Path) -> None:
    result = validate_staging_grants(
        _write(tmp_path, [_well_formed_entry(authorization_record="omni_home@main")])
    )
    assert not result.passed
    assert any("authorization_record" in err for err in result.errors)


def test_bad_grant_id_fails(tmp_path: Path) -> None:
    result = validate_staging_grants(
        _write(tmp_path, [_well_formed_entry(grant_id="grant-nope")])
    )
    assert not result.passed


def test_duplicate_grant_ids_fail(tmp_path: Path) -> None:
    result = validate_staging_grants(
        _write(tmp_path, [_well_formed_entry(), _well_formed_entry()])
    )
    assert not result.passed
    assert any("duplicate grant_id" in err for err in result.errors)


def test_expired_entry_fails(tmp_path: Path) -> None:
    past = datetime.now(UTC) - timedelta(days=2)
    entry = _well_formed_entry(
        created_at=(past - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        operator_quote_at=(past - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=past.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    result = validate_staging_grants(_write(tmp_path, [entry]))
    assert not result.passed
    assert any("EXPIRED" in err for err in result.errors)


def test_expires_at_not_after_created_at_fails(tmp_path: Path) -> None:
    stamp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = validate_staging_grants(
        _write(tmp_path, [_well_formed_entry(created_at=stamp, expires_at=stamp)])
    )
    assert not result.passed
    assert any("strictly after" in err for err in result.errors)


def test_non_mapping_top_level_fails(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- not-a-mapping\n", encoding="utf-8")
    assert not validate_staging_grants(path).passed


def test_unexpected_top_level_key_fails(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("entries: []\nextra: 1\n", encoding="utf-8")
    assert not validate_staging_grants(path).passed


def test_missing_file_fails(tmp_path: Path) -> None:
    assert not validate_staging_grants(tmp_path / "absent.yaml").passed


# ---------------------------------------------------------------------------
# Cross-schema isolation (the NON-NEGOTIABLE)
# ---------------------------------------------------------------------------


def test_prod_anchor_rejects_a_staging_entry(tmp_path: Path) -> None:
    """A staging entry pasted into the prod anchor is rejected by the prod schema.

    This is the reason the staging grant lives in its own file: the prod
    validator's extra-field / missing-field rejection means a staging entry
    inside grants/prod_promotion_grants.yaml would fail prod-anchor validation
    outright rather than silently authorize anything.
    """
    path = tmp_path / "prod_promotion_grants.yaml"
    path.write_text(
        yaml.safe_dump({"entries": [_well_formed_entry()]}), encoding="utf-8"
    )
    result = validate_prod_grants(path)
    assert not result.passed
    assert any("unexpected fields" in err for err in result.errors) or any(
        "missing required fields" in err for err in result.errors
    )


def test_staging_validator_rejects_a_prod_entry(tmp_path: Path) -> None:
    """And the reverse — the two schemas are non-interchangeable both ways."""
    prod_entry = {
        "grant_id": "grant-12345678-1234-1234-1234-123456789abc",
        "runtime_lane": "prod",
        "image_digest": "sha256:" + "a" * 64,
        "promotion_batch_id": "batch-20260830-001",
        "approved_by": "jonahgabriel",
        "expires_at": "2099-01-01T00:00:00Z",
        "created_at": "2026-08-30T00:00:00Z",
        "reason": "prod promotion",
    }
    result = validate_staging_grants(_write(tmp_path, [prod_entry]))
    assert not result.passed


def test_allowed_namespace_constant_is_exactly_the_ruling() -> None:
    assert frozenset({"dev", "onex-dev"}) == ALLOWED_NAMESPACES
    assert ALLOWED_SCOPE == "staging-namespace"
