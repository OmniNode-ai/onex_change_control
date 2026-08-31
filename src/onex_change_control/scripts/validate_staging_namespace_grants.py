# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Validate the staging-namespace authorization anchor (OMN-16702).

`grants/staging_namespace_grants.yaml` is the authorization anchor for
mutations of the PUBLIC k3s cluster's staging-class namespaces (`dev`,
`onex-dev` on `omninode-k3s-system-public`). It exists because of the
2026-08-30 operator ruling: for those namespaces a fresh, timestamped,
explicit in-session operator "go" satisfies the approval requirement, so the
dispatcher != approver rule is relaxed there — and ONLY there.

PROD RUNTIME PROMOTION IS UNCHANGED. This module and the file it validates
have no authority over `onex-prod` / `omnibase-infra-prod`. Two independent
structural reasons, both machine-checkable:

  1. `omninode_infra`'s `scripts/validate_prod_promotion_grant.py` reads
     `grants/prod_promotion_grants.yaml` and only that path (its module-level
     `GRANT_FILE_PATH`). It never reads this file, and that script is
     byte-unchanged by OMN-16702.
  2. Every entry here is pinned to `runtime_lane: staging-namespace`, while
     the prod resolver matches on `runtime_lane == "prod"`. A staging entry is
     invisible to the prod resolver even if pasted into the prod anchor.

Why a SECOND file instead of a `scope:` field in the prod anchor: the prod
schema validator (`validate_prod_promotion_grants._validate_entry`) rejects
any per-entry field outside its `REQUIRED_FIELDS | OPTIONAL_FIELDS`, and
`omninode_infra`'s gate raises `ValueError` -> MALFORMED -> exit 1 for EVERY
prod promotion when an entry lacks `image_digest` / `promotion_batch_id`. A
staging-scoped entry inside the prod anchor would therefore BRICK prod
promotion — the opposite of the non-negotiable this design serves.

