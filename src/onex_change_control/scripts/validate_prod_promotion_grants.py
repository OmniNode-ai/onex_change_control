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

TARGET KIND (OMN-18566, operator ruling 2026-09-17T10:33:11Z, verbatim "1",
recorded at omni_home docs/tracking/ROLLING_WORK_LEDGER.md:4077):
    A grant entry gains an OPTIONAL `target_kind` of "image" (the default) or
    "manifest". An entry that OMITS the field IS an image grant, byte-for-byte
    in meaning -- that is what keeps every grant already written, and every
    tool that has never heard of the field, working unchanged on both halves
    of the gate.

    Common to both kinds: grant_id, runtime_lane, promotion_batch_id,
    approved_by, expires_at, created_at, reason.

    target_kind: image -- REQUIRED, and forbidden on a manifest grant:
      image_digest      "sha256:<64hex>" of the image being promoted.

    target_kind: manifest -- ALL REQUIRED, and forbidden on an image grant:
      manifest_path     repo-relative kustomize overlay under `k8s/` that the
                        promotion applies, e.g.
                        k8s/data-plane/postgres/backup/overlays/public
      manifest_subtree  repo-relative directory under `k8s/` that every file
                        the overlay's kustomization tree references must lie
                        under. THE APPROVED BLAST RADIUS. Declared rather than
                        derived from manifest_path because a real overlay
                        reaches outside its own directory -- the public backup
                        overlay's only resource is `../../base` -- so
                        "everything under manifest_path" would refuse the very
                        overlays this kind exists to authorize. manifest_path
                        must lie inside it, segment-wise.
      manifest_ref      the omninode_infra commit sha (full 40 lowercase hex)
                        the overlay renders at. The dispatch-time gate refuses
                        unless the checkout it is about to apply IS that
                        commit.
      rendered_digest   "sha256:<64hex>" over the CANONICAL render of
                        manifest_path at manifest_ref: the UTF-8 bytes of the
                        compact, recursively key-sorted JSON array of the
                        objects `kubectl kustomize <manifest_path>` emits,
                        ordered by (apiVersion, kind, namespace, name). The
                        dispatch-time gate recomputes it and refuses on a
                        mismatch; no caller can assert it.

    The two kinds' target fields are MUTUALLY EXCLUSIVE. A grant naming both
    an image digest and a manifest path describes two different promotions and
    the gate would have to guess which one the approver meant.

    A manifest grant is NOT a lighter grant: the @main fetch, CODEOWNERS
    review, absolute expires_at, approved_by != requested_by at BOTH authoring
    and dispatch time, single-use consumption and unique grant_id all apply to
    it identically.

    WHY THIS REFERENCE LIVES HERE and not in the anchor's own header. That file
    is a guarded authorization surface: `check-bot-authored-authz-guard`
    refuses any BOT-authored change to `grants/**`, because a machine writer
    must never mint its own authorization. This module lives under `src/`,
    which `check-human-authored-privileged-pr` requires to be authored BY the
    writer App. One PR cannot satisfy both gates, so the schema and its
    enforcement cannot move in one change, and the enforcing module is the
    honest home for the reference.

Self-approval REFUSED again (OMN-17157, restoring OMN-14441):
    `approved_by` must not be the identity that REQUESTED the grant. The
    failure carries the reason string `self_granted` — the same token
    omninode_infra's promotion-time gate emits
    (`EnumGrantOutcome.SELF_GRANTED`) — so a self-approved grant is refused at
    REVIEW time instead of surviving CI here and blocking later, at the moment
    someone is trying to promote.

    OMN-14814 had removed this check, on the premise that
    `@OmniNode-ai/platform-leads` "has exactly one member (the sole
    CODEOWNER)" and so a second, different approver could never be supplied.
    That premise no longer holds — the team has TWO members — so the control
    is satisfiable and its absence is a hole rather than a concession.

    The check is REQUESTER-SCOPED and DIFF-SCOPED:
      * with no `requester`, it does not run (a bare `--file` invocation is
        schema validation and nothing more); and
      * with a `base_file`, it fires only on entries the change ADDS, so an
        unrelated PR opened by someone who shares a login with the approver
        of an untouched entry is never false-positived. A missing or
        unreadable base file fails CLOSED — every entry is then treated as
        new, because an unreadable base is not evidence that an entry is
        pre-existing.

    HONEST LIMIT — what this does NOT cover, and what does. The canonical
    authoring path (`.github/workflows/stage-prod-promotion-grant.yml`)
    opens its PR with a GitHub App token, so on that path the PR author is
    `onexbot-occ-writer[bot]` and never a human: comparing `approved_by` to
    the PR author is VACUOUS there. That path is covered at DISPATCH time
    instead, where the requesting human is real — the workflow refuses
    `inputs.approved_by == github.actor` with the same reason string. This
    validator covers the other path: a grants-file entry hand-authored in an
    ordinary PR. Neither half is sufficient alone; both are wired.

    Stability-proven digest, OCC receipt, declared rollback target, gated-path
    routing, and the health-conditional waiver remain enforced downstream by
    the prod-promotion gate — this validator governs the grants file only.

