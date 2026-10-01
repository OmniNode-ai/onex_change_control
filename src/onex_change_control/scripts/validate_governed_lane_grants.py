# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Validate the governed-lane compose-config grant anchor (OMN-20260).

``grants/governed_lane_grants.yaml`` authorizes ONE compose-config change to ONE
governed ``.201`` compose lane (``stability-test`` or ``judge``). It exists
because the governed lanes are covered by the prod-promotion gate (CLAUDE.md
rule 2a, OMN-15243), while every grant kind in ``prod_promotion_grants.yaml``
names an image digest or a kustomize overlay. A compose-config change, such as
binding a published port to loopback, has neither. The operator approved a new
grant type for it on 2026-10-01 (RULING 2026-10-01T10:37:55Z
lane=compose-grant-kind).

WHY A SEPARATE ANCHOR, NOT A ``target_kind`` IN THE PROD FILE. Every consumer of
``prod_promotion_grants.yaml`` (the omninode_infra dispatch gate, the omnimarket
grant resolver, omnibase_infra's lane-deploy interlock) treats an entry missing
``image_digest`` as malformed and fails closed, so a compose entry pasted there
would block every prod promotion and every stability deploy while it existed.
The staging-namespace anchor (OMN-16702) made the same call for the same
reason. Two files, two validators, no coupling.

SCHEMA, per entry (all fields required; ``consumed*`` optional):

  grant_id          grant-<uuid4>, unique across the file
  target_kind       exactly "compose_config"
  runtime_lane      "stability-test" or "judge"
  compose_project   exactly "omnibase-infra-<runtime_lane>"
  compose_files     non-empty list of omnibase_infra repo paths under docker/,
                    in the exact order they are passed to ``docker compose -f``
  env_files         list (may be empty) of repo paths under docker/ passed as
                    ``--env-file``; the files themselves are host-local
  profiles          list (may be empty) of compose profiles to enable
  services          non-empty list of the services the apply recreates, and
                    nothing else (``up -d --no-deps``) -- the blast radius
  compose_ref       the omnibase_infra commit, 40 lowercase hex, the apply
                    must be checked out at
  rendered_digest   sha256:<64 hex> over the canonical ``docker compose config``
                    render of exactly those inputs. The apply path recomputes it
                    in-process and refuses on any mismatch; no caller asserts it.
  requested_by      GitHub login of the requester
  approved_by       GitHub login of the approver; must differ from requested_by
  expires_at        absolute ISO-8601 UTC, strictly after created_at, not past
  created_at        ISO-8601 UTC
  reason            free text

AUTHORING-TIME SELF-APPROVAL. With ``--requester`` (the pull-request author),
an entry this change ADDS (by ``grant_id`` against ``--base-file``) is refused
when its ``approved_by`` is the requester. A missing or unreadable base file
fails closed: every entry is treated as new.

HONEST LIMIT. No file proves a human said the words behind ``approved_by``.
This anchor constrains blast radius and enforces that approver and requester
are two different accounts; CODEOWNERS review on ``main`` is the human step.

Exit codes:
    0: anchor is valid (or ``entries: []`` at rest)
    1: one or more violations, or the file could not be read
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

TARGET_KIND_COMPOSE_CONFIG = "compose_config"

#: The governed compose lanes on .201 this anchor can authorize. ``prod`` is
#: deliberately absent: production is the AWS namespace and keeps the
#: OMN-13418 image/manifest grant path. ``dev`` is pre-authorized and needs no
#: grant. Widening this set widens what the anchor can authorize, so it is
#: pinned by tests.
ALLOWED_LANES: frozenset[str] = frozenset({"stability-test", "judge"})

REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "grant_id",
        "target_kind",
        "runtime_lane",
        "compose_project",
        "compose_files",
        "env_files",
        "profiles",
        "services",
        "compose_ref",
        "rendered_digest",
        "requested_by",
        "approved_by",
        "expires_at",
        "created_at",
        "reason",
    }
)
OPTIONAL_FIELDS: frozenset[str] = frozenset(
    {"consumed", "consumed_at", "consumed_by_correlation_id"}
)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
GRANT_ID_RE = re.compile(
    r"^grant-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
# A repo path under docker/, no directory components below it, so no "." or
# ".." segment can resolve the grant somewhere the approver did not read.
DOCKER_PATH_RE = re.compile(r"^docker/[A-Za-z0-9_][A-Za-z0-9._-]*$")
COMPOSE_FILE_RE = re.compile(r"^docker/[A-Za-z0-9_][A-Za-z0-9._-]*\.ya?ml$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")


@dataclass(frozen=True)
class ModelGovernedLaneGrantValidationResult:
    """Outcome of validating a governed-lane grants file."""

    passed: bool
    errors: list[str]
    entry_count: int


def parse_iso8601(ts: str) -> datetime | None:
    """Parse an ISO-8601 UTC datetime string; return None on failure."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _check_string_list(
    prefix: str,
    field: str,
    value: Any,
    pattern: re.Pattern[str],
    *,
    allow_empty: bool,
) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        need = "a list" if allow_empty else "a non-empty list"
        return [f"{prefix}: {field} must be {need}, got: {value!r}"]
    errors: list[str] = []
    for item in value:
        if not isinstance(item, str) or not pattern.match(item):
            errors.append(
                f"{prefix}: {field} item {item!r} must match {pattern.pattern!r}"
            )
    strings = [i for i in value if isinstance(i, str)]
    dupes = sorted({i for i in strings if strings.count(i) > 1})
    if dupes:
        errors.append(f"{prefix}: {field} contains duplicates: {dupes}")
    return errors


def _check_target(prefix: str, entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if entry["target_kind"] != TARGET_KIND_COMPOSE_CONFIG:
        errors.append(
            f"{prefix}: target_kind must be exactly {TARGET_KIND_COMPOSE_CONFIG!r}, "
            f"got: {entry['target_kind']!r}"
        )
    lane = entry["runtime_lane"]
    if lane not in ALLOWED_LANES:
        errors.append(
            f"{prefix}: runtime_lane must be one of {sorted(ALLOWED_LANES)}, got: "
            f"{lane!r}. This anchor never authorizes prod (rule 12) and dev needs "
            "no grant."
        )
    elif entry["compose_project"] != f"omnibase-infra-{lane}":
        errors.append(
            f"{prefix}: compose_project must be 'omnibase-infra-{lane}' for "
            f"runtime_lane {lane!r}, got: {entry['compose_project']!r}"
        )
    errors += _check_string_list(
        prefix,
        "compose_files",
        entry["compose_files"],
        COMPOSE_FILE_RE,
        allow_empty=False,
    )
    errors += _check_string_list(
        prefix, "env_files", entry["env_files"], DOCKER_PATH_RE, allow_empty=True
    )
    errors += _check_string_list(
        prefix, "profiles", entry["profiles"], NAME_RE, allow_empty=True
    )
    errors += _check_string_list(
        prefix, "services", entry["services"], NAME_RE, allow_empty=False
    )
    ref = entry["compose_ref"]
    if not isinstance(ref, str) or not COMMIT_RE.match(ref):
        errors.append(
            f"{prefix}: compose_ref must be a full 40-hex commit, got: {ref!r}. A "
            "branch or an abbreviation is not the exact tree the approver read."
        )
    digest = entry["rendered_digest"]
    if not isinstance(digest, str) or not DIGEST_RE.match(digest):
        errors.append(
            f"{prefix}: rendered_digest must match 'sha256:<64hex>', got: {digest!r}"
        )
    return errors


def _check_identity(prefix: str, entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    gid = entry["grant_id"]
    if not isinstance(gid, str) or not GRANT_ID_RE.match(gid):
        errors.append(f"{prefix}: grant_id must match 'grant-<uuid4>', got: {gid!r}")
    for field in ("requested_by", "approved_by"):
        val = entry[field]
        if not isinstance(val, str) or not LOGIN_RE.match(val):
            errors.append(f"{prefix}: {field} must be a GitHub login, got: {val!r}")
    req, app = entry["requested_by"], entry["approved_by"]
    if (
        isinstance(req, str)
        and isinstance(app, str)
        and req.casefold() == app.casefold()
    ):
        errors.append(
            f"{prefix}: self_granted - approved_by {app!r} equals requested_by. "
            "A governed-lane grant needs a second account (rule 2a)."
        )
    reason = entry["reason"]
    if not isinstance(reason, str) or not reason.strip():
        errors.append(f"{prefix}: reason must be a non-empty string, got: {reason!r}")
    return errors


def _check_lifecycle(prefix: str, entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "consumed" in entry and not isinstance(entry["consumed"], bool):
        errors.append(f"{prefix}: consumed must be a bool, got: {entry['consumed']!r}")
    ca = entry.get("consumed_at")
    if "consumed_at" in entry and (not isinstance(ca, str) or not ISO8601_RE.match(ca)):
        errors.append(f"{prefix}: consumed_at must be ISO-8601 UTC, got: {ca!r}")
    cc = entry.get("consumed_by_correlation_id")
    if "consumed_by_correlation_id" in entry and (
        not isinstance(cc, str) or not UUID_RE.match(cc)
    ):
        errors.append(
            f"{prefix}: consumed_by_correlation_id must be a UUID, got: {cc!r}"
        )
    return errors


def _check_timestamps(prefix: str, entry: dict[str, Any], now: datetime) -> list[str]:
    errors: list[str] = []
    parsed: dict[str, datetime | None] = {}
    for field in ("expires_at", "created_at"):
        ts = entry[field]
        if not isinstance(ts, str) or not ISO8601_RE.match(ts):
            errors.append(f"{prefix}: {field} must be ISO-8601 UTC, got: {ts!r}")
            parsed[field] = None
        else:
            parsed[field] = parse_iso8601(ts)
    created, expires = parsed["created_at"], parsed["expires_at"]
    if created is not None and expires is not None and expires <= created:
        errors.append(f"{prefix}: expires_at must be strictly after created_at")
    if expires is not None and expires < now:
        errors.append(
            f"{prefix}: grant is EXPIRED (expires_at={entry['expires_at']!r}); "
            "prune it (at rest: entries: [])"
        )
    return errors


def _validate_entry(idx: int, entry: Any, now: datetime) -> list[str]:
    prefix = f"Entry[{idx}]"
    if not isinstance(entry, dict):
        return [f"{prefix}: must be a mapping, got {type(entry).__name__}"]
    present = set(entry)
    missing = REQUIRED_FIELDS - present
    extra = present - REQUIRED_FIELDS - OPTIONAL_FIELDS
    if missing or extra:
        out: list[str] = []
        if missing:
            out.append(f"{prefix}: missing required fields: {sorted(missing)}")
        if extra:
            out.append(f"{prefix}: unexpected fields: {sorted(extra)}")
        return out
    return [
        *_check_target(prefix, entry),
        *_check_identity(prefix, entry),
        *_check_lifecycle(prefix, entry),
        *_check_timestamps(prefix, entry, now),
    ]


def _load_entries(file_path: Path) -> tuple[list[Any] | None, list[str]]:
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        return None, [f"Cannot parse {file_path}: {exc}"]
    if not isinstance(data, dict) or set(data) != {"entries"}:
        return None, [f"{file_path} must be a mapping with exactly one key 'entries'"]
    if not isinstance(data["entries"], list):
        return None, [f"{file_path} 'entries' must be a list"]
    return data["entries"], []


def _base_grant_ids(base_file: Path | None) -> set[str]:
    """grant_ids already present at the base. Unreadable base -> empty (fail closed)."""
    if base_file is None:
        return set()
    entries, errors = _load_entries(base_file)
    if errors or entries is None:
        return set()
    return {
        e["grant_id"]
        for e in entries
        if isinstance(e, dict) and isinstance(e.get("grant_id"), str)
    }


def validate_governed_lane_grants(
    file_path: Path,
    *,
    requester: str | None = None,
    base_file: Path | None = None,
    now: datetime | None = None,
) -> ModelGovernedLaneGrantValidationResult:
    """Validate the anchor. The only I/O is reading the two files."""
    now = now or datetime.now(UTC)
    entries, errors = _load_entries(file_path)
    if entries is None:
        return ModelGovernedLaneGrantValidationResult(
            passed=False, errors=errors, entry_count=0
        )

    seen: dict[str, int] = {}
    for idx, entry in enumerate(entries):
        gid = entry.get("grant_id") if isinstance(entry, dict) else None
        if isinstance(gid, str):
            if gid in seen:
                errors.append(
                    f"Entry[{idx}]: duplicate grant_id {gid!r} "
                    f"(also Entry[{seen[gid]}])"
                )
            seen.setdefault(gid, idx)
        errors.extend(_validate_entry(idx, entry, now))

    if requester is not None:
        base_ids = _base_grant_ids(base_file)
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict) or entry.get("grant_id") in base_ids:
                continue
            app = entry.get("approved_by")
            if isinstance(app, str) and app.casefold() == requester.casefold():
                errors.append(
                    f"Entry[{idx}]: self_granted - approved_by {app!r} is the "
                    "identity that opened this change"
                )

    return ModelGovernedLaneGrantValidationResult(
        passed=not errors, errors=errors, entry_count=len(entries)
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. 0 valid, 1 violations."""
    parser = argparse.ArgumentParser(
        description="Validate the OMN-20260 governed-lane compose-config grant anchor."
    )
    parser.add_argument("--file", default="grants/governed_lane_grants.yaml")
    parser.add_argument(
        "--requester",
        default=None,
        help="Pull-request author; an entry this change adds may not name it "
        "as approved_by.",
    )
    parser.add_argument(
        "--base-file",
        default=None,
        help="The anchor at the base ref; scopes --requester to added entries.",
    )
    args = parser.parse_args(argv)
    if args.requester is not None and not args.requester.strip():
        print("FAIL: --requester is empty; refusing rather than skipping the check")
        return 1

    file_path = Path(args.file)
    result = validate_governed_lane_grants(
        file_path,
        requester=args.requester,
        base_file=Path(args.base_file) if args.base_file else None,
    )
    if not result.passed:
        print(f"FAIL: {file_path} has {len(result.errors)} violation(s):")
        for err in result.errors:
            print(f"  - {err}")
        return 1
    if result.entry_count == 0:
        print(f"PASS: {file_path} is valid (entries: [] at rest)")
    else:
        print(f"PASS: {file_path} - {result.entry_count} grant(s) validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