Checks enforced:
    - File parses as YAML; exactly one top-level key, `entries` (a list).
    - Each entry carries exactly REQUIRED_FIELDS, plus optionally the
      OMN-13424-style `consumed*` lifecycle markers. No other field.
    - `grant_id` matches `grant-<uuid4>`; no two entries share one.
    - `scope == "staging-namespace"`, `runtime_lane == "staging-namespace"`,
      `cluster == "omninode-k3s-system-public"`,
      `authorization == "in-session-operator-go"` — each a single accepted
      value, so a widened grant cannot be expressed at all.
    - `namespaces` is a non-empty list whose members all lie in
      {dev, onex-dev}. This is the "cannot reach onex-prod" clause.
    - `operator_quote` is a non-empty string (the verbatim in-session "go").
    - `operator_quote_at`, `expires_at`, `created_at` are ISO-8601 UTC;
      `expires_at` is strictly after `created_at`; `operator_quote_at` is at
      or before `expires_at`; no entry may be expired.
    - `authorization_record` matches `omni_home@<40 hex>` — the landed commit
      the quote must be readable in (the ruling's "quoted verbatim in the
      ticket and ledger", mechanized as far as YAML can mechanize it).

NOT enforced here (stated so it is not over-claimed): operator AUTHENTICITY.
Nothing in a YAML file can prove a human said the quoted words. This anchor
constrains BLAST RADIUS. See the OMN-16702 residual R1.

Usage:
    uv run validate-staging-namespace-grants \
        --file grants/staging_namespace_grants.yaml

Exit codes:
    0: anchor is valid (or `entries: []` at rest)
    1: one or more violations found
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REQUIRED_FIELDS = {
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
}
# Single-use lifecycle markers, same semantics as the prod anchor's.
OPTIONAL_FIELDS = {
    "consumed",
    "consumed_at",
    "consumed_by_correlation_id",
}

# The 2026-08-30 ruling names exactly these two namespaces, on exactly this
# cluster. Widening either constant widens the relaxation, so both are pinned
# by tests in tests/test_staging_namespace_grants.py.
ALLOWED_NAMESPACES: frozenset[str] = frozenset({"dev", "onex-dev"})
ALLOWED_CLUSTER = "omninode-k3s-system-public"
ALLOWED_SCOPE = "staging-namespace"
ALLOWED_RUNTIME_LANE = "staging-namespace"
ALLOWED_AUTHORIZATION = "in-session-operator-go"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
GRANT_ID_RE = re.compile(
    r"^grant-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
AUTHORIZATION_RECORD_RE = re.compile(r"^omni_home@[0-9a-f]{40}$")
# ISO-8601 UTC: 2026-08-30T05:30:00Z or 2026-08-30T05:30:00+00:00
ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")

_SINGLE_VALUE_FIELDS: tuple[tuple[str, str], ...] = (
    ("scope", ALLOWED_SCOPE),
    ("runtime_lane", ALLOWED_RUNTIME_LANE),
    ("cluster", ALLOWED_CLUSTER),
    ("authorization", ALLOWED_AUTHORIZATION),
)


def parse_iso8601(ts: str) -> datetime | None:
    """Parse an ISO-8601 UTC datetime string; return None on failure."""
    normalized = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


@dataclass(frozen=True)
class ModelStagingGrantValidationResult:
    """Outcome of validating a staging-namespace grants file."""

    passed: bool
    errors: list[str]
    entry_count: int


def _check_duplicate_grant_ids(entries: list[Any]) -> list[str]:
    """No two entries may share a grant_id."""
    errors: list[str] = []
    seen_ids: dict[str, int] = {}
    for idx, entry in enumerate(entries):
        if isinstance(entry, dict) and isinstance(entry.get("grant_id"), str):
            gid = entry["grant_id"]
            if gid in seen_ids:
                errors.append(
                    f"Entry[{idx}]: duplicate grant_id {gid!r} — also used by "
                    f"Entry[{seen_ids[gid]}]. Every grant_id must be unique."
                )
            else:
                seen_ids[gid] = idx
    return errors


def _check_lifecycle_markers(prefix: str, entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "consumed" in entry and not isinstance(entry["consumed"], bool):
        errors.append(f"{prefix}: consumed must be a bool, got: {entry['consumed']!r}")
    if "consumed_at" in entry:
        ca = entry["consumed_at"]
        if not isinstance(ca, str) or not ISO8601_RE.match(ca):
            errors.append(
                f"{prefix}: consumed_at must be ISO-8601 UTC datetime, got: {ca!r}"
            )
    if "consumed_by_correlation_id" in entry:
        cc = entry["consumed_by_correlation_id"]
        if not isinstance(cc, str) or not UUID_RE.match(cc):
            errors.append(
                f"{prefix}: consumed_by_correlation_id must be a UUID, got: {cc!r}"
            )
    return errors


def _check_identity_fields(prefix: str, entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    gid = entry["grant_id"]
    if not isinstance(gid, str) or not GRANT_ID_RE.match(gid):
        errors.append(f"{prefix}: grant_id must match 'grant-<uuid4>', got: {gid!r}")

    for field, allowed in _SINGLE_VALUE_FIELDS:
        val = entry[field]
        if val != allowed:
            errors.append(
                f"{prefix}: {field} must be exactly {allowed!r}, got: {val!r}. "
                "This anchor authorizes public-cluster staging-namespace "
                "mutations only; it can never authorize a prod runtime "
                "promotion (OMN-16702)."
            )

    record = entry["authorization_record"]
    if not isinstance(record, str) or not AUTHORIZATION_RECORD_RE.match(record):
        errors.append(
            f"{prefix}: authorization_record must match 'omni_home@<40-hex commit>' "
            f"— the landed commit the operator quote is readable in — got: {record!r}"
        )

    for str_field in ("operator_quote", "approved_by", "reason"):
        val = entry[str_field]
        if not isinstance(val, str) or not val.strip():
            errors.append(
                f"{prefix}: {str_field} must be a non-empty string, got: {val!r}"
            )
    return errors


def _check_namespaces(prefix: str, entry: dict[str, Any]) -> list[str]:
    """The scope clause: a grant may name only staging-class namespaces."""
    errors: list[str] = []
    namespaces = entry["namespaces"]
    if not isinstance(namespaces, list) or not namespaces:
        errors.append(
            f"{prefix}: namespaces must be a non-empty list, got: {namespaces!r}"
        )
        return errors
    if not all(isinstance(ns, str) for ns in namespaces):
        errors.append(
            f"{prefix}: every namespace must be a string, got: {namespaces!r}"
        )
        return errors
    outside = sorted(set(namespaces) - ALLOWED_NAMESPACES)
    if outside:
        errors.append(
            f"{prefix}: namespaces {outside} are outside the staging-class "
            f"allowlist {sorted(ALLOWED_NAMESPACES)}. The 2026-08-30 ruling "
            "covers dev and onex-dev only; prod runtime promotion keeps the "
            "OMN-13418/OMN-14209 grant path unchanged."
        )
    duplicates = sorted({ns for ns in namespaces if namespaces.count(ns) > 1})
    if duplicates:
        errors.append(f"{prefix}: namespaces contains duplicates: {duplicates}")
    return errors


def _check_timestamps(prefix: str, entry: dict[str, Any], file_path: Path) -> list[str]:
    errors: list[str] = []
    ts_parsed: dict[str, datetime | None] = {}
    for ts_field in ("expires_at", "created_at", "operator_quote_at"):
        ts = entry[ts_field]
        if not isinstance(ts, str) or not ISO8601_RE.match(ts):
            errors.append(
                f"{prefix}: {ts_field} must be ISO-8601 UTC datetime, got: {ts!r}"
            )
            ts_parsed[ts_field] = None
        else:
            ts_parsed[ts_field] = parse_iso8601(ts)

    created = ts_parsed.get("created_at")
    expires = ts_parsed.get("expires_at")
    quoted = ts_parsed.get("operator_quote_at")

    if created is not None and expires is not None and expires <= created:
        errors.append(
            f"{prefix}: expires_at must be strictly after created_at (got "
            f"expires_at={entry['expires_at']!r}, created_at={entry['created_at']!r})"
        )

    if quoted is not None and expires is not None and quoted > expires:
        errors.append(
            f"{prefix}: operator_quote_at must be at or before expires_at (got "
            f"operator_quote_at={entry['operator_quote_at']!r}, "
            f"expires_at={entry['expires_at']!r}) — a grant cannot expire before "
            "the 'go' that authorized it was given."
        )

    if expires is not None and expires < datetime.now(UTC):
        errors.append(
            f"{prefix}: grant is EXPIRED (expires_at={entry['expires_at']!r} is in "
            f"the past) — prune it from {file_path} (at rest: entries: [])"
        )
    return errors


def _validate_entry(idx: int, entry: Any, file_path: Path) -> list[str]:
    prefix = f"Entry[{idx}]"
    if not isinstance(entry, dict):
        return [f"{prefix}: must be a mapping, got {type(entry).__name__}"]

    present = set(entry.keys())
    missing_fields = REQUIRED_FIELDS - present
    extra_fields = present - REQUIRED_FIELDS - OPTIONAL_FIELDS
    if missing_fields or extra_fields:
        errors: list[str] = []
        if missing_fields:
            errors.append(
                f"{prefix}: missing required fields: {sorted(missing_fields)}"
            )
        if extra_fields:
            errors.append(f"{prefix}: unexpected fields: {sorted(extra_fields)}")
        return errors

    return [
        *_check_lifecycle_markers(prefix, entry),
        *_check_identity_fields(prefix, entry),
        *_check_namespaces(prefix, entry),
        *_check_timestamps(prefix, entry, file_path),
    ]


def validate_staging_grants(file_path: Path) -> ModelStagingGrantValidationResult:
    """Validate a staging-namespace grants YAML file.

    Pure(ish) — the only I/O is reading ``file_path``.
    """
    try:
        with file_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (yaml.YAMLError, OSError) as exc:
        return ModelStagingGrantValidationResult(
            passed=False, errors=[f"Cannot parse {file_path}: {exc}"], entry_count=0
        )

    if not isinstance(data, dict):
        return ModelStagingGrantValidationResult(
            passed=False,
            errors=[f"{file_path} must be a YAML mapping at the top level"],
            entry_count=0,
        )

    if set(data.keys()) != {"entries"}:
        errors = []
        missing = {"entries"} - set(data.keys())
        extra = set(data.keys()) - {"entries"}
        if missing:
            errors.append(f"{file_path} missing top-level key 'entries'")
        if extra:
            errors.append(f"{file_path} has unexpected top-level keys: {sorted(extra)}")
        return ModelStagingGrantValidationResult(
            passed=False, errors=errors, entry_count=0
        )

    entries = data["entries"]
    if not isinstance(entries, list):
        return ModelStagingGrantValidationResult(
            passed=False,
            errors=[f"{file_path} 'entries' must be a list"],
            entry_count=0,
        )
    if len(entries) == 0:
        return ModelStagingGrantValidationResult(passed=True, errors=[], entry_count=0)

    errors = _check_duplicate_grant_ids(entries)
    for idx, entry in enumerate(entries):
        errors.extend(_validate_entry(idx, entry, file_path))

    return ModelStagingGrantValidationResult(
        passed=not errors, errors=errors, entry_count=len(entries)
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code (0 valid, 1 violations)."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate the OMN-16702 staging-namespace authorization anchor "
            "(schema + namespace-allowlist + duplicate-id integrity checks). "
            "This anchor cannot authorize a prod runtime promotion."
        )
    )
    parser.add_argument(
        "--file",
        default="grants/staging_namespace_grants.yaml",
        help="Path to the staging-namespace grants YAML file.",
    )
    args = parser.parse_args(argv)

    file_path = Path(args.file)
    result = validate_staging_grants(file_path)

    if not result.passed:
        print(f"FAIL: {file_path} has {len(result.errors)} violation(s):")
        for err in result.errors:
            print(f"  - {err}")
        return 1

    if result.entry_count == 0:
        print(f"PASS: {file_path} is valid (entries: [] at rest)")
    else:
        print(
            f"PASS: {file_path} — {result.entry_count} staging-namespace "
            "authorization(s) validated successfully"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