Usage:
    uv run validate-prod-promotion-grants --file grants/prod_promotion_grants.yaml
    uv run validate-prod-promotion-grants --file grants/prod_promotion_grants.yaml \
        --requester jonahgabriel --base-file /tmp/base_grants.yaml

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

# OMN-18566: the target kind. A grant authorizes ONE promotion, and there are
# two shapes it can take -- an image digest, or a rendered kustomize overlay.
# `image` is the DEFAULT precisely so that every entry written before this
# field existed keeps its exact meaning: absent target_kind IS target_kind:
# image, byte-for-byte, on both halves of the gate.
TARGET_KIND_IMAGE = "image"
TARGET_KIND_MANIFEST = "manifest"
DEFAULT_TARGET_KIND = TARGET_KIND_IMAGE
VALID_TARGET_KINDS = frozenset({TARGET_KIND_IMAGE, TARGET_KIND_MANIFEST})

#: Fields every grant carries, whatever it promotes.
COMMON_REQUIRED_FIELDS = {
    "grant_id",
    "runtime_lane",
    "promotion_batch_id",
    "approved_by",
    "expires_at",
    "created_at",
    "reason",
}
#: Fields an `image` grant carries and a `manifest` grant must NOT.
IMAGE_REQUIRED_FIELDS = {"image_digest"}
#: Fields a `manifest` grant carries and an `image` grant must NOT.
#:
#: `manifest_subtree` is a separate field rather than a derivation from
#: `manifest_path` because a real overlay's kustomization tree reaches OUTSIDE
#: its own directory -- k8s/data-plane/postgres/backup/overlays/public declares
#: its only resource as `../../base`. A rule of "every referenced file lies
#: under manifest_path" would refuse the very overlay this kind exists to
#: authorize. Declaring the subtree makes the approved blast radius explicit
#: and reviewable instead of implicit, and it is what omninode_infra's
#: dispatch-time gate bounds the transitive kustomization tree against.
MANIFEST_REQUIRED_FIELDS = {
    "manifest_path",
    "manifest_subtree",
    "manifest_ref",
    "rendered_digest",
}

#: Union of everything any kind requires. Retained under its historical name
#: because it is the set a reader looking for "the grant schema" expects to
#: find; the per-kind sets above are what validation actually uses.
REQUIRED_FIELDS = COMMON_REQUIRED_FIELDS | IMAGE_REQUIRED_FIELDS

