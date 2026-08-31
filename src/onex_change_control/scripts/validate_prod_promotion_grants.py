# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Validate the prod-promotion-grant trust anchor (OMN-13418 / OMN-14441 / OMN-14814).

`grants/prod_promotion_grants.yaml` is the un-forgeable trust anchor for
production promotion approvals (CLAUDE.md §2a/§12). This module is the
canonical (and, as of OMN-14441, the ONLY) implementation of its schema and
integrity checks — previously duplicated inline in a standalone workflow
(`.github/workflows/validate-prod-promotion-grants.yml`) that ran, could
fail, but was structurally invisible to the required CI Summary rollup
(a separate workflow file gets a separate `run_id`; OMN-14415 instance #1).
That workflow is retired; this script is invoked as an unconditional job in
`ci.yml`, wired into `ci-summary`'s `needs:` with an explicit fail-closed
check (skipped/cancelled/failure all block — see OMN-14350's
`no-noncanonical-lifecycle-classes` for the established pattern this
mirrors).

Checks enforced (schema, pre-existing, ported verbatim from the retired
workflow):
    - File must parse as valid YAML; top-level key must be `entries` (list).
    - Each entry has all required fields; only OMN-13424 `consumed*`
      lifecycle markers are tolerated as extras.
    - `grant_id` matches `grant-<uuid4>`; `image_digest` matches
      `sha256:<64hex>`.
    - `expires_at` / `created_at` are ISO-8601 UTC; `expires_at` is strictly
      after `created_at`; no entry may be expired (OMN-13424: at rest,
      `entries: []`).

Checks enforced (integrity, NEW as of OMN-14441):
    - No two entries share a `grant_id` (a duplicate would let two grants
      of the same identity resolve ambiguously downstream).

Dual-control REMOVED (OMN-14814):
    The former `approved_by != PR-author` self-approval check has been
    removed. `@OmniNode-ai/platform-leads` has exactly one member (the sole
    CODEOWNER), so requiring a *second, different* approver would wedge every
    prod-promotion grant permanently — the sole owner can never satisfy
    `approved_by != requested_by`. The grant remains un-forgeable through the
    checks that survive: a human must author the entry in a PR that passes
    CODEOWNERS review (an AI agent cannot self-mint it), the grant must be
    fresh (unexpired), digest-pinned, uniquely identified, and time-bounded.
    Stability-proven digest, OCC receipt, declared rollback target, gated-path
    routing, and the health-conditional waiver are enforced downstream by the
    prod-promotion gate node — this validator governs only the grants-file
    schema/integrity and is unchanged in those respects.

Usage:
    uv run validate-prod-promotion-grants --file grants/prod_promotion_grants.yaml

Exit codes:
    0: grants file is valid (or `entries: []` at rest)
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
    "runtime_lane",
    "image_digest",
    "promotion_batch_id",
    "approved_by",
    "expires_at",
    "created_at",
    "reason",
}
# OMN-13424 single-use lifecycle markers. OPTIONAL (absent == not consumed);
# tolerated as extras so a consumed grant can carry its provenance before
# the prune job removes it.
OPTIONAL_FIELDS = {
    "consumed",
    "consumed_at",
    "consumed_by_correlation_id",
}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
GRANT_ID_RE = re.compile(
    r"^grant-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
# ISO-8601 UTC: 2026-06-21T12:00:00Z or 2026-06-21T12:00:00+00:00
ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")


def parse_iso8601(ts: str) -> datetime | None:
    """Parse an ISO-8601 UTC datetime string; return None on failure."""
    normalized = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


@dataclass(frozen=True)
class ModelGrantValidationResult:
    """Outcome of validating a grants file. `passed` is False iff `errors`
    is non-empty; kept as an explicit field (not derived) so callers never
    have to remember `bool(errors)`.
    """

    passed: bool
    errors: list[str]
    entry_count: int


def _check_duplicate_grant_ids(entries: list[Any]) -> list[str]:
    """No two entries may share a grant_id — independent of PR-newness, a
    duplicate is always invalid state regardless of which PR introduced the
    second occurrence.
    """
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

    digest = entry["image_digest"]
    if not isinstance(digest, str) or not IMAGE_DIGEST_RE.match(digest):
        errors.append(
            f"{prefix}: image_digest must match 'sha256:<64hex>', got: {digest!r}"
        )

    for str_field in ("runtime_lane", "promotion_batch_id", "approved_by", "reason"):
        val = entry[str_field]
        if not isinstance(val, str) or not val.strip():
            errors.append(
                f"{prefix}: {str_field} must be a non-empty string, got: {val!r}"
            )
    return errors


def _check_timestamps(prefix: str, entry: dict[str, Any], file_path: Path) -> list[str]:
    errors: list[str] = []
    ts_parsed: dict[str, datetime | None] = {}
    for ts_field in ("expires_at", "created_at"):
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
    if created is not None and expires is not None and expires <= created:
        errors.append(
            f"{prefix}: expires_at must be strictly after created_at (got "
            f"expires_at={entry['expires_at']!r}, created_at={entry['created_at']!r})"
        )

    # OMN-13424 lint/prune signal: an expired grant must not linger.
    if expires is not None and expires < datetime.now(UTC):
        errors.append(
            f"{prefix}: grant is EXPIRED (expires_at={entry['expires_at']!r} is in the "
            f"past) — prune it from {file_path} (OMN-13424: at rest entries: [])"
        )
    return errors


def _validate_entry(
    idx: int,
    entry: Any,
    file_path: Path,
) -> list[str]:
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
        *_check_timestamps(prefix, entry, file_path),
    ]


def validate_grants(file_path: Path) -> ModelGrantValidationResult:
    """Validate a prod-promotion-grants YAML file. Pure(ish) — the only I/O
    is reading `file_path`.
    """
    try:
        with file_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (yaml.YAMLError, OSError) as exc:
        return ModelGrantValidationResult(
            passed=False, errors=[f"Cannot parse {file_path}: {exc}"], entry_count=0
        )

    if not isinstance(data, dict):
        return ModelGrantValidationResult(
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
        return ModelGrantValidationResult(passed=False, errors=errors, entry_count=0)

    entries = data["entries"]
    if not isinstance(entries, list):
        return ModelGrantValidationResult(
            passed=False,
            errors=[f"{file_path} 'entries' must be a list"],
            entry_count=0,
        )
    if len(entries) == 0:
        return ModelGrantValidationResult(passed=True, errors=[], entry_count=0)

    errors = _check_duplicate_grant_ids(entries)
    for idx, entry in enumerate(entries):
        errors.extend(_validate_entry(idx, entry, file_path))

    return ModelGrantValidationResult(
        passed=not errors, errors=errors, entry_count=len(entries)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the prod-promotion-grant trust anchor "
            "(schema + duplicate-id integrity checks)."
        )
    )
    parser.add_argument(
        "--file",
        default="grants/prod_promotion_grants.yaml",
        help="Path to the grants YAML file.",
    )
    args = parser.parse_args(argv)

    file_path = Path(args.file)
    result = validate_grants(file_path)

    if not result.passed:
        print(f"FAIL: {file_path} has {len(result.errors)} violation(s):")
        for err in result.errors:
            print(f"  - {err}")
        return 1

    if result.entry_count == 0:
        print(f"PASS: {file_path} is valid (entries: [] at rest)")
    else:
        print(
            f"PASS: {file_path} — {result.entry_count} grant(s) validated successfully"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
