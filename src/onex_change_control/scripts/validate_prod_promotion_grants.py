# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Validate the prod-promotion-grant trust anchor (OMN-13418 / OMN-14441).

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
    - Self-approval: for entries that are NEW in this PR (added since
      `--base-ref`), `approved_by` must not equal the PR author
      (case-insensitive). This mirrors the skip-token allowlist's
      self-approval rejection in `omnibase_core.validation.validator_receipt_gate`.
      Self-approval checking is diff-scoped (only entries newly added by
      THIS PR) rather than checked against every entry in the file — an
      unconditional per-PR check over the whole file would false-positive
      an unrelated PR opened by someone who happens to share a login with
      a *historical* approver of an entry they never touched. Requires both
      `--pr-author` and `--base-ref`; without both, self-approval checking
      is skipped (there's no reliable way to determine "new" without a
      base to diff against, or "who's approving" without PR context).

Usage:
    uv run validate-prod-promotion-grants --file grants/prod_promotion_grants.yaml
    uv run validate-prod-promotion-grants --file grants/prod_promotion_grants.yaml \
        --pr-author jonahgabriel --base-ref origin/dev

Exit codes:
    0: grants file is valid (or `entries: []` at rest)
    1: one or more violations found
"""

from __future__ import annotations

import argparse
import re
import subprocess
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


def _resolve_repo_relative_path(file_path: Path) -> tuple[Path, str] | None:
    """Resolve `file_path`'s git repo root and its path relative to that
    root. `git show <ref>:<path>` requires a repo-root-relative path, never
    absolute — file_path.parent is NOT the repo root in production (the
    grants file lives one level below the checkout root, at
    `<checkout>/grants/prod_promotion_grants.yaml`).
    """
    toplevel = subprocess.run(  # noqa: S603 — trusted git subprocess, no shell, no user-controlled argv
        ["git", "-C", str(file_path.resolve().parent), "rev-parse", "--show-toplevel"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if toplevel.returncode != 0:
        return None
    repo_root = Path(toplevel.stdout.strip())
    try:
        relative_path = file_path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return None
    return repo_root, relative_path


def _load_base_grant_ids(file_path: Path, base_ref: str) -> set[str] | None:
    """Return the `grant_id`s present in `file_path` at `base_ref`. Returns
    an empty set if the file didn't exist at `base_ref` (every current
    entry is then new), or None if the base revision genuinely can't be
    resolved (git/YAML failure) — callers treat None as "diff unavailable,"
    distinct from "diff available, zero entries at base."
    """
    resolved = _resolve_repo_relative_path(file_path)
    if resolved is None:
        return None
    repo_root, relative_path = resolved

    try:
        result = subprocess.run(  # noqa: S603 — trusted git subprocess, no shell, no user-controlled argv
            ["git", "-C", str(repo_root), "show", f"{base_ref}:{relative_path}"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        # File didn't exist at base_ref (or base_ref itself doesn't
        # resolve) — every entry currently in the file is new.
        return set()

    try:
        base_data = yaml.safe_load(result.stdout)
    except yaml.YAMLError:
        return None

    base_entries = base_data.get("entries", []) if isinstance(base_data, dict) else []
    if not isinstance(base_entries, list):
        base_entries = []

    return {
        entry["grant_id"]
        for entry in base_entries
        if isinstance(entry, dict) and isinstance(entry.get("grant_id"), str)
    }


def _compute_new_grant_ids(
    entries: list[Any], file_path: Path, pr_author: str | None, base_ref: str | None
) -> set[str] | None:
    """Return the set of grant_ids newly added in this PR, or None if
    self-approval checking isn't possible (missing PR context, or the base
    revision couldn't be resolved).
    """
    if not pr_author or not base_ref:
        return None
    base_ids = _load_base_grant_ids(file_path, base_ref)
    if base_ids is None:
        return None
    head_ids = {
        entry["grant_id"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("grant_id"), str)
    }
    return head_ids - base_ids


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


def _check_self_approval(
    prefix: str,
    entry: dict[str, Any],
    new_grant_ids: set[str] | None,
    pr_author: str | None,
) -> list[str]:
    """OMN-14441: only for entries this PR newly added — see module
    docstring for why this is diff-scoped rather than checked globally.
    """
    gid = entry.get("grant_id")
    approved_by = entry.get("approved_by")
    if (
        new_grant_ids is not None
        and gid in new_grant_ids
        and pr_author
        and isinstance(approved_by, str)
        and approved_by.strip().casefold() == pr_author.strip().casefold()
    ):
        return [
            f"{prefix}: SELF-APPROVAL REJECTED — approved_by={approved_by!r} equals "
            f"the PR author {pr_author!r}. A different platform-lead must approve "
            "this grant (CLAUDE.md §2a: approved_by != requested_by)."
        ]
    return []


def _validate_entry(
    idx: int,
    entry: Any,
    new_grant_ids: set[str] | None,
    pr_author: str | None,
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
        *_check_self_approval(prefix, entry, new_grant_ids, pr_author),
    ]


def validate_grants(
    file_path: Path,
    *,
    pr_author: str | None = None,
    base_ref: str | None = None,
) -> ModelGrantValidationResult:
    """Validate a prod-promotion-grants YAML file. Pure(ish) — the only I/O
    is reading `file_path` and, when `base_ref` is given, one `git show`
    subprocess call to diff against the base revision.
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

    new_grant_ids = _compute_new_grant_ids(entries, file_path, pr_author, base_ref)
    errors = _check_duplicate_grant_ids(entries)
    for idx, entry in enumerate(entries):
        errors.extend(_validate_entry(idx, entry, new_grant_ids, pr_author, file_path))

    return ModelGrantValidationResult(
        passed=not errors, errors=errors, entry_count=len(entries)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the prod-promotion-grant trust anchor "
            "(schema + duplicate-id + self-approval integrity checks)."
        )
    )
    parser.add_argument(
        "--file",
        default="grants/prod_promotion_grants.yaml",
        help="Path to the grants YAML file.",
    )
    parser.add_argument(
        "--pr-author",
        default=None,
        help=(
            "GitHub login of the PR author, for self-approval detection. "
            "Requires --base-ref to be meaningful."
        ),
    )
    parser.add_argument(
        "--base-ref",
        default=None,
        help=(
            "Git ref to diff against to determine which entries are NEW in "
            "this PR (self-approval is only checked for new entries)."
        ),
    )
    args = parser.parse_args(argv)

    file_path = Path(args.file)
    result = validate_grants(
        file_path, pr_author=args.pr_author, base_ref=args.base_ref
    )

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