# OMN-13424 single-use lifecycle markers. OPTIONAL (absent == not consumed);
# tolerated as extras so a consumed grant can carry its provenance before
# the prune job removes it. `target_kind` is optional in the same sense: it
# may be written out explicitly, and its absence means `image`.
OPTIONAL_FIELDS = {
    "consumed",
    "consumed_at",
    "consumed_by_correlation_id",
    "target_kind",
}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
GRANT_ID_RE = re.compile(
    r"^grant-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
#: Same shape as an image digest. Spelled separately so the two can diverge
#: without one silently inheriting the other's rule.
RENDERED_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
#: A full 40-hex commit sha. Abbreviations are ambiguous and a branch name is
#: not a commit -- a grant must name the exact tree the approver read.
MANIFEST_REF_RE = re.compile(r"^[0-9a-f]{40}$")
#: A repo-relative kustomize path under `k8s/`. Anchored to `k8s/` so a grant
#: cannot point at scripts, workflows or anything else in the repository, and
#: segment-checked below so no `.` or `..` component can escape the tree.
MANIFEST_PATH_RE = re.compile(r"^k8s/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$")
# ISO-8601 UTC: 2026-06-21T12:00:00Z or 2026-06-21T12:00:00+00:00
ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")

# OMN-17157: the reason string the promotion-time gate in omninode_infra emits
# for this same condition (EnumGrantOutcome.SELF_GRANTED). Spelled once, here,
# so both halves of the control are findable with one grep and cannot drift
# into two differently-worded refusals of the same thing.
SELF_GRANTED_REASON = "self_granted"


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


def _load_base_grant_ids(base_file: Path | None) -> frozenset[str] | None:
    """Grant ids present in the grants file AS IT EXISTS ON THE BASE REF.

    Returns ``None`` when newness cannot be established — no base file was
    supplied, or the supplied one is missing/unreadable/malformed. Callers
    MUST treat ``None`` as "every entry is new" (fail closed): an unreadable
    base is not evidence that an entry is pre-existing.
    """
    if base_file is None:
        return None
    try:
        with base_file.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    entries = data.get("entries")
    if not isinstance(entries, list):
        return None
    return frozenset(
        entry["grant_id"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("grant_id"), str)
    )


def _check_self_approval(
    entries: list[Any],
    *,
    requester: str,
    base_grant_ids: frozenset[str] | None,
) -> list[str]:
    """Refuse entries NEW in this change whose approved_by is the requester.

    GitHub logins are case-insensitive, so the comparison is casefolded —
    otherwise a change of capitalisation would launder a self-approval past
    the check while resolving to the same account everywhere else.
    """
    errors: list[str] = []
    requester_key = requester.casefold()
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        approved_by = entry.get("approved_by")
        if not isinstance(approved_by, str):
            # Shape is _check_identity_fields' job; do not double-report.
            continue
        grant_id = entry.get("grant_id")
        is_new = (
            base_grant_ids is None
            or not isinstance(grant_id, str)
            or grant_id not in base_grant_ids
        )
        if not is_new:
            continue
        if approved_by.casefold() == requester_key:
            errors.append(
                f"Entry[{idx}]: {SELF_GRANTED_REASON} — approved_by "
                f"{approved_by!r} is the identity that requested this grant "
                f"({requester!r}). A prod-promotion grant must be approved by "
                "someone other than the person requesting it "
                "(@OmniNode-ai/platform-leads has more than one member). This "
                "is the same condition omninode_infra's promotion-time gate "
                "refuses as 'self_granted'; refusing it here means it is "
                "caught at review time instead of at promotion time."
            )
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


def resolve_target_kind(entry: dict[str, Any]) -> str:
    """The kind this entry promotes. Absent means `image` (OMN-18566).

    Callers that need to know whether the field was WRITTEN (as opposed to
    defaulted) read `entry` directly; every validation rule below cares only
    about the resolved kind, which is what makes an entry with no
    `target_kind` indistinguishable from one that spells `image` out.
    """
    kind = entry.get("target_kind", DEFAULT_TARGET_KIND)
    return kind if isinstance(kind, str) else str(kind)


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
    if not MANIFEST_PATH_RE.match(value):
        return [
            f"{prefix}: {field} must be a repo-relative path under 'k8s/' with "
            f"no leading or trailing slash, got: {value!r}"
        ]
    if any(segment in {".", ".."} for segment in value.split("/")):
        return [
            f"{prefix}: {field} contains a '.' or '..' segment ({value!r}). A "
            "grant must name the exact tree the approver read; a relative "
            "segment lets an approved grant resolve somewhere else."
        ]
    return []


def _check_kind_fields(prefix: str, entry: dict[str, Any]) -> list[str]:
    """Shape-check the fields that belong to this entry's target kind."""
    kind = resolve_target_kind(entry)
    if kind == TARGET_KIND_IMAGE:
        digest = entry["image_digest"]
        if not isinstance(digest, str) or not IMAGE_DIGEST_RE.match(digest):
            return [
                f"{prefix}: image_digest must match 'sha256:<64hex>', got: {digest!r}"
            ]
        return []

    errors: list[str] = []
    errors.extend(
        _check_manifest_path_shape(prefix, "manifest_path", entry["manifest_path"])
    )
    errors.extend(
        _check_manifest_path_shape(
            prefix, "manifest_subtree", entry["manifest_subtree"]
        )
    )
    ref = entry["manifest_ref"]
    if not isinstance(ref, str) or not MANIFEST_REF_RE.match(ref):
        errors.append(
            f"{prefix}: manifest_ref must be a full 40-character lowercase hex "
            f"commit sha, got: {ref!r}. An abbreviation is ambiguous and a "
            "branch name is not a commit."
        )
    rendered = entry["rendered_digest"]
    if not isinstance(rendered, str) or not RENDERED_DIGEST_RE.match(rendered):
        errors.append(
            f"{prefix}: rendered_digest must match 'sha256:<64hex>', got: {rendered!r}"
        )
    if not errors and not _is_inside(entry["manifest_path"], entry["manifest_subtree"]):
        errors.append(
            f"{prefix}: manifest_path {entry['manifest_path']!r} does not lie "
            f"inside manifest_subtree {entry['manifest_subtree']!r}. The "
            "subtree is the approved blast radius; a path outside it describes "
            "a promotion the approver did not bound."
        )
    return errors


def _check_identity_fields(prefix: str, entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    gid = entry["grant_id"]
    if not isinstance(gid, str) or not GRANT_ID_RE.match(gid):
        errors.append(f"{prefix}: grant_id must match 'grant-<uuid4>', got: {gid!r}")

    errors.extend(_check_kind_fields(prefix, entry))

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

    kind_raw = entry.get("target_kind", DEFAULT_TARGET_KIND)
    if not isinstance(kind_raw, str) or kind_raw not in VALID_TARGET_KINDS:
        return [
            f"{prefix}: target_kind must be one of "
            f"{sorted(VALID_TARGET_KINDS)}, got: {kind_raw!r}. Omit it for an "
            f"image promotion; it defaults to {DEFAULT_TARGET_KIND!r}."
        ]
    kind = kind_raw

    # The two kinds' fields are MUTUALLY EXCLUSIVE, not merely
    # independently optional. A grant naming both an image digest and a
    # manifest path describes two different promotions, and the
    # dispatch-time gate would have to guess which one the approver meant.
    if kind == TARGET_KIND_IMAGE:
        required = COMMON_REQUIRED_FIELDS | IMAGE_REQUIRED_FIELDS
        forbidden = MANIFEST_REQUIRED_FIELDS
    else:
        required = COMMON_REQUIRED_FIELDS | MANIFEST_REQUIRED_FIELDS
        forbidden = IMAGE_REQUIRED_FIELDS

    present = set(entry.keys())
    missing_fields = required - present
    forbidden_present = forbidden & present
    extra_fields = present - required - forbidden - OPTIONAL_FIELDS
    if missing_fields or forbidden_present or extra_fields:
        errors: list[str] = []
        if missing_fields:
            errors.append(
                f"{prefix}: missing required fields for target_kind "
                f"{kind!r}: {sorted(missing_fields)}"
            )
        if forbidden_present:
            errors.append(
                f"{prefix}: fields {sorted(forbidden_present)} do not belong "
                f"to a target_kind {kind!r} grant. The two kinds' target "
                "fields are mutually exclusive: one grant authorizes one "
                "promotion."
            )
        if extra_fields:
            errors.append(f"{prefix}: unexpected fields: {sorted(extra_fields)}")
        return errors

    return [
        *_check_lifecycle_markers(prefix, entry),
        *_check_identity_fields(prefix, entry),
        *_check_timestamps(prefix, entry, file_path),
    ]


def validate_grants(
    file_path: Path,
    *,
    requester: str | None = None,
    base_file: Path | None = None,
) -> ModelGrantValidationResult:
    """Validate a prod-promotion-grants YAML file. Pure(ish) — the only I/O
    is reading `file_path` (and `base_file`, when diff-scoping is requested).

    `requester` is the identity asking for the grant, resolved by the CALLER
    from an authenticated source (the PR author on CI, never a field inside
    the YAML — a requester could hand-type any name into `approved_by`).
    When it is ``None`` the OMN-17157 self-approval check does not run and
    this is schema validation only.
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
    if requester is not None:
        errors.extend(
            _check_self_approval(
                entries,
                requester=requester,
                base_grant_ids=_load_base_grant_ids(base_file),
            )
        )
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
    parser.add_argument(
        "--requester",
        default=None,
        help="OMN-17157: GitHub login of the identity requesting the grant "
        "(on CI, the PR author). When supplied, an entry NEW in this change "
        "whose approved_by is this same identity is refused as "
        f"'{SELF_GRANTED_REASON}'. Omit for schema-only validation.",
    )
    parser.add_argument(
        "--base-file",
        default=None,
        help="OMN-17157: the grants file as it exists on the base ref, used "
        "to diff-scope --requester to entries this change ADDS. A missing or "
        "unreadable file fails CLOSED (every entry is treated as new).",
    )
    args = parser.parse_args(argv)

    file_path = Path(args.file)
    result = validate_grants(
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
        print(
            f"PASS: {file_path} — {result.entry_count} grant(s) validated successfully"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
