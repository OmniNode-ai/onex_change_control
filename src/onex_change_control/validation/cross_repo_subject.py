# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
# ruff: noqa: C901, E501, EM101, EM102, N818, PLR0912, PLR0913, PLR2004, RUF022, S105, S603, TC003, TC006, TRY003, TRY004, TRY301

"""Strict cross-repository receipt subjects and bounded GitHub validation.

The ordinary receipt schema is repository-local v1.  This module adds the
explicit v2 receipt lane for evidence that was run in another repository.  The
subject is an evidence reference only: it contains no authorization or
activation fields and this validator never returns a permission decision.

Two validation modes are deliberately separate:

* :func:`validate_offline` validates the legacy standalone subject shape and
  any bytes supplied by the caller, then returns ``UNEVALUATED``. It never
  calls GitHub.
* :class:`CrossRepoSubjectResolver` consumes an immutable GitHub snapshot from
  a bounded, injectable transport. Its online result is the only result that
  can be ``PASS`` for a canonical v2 receipt.

The product and OCC repositories are compiled allowlist values.  Repository
identity is never discovered from a checkout, a remote, a branch, a PR body,
or an arbitrary CLI argument.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, cast
from urllib.parse import quote

import yaml
from omnibase_core.enums.ticket.enum_receipt_status import EnumReceiptStatus
from omnibase_core.models.contracts.ticket.model_dod_receipt import ModelDodReceipt
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

PRODUCT_OWNER = "OmniNode-ai"
PRODUCT_NAME = "omnibase_infra"
PRODUCT_REPOSITORY = f"{PRODUCT_OWNER}/{PRODUCT_NAME}"
PRODUCT_REMOTE = f"https://github.com/{PRODUCT_REPOSITORY}.git"
OCC_OWNER = "OmniNode-ai"
OCC_NAME = "onex_change_control"
OCC_REPOSITORY = f"{OCC_OWNER}/{OCC_NAME}"
OCC_REMOTE = f"https://github.com/{OCC_REPOSITORY}.git"
CONTRACT_PATH = "contracts/OMN-17486.yaml"
SUBJECT_SCHEMA_VERSION = "occ-cross-repo-subject/v1"
SUBJECT_SCHEMA_VERSION_V2 = "occ-cross-repo-subject/v2"
SUBJECT_PURPOSE = "evidence_only"
MAX_HTTP_BODY_BYTES = 2_097_152
MAX_CONTRACT_BYTES = 1_048_576
MAX_ARTIFACT_BYTES = 1_048_576
MAX_RECEIPT_BYTES = 2_097_152
MAX_RECEIPT_AGGREGATE_BYTES = 128 * 1_048_576
MAX_RECEIPT_COUNT = 50_000
MAX_INPUT_DEPTH = 64
MAX_INPUT_NODES = 50_000
MAX_API_CALLS = 12
REQUEST_TIMEOUT_SECONDS = 5.0
TOTAL_TIMEOUT_SECONDS = 15.0
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REF_RE = re.compile(r"^[^\x00\r\n]+$")
_GITHUB_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class CrossRepoStatus(StrEnum):
    """The three states exposed by the offline/online validation matrix."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNEVALUATED = "UNEVALUATED"


class RepositoryIdentity(BaseModel):
    """A canonical, credential-free GitHub repository identity."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    github_owner: str = Field(min_length=1, max_length=100)
    github_name: str = Field(min_length=1, max_length=100)
    canonical_remote: str = Field(min_length=1, max_length=256)

    @property
    def full_name(self) -> str:
        """Return the case-sensitive GitHub owner/name pair."""

        return f"{self.github_owner}/{self.github_name}"

    @field_validator("github_owner", "github_name", "canonical_remote")
    @classmethod
    def _reject_control_characters(cls, value: str) -> str:
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
            raise ValueError("repository identity must not contain control characters")
        if not value.strip():
            raise ValueError("repository identity values must not be blank")
        return value

    @field_validator("github_owner", "github_name")
    @classmethod
    def _validate_github_component(cls, value: str) -> str:
        if _GITHUB_COMPONENT_RE.fullmatch(value) is None:
            raise ValueError("GitHub owner/name contains invalid characters")
        return value

    def model_post_init(self, __context: Any) -> None:
        expected = f"https://github.com/{self.full_name}.git"
        if self.canonical_remote != expected:
            raise ValueError(
                "canonical_remote must be the credential-free HTTPS GitHub URL "
                f"for github_owner/github_name ({expected!r})"
            )


class RepositoryRef(RepositoryIdentity):
    """A repository identity plus a descriptive ref and authoritative SHA."""

    ref: str = Field(min_length=1, max_length=256)
    sha: str

    @field_validator("ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        if _REF_RE.fullmatch(value) is None or not value.strip():
            raise ValueError("ref must be a bounded non-blank value without newlines")
        return value

    @field_validator("sha")
    @classmethod
    def _validate_sha(cls, value: str) -> str:
        if _SHA_RE.fullmatch(value) is None:
            raise ValueError("SHA must be exactly 40 lowercase hexadecimal characters")
        return value

    @property
    def repository_identity(self) -> RepositoryIdentity:
        """Return this ref's repository identity without ref coordinates."""

        return RepositoryIdentity(
            github_owner=self.github_owner,
            github_name=self.github_name,
            canonical_remote=self.canonical_remote,
        )


class PullRequestSubject(BaseModel):
    """Explicit PR target/base/head identity captured by a trusted runner."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    repository: RepositoryIdentity
    number: int = Field(ge=1, le=2_147_483_647)
    base: RepositoryRef
    head: RepositoryRef
    state: Literal["OPEN", "MERGED"]
    merge_commit_sha: str | None = None

    @field_validator("merge_commit_sha")
    @classmethod
    def _validate_merge_sha(cls, value: str | None) -> str | None:
        if value is not None and _SHA_RE.fullmatch(value) is None:
            raise ValueError(
                "merge_commit_sha must be exactly 40 lowercase hexadecimal characters"
            )
        return value

    def model_post_init(self, __context: Any) -> None:
        if self.repository.full_name != self.base.full_name:
            raise ValueError("PR base repository must equal the explicit PR repository")
        if self.state == "OPEN" and self.merge_commit_sha is not None:
            raise ValueError("OPEN PRs must not claim a merge commit")
        if self.state == "MERGED" and self.merge_commit_sha is None:
            raise ValueError("MERGED PRs require merge_commit_sha")


class RevisionSubject(BaseModel):
    """The immutable revision used as the provenance of a receipt."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kind: Literal["head", "merge_commit"]
    sha: str

    @field_validator("sha")
    @classmethod
    def _validate_sha(cls, value: str) -> str:
        if _SHA_RE.fullmatch(value) is None:
            raise ValueError("SHA must be exactly 40 lowercase hexadecimal characters")
        return value


class ContractSource(BaseModel):
    """Immutable OCC contract bytes and digest bindings."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    repository: RepositoryIdentity
    commit_sha: str
    path: str = Field(min_length=1, max_length=512)
    file_sha256: str
    entry_sha256: str

    @field_validator("commit_sha")
    @classmethod
    def _validate_commit_sha(cls, value: str) -> str:
        if _SHA_RE.fullmatch(value) is None:
            raise ValueError("contract commit SHA must be exactly 40 lowercase hex")
        return value

    @field_validator("file_sha256", "entry_sha256")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError(
                "digest must be sha256:<64 lowercase hexadecimal characters>"
            )
        return value

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        if value != CONTRACT_PATH:
            raise ValueError(f"path must be the canonical {CONTRACT_PATH} path")
        return value


class ArtifactSource(BaseModel):
    """Immutable product-repository location of the evidence artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    repository: RepositoryIdentity
    commit_sha: str
    path: str = Field(min_length=1, max_length=512)
    artifact_sha256: str

    @field_validator("commit_sha")
    @classmethod
    def _validate_commit_sha(cls, value: str) -> str:
        if _SHA_RE.fullmatch(value) is None:
            raise ValueError("artifact commit SHA must be exactly 40 lowercase hex")
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def _validate_artifact_digest(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("artifact_sha256 must be sha256:<64 lowercase hex>")
        return value

    @field_validator("path")
    @classmethod
    def _validate_artifact_path(cls, value: str) -> str:
        # The path is sent to GitHub's contents API, never interpolated into a
        # shell command. Keep it a strict relative POSIX path nevertheless so
        # traversal and alternate separators cannot cross the evidence root.
        if (
            value.startswith(("/", "\\"))
            or "\\" in value
            or "//" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
        ):
            raise ValueError("artifact path must be a bounded relative POSIX path")
        return value


class CrossRepoSubjectV2(BaseModel):
    """Canonical cross-repository subject carried by a v2 receipt.

    v1 remains available for reading old standalone design fixtures, but it is
    intentionally not accepted by :class:`CrossRepoReceipt`. Embedding the
    subject in this receipt model makes all identity and binding fields part of
    one validated object instead of an untrusted side channel.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal["occ-cross-repo-subject/v2"]
    purpose: Literal["evidence_only"]
    ticket_id: Literal["OMN-17486"]
    evidence_item_id: str = Field(min_length=1, max_length=200)
    check_type: Literal[
        "test_exists",
        "test_passes",
        "file_exists",
        "grep",
        "command",
        "endpoint",
        "behavior_proven",
        "semantic_grading",
    ]
    check_value: str = Field(min_length=1, max_length=10_000)
    repository: RepositoryIdentity
    pull_request: PullRequestSubject
    revision: RevisionSubject
    contract_source: ContractSource
    artifact_source: ArtifactSource

    def model_post_init(self, __context: Any) -> None:
        if self.repository != _product_identity():
            raise ValueError(
                "cross-repo product repository is not allowlisted; only "
                f"{PRODUCT_REPOSITORY} is supported"
            )
        if self.pull_request.repository != self.repository:
            raise ValueError("PR repository must equal the explicit PR repository")
        if self.pull_request.base.repository_identity != self.repository:
            raise ValueError("PR base repository must be the allowlisted product repo")
        if self.pull_request.head.repository_identity != self.repository:
            raise ValueError("fork PR heads are not permitted by the v2 product policy")
        if self.artifact_source.repository != self.repository:
            raise ValueError("artifact repository must be the allowlisted product repo")
        if self.artifact_source.commit_sha != self.revision.sha:
            raise ValueError("artifact commit SHA must equal the receipt revision SHA")
        if self.contract_source.repository != _occ_identity():
            raise ValueError(
                "contract_source repository must be the canonical OCC repository"
            )
        if self.revision.kind == "merge_commit":
            if self.pull_request.state != "MERGED":
                raise ValueError("merge_commit revision requires a MERGED PR")
            if self.revision.sha != self.pull_request.merge_commit_sha:
                raise ValueError("merge_commit revision must equal merge_commit_sha")
        elif self.revision.sha != self.pull_request.head.sha:
            raise ValueError("head revision must equal the explicit PR head SHA")


class CrossRepoReceipt(ModelDodReceipt):
    """Canonical v2 receipt for an immutable cross-repository evidence run."""

    schema_version: Literal["2.0.0"]
    cross_repo_subject: CrossRepoSubjectV2

    @model_validator(mode="after")
    def enforce_cross_repo_invariants(self) -> CrossRepoReceipt:
        subject = self.cross_repo_subject
        if self.status is not EnumReceiptStatus.PASS:
            raise ValueError("cross-repository receipts must have status PASS")
        if self.ticket_id != subject.ticket_id:
            raise ValueError("receipt ticket_id does not match cross-repo subject")
        if self.evidence_item_id != subject.evidence_item_id:
            raise ValueError(
                "receipt evidence_item_id does not match cross-repo subject"
            )
        if self.check_type != subject.check_type:
            raise ValueError("receipt check_type does not match cross-repo subject")
        if self.check_value != subject.check_value:
            raise ValueError("receipt check_value does not match cross-repo subject")
        if self.commit_sha != subject.revision.sha:
            raise ValueError("receipt commit_sha does not match subject revision")
        if self.contract_sha256 != subject.contract_source.file_sha256:
            raise ValueError("receipt contract_sha256 does not match subject binding")
        if self.contract_entry_sha256 != subject.contract_source.entry_sha256:
            raise ValueError(
                "receipt contract_entry_sha256 does not match subject binding"
            )
        if self.pr_number is not None and self.pr_number != subject.pull_request.number:
            raise ValueError("receipt pr_number does not match subject PR")
        return self


class CrossRepoReceiptSupersession(BaseModel):
    """Canonical supersession record whose replacement is a v2 receipt.

    ``omnibase_core`` owns the ordinary receipt supersession model. This
    companion is needed because that model intentionally knows only the v1
    receipt shape; it preserves the same append-only invariants for v2.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: str = Field(min_length=5, max_length=64)
    ticket_id: str = Field(pattern=r"^OMN-\d+$")
    evidence_item_id: str = Field(min_length=1, max_length=200)
    check_type: str = Field(min_length=1, max_length=100)
    supersedes: str = Field(min_length=1, max_length=1024)
    reason: str = Field(min_length=1, max_length=10_000)
    superseder: str = Field(min_length=1, max_length=200)
    created_at: datetime
    tombstone: bool = False
    replacement: CrossRepoReceipt | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        expected_target = (
            f"drift/dod_receipts/{self.ticket_id}/{self.evidence_item_id}/"
            f"{self.check_type}.yaml"
        )
        if self.supersedes != expected_target:
            raise ValueError(
                "supersedes must name the canonical base receipt for this key"
            )
        if self.tombstone and self.replacement is not None:
            raise ValueError("a tombstone supersession must not carry replacement")
        if not self.tombstone and self.replacement is None:
            raise ValueError("a non-tombstone supersession requires replacement")
        if self.replacement is not None and (
            self.replacement.ticket_id,
            self.replacement.evidence_item_id,
            self.replacement.check_type,
        ) != (self.ticket_id, self.evidence_item_id, self.check_type):
            raise ValueError("supersession replacement key does not match record key")
        if (
            self.replacement is not None
            and self.replacement.contract_entry_sha256 is None
        ):
            raise ValueError(
                "supersession replacement must carry a contract entry binding"
            )


CanonicalReceipt = ModelDodReceipt | CrossRepoReceipt


class CrossRepoSubject(BaseModel):
    """The versioned, evidence-only cross-repository subject."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal["occ-cross-repo-subject/v1"]
    purpose: Literal["evidence_only"]
    repository: RepositoryIdentity
    pull_request: PullRequestSubject
    revision: RevisionSubject
    contract_source: ContractSource
    artifact_sha256: str

    @field_validator("artifact_sha256")
    @classmethod
    def _validate_artifact_digest(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("artifact_sha256 must be sha256:<64 lowercase hex>")
        return value

    def model_post_init(self, __context: Any) -> None:
        if self.repository != _product_identity():
            raise ValueError(
                "cross-repo product repository is not allowlisted; only "
                f"{PRODUCT_REPOSITORY} is supported"
            )
        if self.pull_request.repository != self.repository:
            raise ValueError("PR repository must equal the cross-repo repository")
        if self.pull_request.base.repository_identity != self.repository:
            raise ValueError("PR base repository must be the allowlisted product repo")
        if (
            self.pull_request.head.github_owner != PRODUCT_OWNER
            or self.pull_request.head.github_name != PRODUCT_NAME
        ):
            raise ValueError("fork PR heads are not permitted by the v1 product policy")
        if self.pull_request.head.canonical_remote != PRODUCT_REMOTE:
            raise ValueError(
                "PR head canonical_remote must be the allowlisted product repo"
            )
        if self.contract_source.repository != _occ_identity():
            raise ValueError(
                "contract_source repository must be the canonical OCC repository"
            )
        if self.revision.kind == "merge_commit":
            if self.pull_request.state != "MERGED":
                raise ValueError("merge_commit revision requires a MERGED PR")
            if self.revision.sha != self.pull_request.merge_commit_sha:
                raise ValueError("merge_commit revision must equal merge_commit_sha")
        elif self.revision.sha != self.pull_request.head.sha:
            raise ValueError("head revision must equal the explicit PR head SHA")


def _product_identity() -> RepositoryIdentity:
    return RepositoryIdentity(
        github_owner=PRODUCT_OWNER,
        github_name=PRODUCT_NAME,
        canonical_remote=PRODUCT_REMOTE,
    )


def _occ_identity() -> RepositoryIdentity:
    return RepositoryIdentity(
        github_owner=OCC_OWNER,
        github_name=OCC_NAME,
        canonical_remote=OCC_REMOTE,
    )


@dataclass(frozen=True, slots=True)
class ApiResponse:
    """Minimal transport response; body is bounded before JSON parsing."""

    status_code: int
    headers: dict[str, str]
    body: bytes


class GitHubTransport(Protocol):
    """Injectable, read-only transport used by the bounded resolver."""

    def request(
        self, path: str, *, headers: dict[str, str], timeout: float
    ) -> ApiResponse: ...


class GhCliTransport:
    """Public GitHub REST transport through the ``gh api`` CLI.

    ``gh`` receives the job's read-only ``GH_TOKEN`` through its normal
    process environment.  This class never reads, prints, or stores token
    values and never accepts a repository argument from a caller.
    """

    def request(
        self, path: str, *, headers: dict[str, str], timeout: float
    ) -> ApiResponse:
        command = ["gh", "api", "--include", "--method", "GET"]
        for name, value in headers.items():
            command.extend(["--header", f"{name}: {value}"])
        command.append(path)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TransportError(f"GitHub request failed: {exc}") from exc
        if completed.returncode != 0:
            raise TransportError("gh api exited with a non-zero status")
        if len(completed.stdout) > MAX_HTTP_BODY_BYTES:
            raise TransportError("GitHub response exceeded the bounded body size")
        status, response_headers, body = _parse_http_response(completed.stdout)
        if status is None:
            raise TransportError("GitHub response did not contain an HTTP status")
        return ApiResponse(status, response_headers, body)


class TransportError(RuntimeError):
    """A transport failure that must never become a PASS result."""


@dataclass(frozen=True, slots=True)
class CrossRepoValidationResult:
    """A bounded, auditable outcome from either validation mode."""

    status: CrossRepoStatus
    details: tuple[str, ...] = ()
    api_calls: int = 0


def _validate_input_budget(value: object) -> None:
    """Reject oversized/deep model inputs before Pydantic walks them."""

    nodes = 0
    seen: set[int] = set()
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_INPUT_NODES:
            raise ValueError("receipt/subject input exceeds the object-count bound")
        if depth > MAX_INPUT_DEPTH:
            raise ValueError("receipt/subject input exceeds the nesting-depth bound")
        if isinstance(current, str):
            if len(current.encode("utf-8")) > MAX_HTTP_BODY_BYTES:
                raise ValueError("receipt/subject input contains an oversized string")
        elif isinstance(current, dict):
            object_id = id(current)
            if object_id in seen:
                continue
            seen.add(object_id)
            if len(current) > MAX_INPUT_NODES:
                raise ValueError("receipt/subject input contains too many mapping keys")
            for key, child in current.items():
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif isinstance(current, (list, tuple, set, frozenset)):
            object_id = id(current)
            if object_id in seen:
                continue
            seen.add(object_id)
            if len(current) > MAX_INPUT_NODES:
                raise ValueError("receipt/subject input contains too many list items")
            stack.extend((child, depth + 1) for child in current)


def parse_canonical_receipt(raw: object) -> CanonicalReceipt:
    """Parse a complete receipt under the explicit v1/v2 version contract.

    ``ModelDodReceipt`` remains the v1 same-repository model.  v2 is the only
    version allowed to carry a cross-repository subject, and that subject is
    parsed as a declared model field.  No caller may parse a receipt's
    cross-repo data by reaching into an arbitrary mapping.
    """

    _validate_input_budget(raw)
    if not isinstance(raw, dict):
        raise ValueError("receipt must contain a mapping")
    version = raw.get("schema_version")
    carries_subject = "cross_repo_subject" in raw
    if carries_subject and version != "2.0.0":
        raise ValueError(
            "cross_repo_subject is permitted only on canonical receipt schema v2"
        )
    if version == "2.0.0" and not carries_subject:
        raise ValueError("canonical receipt schema v2 requires cross_repo_subject")
    if version not in {"1.0.0", "2.0.0"}:
        raise ValueError("receipt schema_version must be exactly 1.0.0 or 2.0.0")
    try:
        if version == "2.0.0":
            return CrossRepoReceipt.model_validate(raw)
        return ModelDodReceipt.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid canonical receipt: {exc}") from exc


def _sha256_prefixed(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _compute_contract_entry_sha256(contract_data: object, evidence_item_id: str) -> str:
    """Mirror the canonical OCC per-entry contract hash algorithm."""

    if not isinstance(contract_data, dict):
        raise ValueError("contract bytes do not contain a mapping")
    items = contract_data.get("dod_evidence")
    if not isinstance(items, list):
        raise ValueError("contract has no dod_evidence list")
    entry = next(
        (
            item
            for item in items
            if isinstance(item, dict) and item.get("id") == evidence_item_id
        ),
        None,
    )
    if not isinstance(entry, dict):
        raise ValueError(f"contract evidence item {evidence_item_id!r} was not found")
    header = {key: contract_data.get(key) for key in ("ticket_id", "schema_version")}
    canonical = {"header": header, "entry": entry}
    blob = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return _sha256_prefixed(blob.encode("utf-8"))


def _validate_contract_identity(contract_data: object) -> None:
    """Require the pinned bytes to be the OMN-17486 contract, not just YAML."""

    if not isinstance(contract_data, dict):
        raise ValueError("contract bytes do not contain a mapping")
    if contract_data.get("ticket_id") != "OMN-17486":
        raise ValueError("contract bytes do not identify OMN-17486")


def _check_local_digests(
    subject: CrossRepoSubject,
    *,
    evidence_item_id: str | None,
    contract_bytes: bytes | None,
    artifact_bytes: bytes | None,
) -> tuple[str, ...]:
    """Check only caller-supplied bytes; never fetch a missing object."""

    errors: list[str] = []
    if contract_bytes is not None:
        if not isinstance(contract_bytes, bytes):
            errors.append("contract_bytes must be bytes")
        elif len(contract_bytes) > MAX_CONTRACT_BYTES:
            errors.append("contract bytes exceed the 1 MiB bound")
        elif _sha256_prefixed(contract_bytes) != subject.contract_source.file_sha256:
            errors.append("contract file digest does not match the subject")
        else:
            try:
                data = yaml.safe_load(contract_bytes)
                _validate_input_budget(data)
                _validate_contract_identity(data)
                actual = (
                    _compute_contract_entry_sha256(data, evidence_item_id)
                    if evidence_item_id is not None
                    else None
                )
            except (TypeError, ValueError, yaml.YAMLError) as exc:
                errors.append(
                    f"contract YAML/entry digest could not be computed: {exc}"
                )
            else:
                if (
                    evidence_item_id is not None
                    and actual != subject.contract_source.entry_sha256
                ):
                    errors.append("contract entry digest does not match the subject")
    if artifact_bytes is not None:
        if not isinstance(artifact_bytes, bytes):
            errors.append("artifact_bytes must be bytes")
        elif len(artifact_bytes) > MAX_ARTIFACT_BYTES:
            errors.append("artifact bytes exceed the 1 MiB bound")
        elif _sha256_prefixed(artifact_bytes) != subject.artifact_sha256:
            errors.append("artifact digest does not match the subject")
    return tuple(errors)


def validate_offline(
    raw_subject: object,
    *,
    evidence_item_id: str | None = None,
    contract_bytes: bytes | None = None,
    artifact_bytes: bytes | None = None,
) -> CrossRepoValidationResult:
    """Validate shape/local bytes without any network call.

    A valid subject remains ``UNEVALUATED`` because repository identity,
    current PR head, commit membership, and immutable remote bytes require the
    online snapshot.  Local digest mismatches are definitive failures.
    """

    try:
        _validate_input_budget(raw_subject)
        subject = CrossRepoSubject.model_validate(raw_subject)
    except (ValidationError, ValueError) as exc:
        return CrossRepoValidationResult(
            CrossRepoStatus.FAIL, (f"invalid cross-repo subject: {exc}",)
        )
    errors = _check_local_digests(
        subject,
        evidence_item_id=evidence_item_id,
        contract_bytes=contract_bytes,
        artifact_bytes=artifact_bytes,
    )
    if errors:
        return CrossRepoValidationResult(CrossRepoStatus.FAIL, errors)
    return CrossRepoValidationResult(
        CrossRepoStatus.UNEVALUATED,
        ("cross-repo subject requires the online GitHub snapshot",),
    )


class CrossRepoSubjectResolver:
    """Resolve exactly one subject within fixed request/call/deadline bounds."""

    def __init__(
        self,
        *,
        transport: GitHubTransport | None = None,
        request_timeout: float = REQUEST_TIMEOUT_SECONDS,
        total_timeout: float = TOTAL_TIMEOUT_SECONDS,
        max_calls: int = MAX_API_CALLS,
        monotonic: Any = time.monotonic,
        sleep: Any = time.sleep,
    ) -> None:
        if (
            isinstance(request_timeout, bool)
            or not isinstance(request_timeout, (int, float))
            or not math.isfinite(request_timeout)
            or request_timeout <= 0
            or request_timeout > REQUEST_TIMEOUT_SECONDS
        ):
            raise ValueError("request_timeout must be in (0, 5] seconds")
        if (
            isinstance(total_timeout, bool)
            or not isinstance(total_timeout, (int, float))
            or not math.isfinite(total_timeout)
            or total_timeout <= 0
            or total_timeout > TOTAL_TIMEOUT_SECONDS
        ):
            raise ValueError("total_timeout must be in (0, 15] seconds")
        if (
            isinstance(max_calls, bool)
            or not isinstance(max_calls, int)
            or max_calls <= 0
            or max_calls > MAX_API_CALLS
        ):
            raise ValueError("max_calls must be in [1, 12]")
        self._transport = transport if transport is not None else GhCliTransport()
        self._request_timeout = request_timeout
        self._total_timeout = total_timeout
        self._max_calls = max_calls
        self._monotonic = monotonic
        self._sleep = sleep
        self._calls = 0
        self._deadline = 0.0
        self._retry_used = False
        self._batch_active = False
        self._immutable_cache: dict[str, ApiResponse] = {}

    @property
    def api_calls(self) -> int:
        """Number of transport calls launched by this resolver."""

        return self._calls

    def start_batch(self) -> None:
        """Start one bounded resolution batch.

        The CLI uses one resolver for all effective receipts.  Consequently
        the 12-call budget, 15-second deadline, cache, and single retry are
        global to the job rather than reset once per receipt.
        """

        self._calls = 0
        self._deadline = self._monotonic() + self._total_timeout
        self._retry_used = False
        self._batch_active = True
        self._immutable_cache.clear()

    def resolve(
        self,
        raw_subject: CrossRepoSubject | object,
        *,
        evidence_item_id: str,
        artifact_bytes: bytes,
        contract_bytes: bytes | None = None,
    ) -> CrossRepoValidationResult:
        """Return an online PASS only after every immutable binding succeeds."""

        try:
            _validate_input_budget(raw_subject)
            subject = (
                raw_subject
                if isinstance(raw_subject, CrossRepoSubject)
                else CrossRepoSubject.model_validate(raw_subject)
            )
        except (ValidationError, ValueError) as exc:
            return CrossRepoValidationResult(
                CrossRepoStatus.FAIL, (f"invalid cross-repo subject: {exc}",)
            )
        if not isinstance(artifact_bytes, bytes):
            return CrossRepoValidationResult(
                CrossRepoStatus.FAIL,
                ("artifact_bytes must be supplied for online validation",),
            )
        local_errors = _check_local_digests(
            subject,
            evidence_item_id=evidence_item_id,
            contract_bytes=contract_bytes,
            artifact_bytes=artifact_bytes,
        )
        if local_errors:
            return CrossRepoValidationResult(CrossRepoStatus.FAIL, local_errors)
        self.start_batch()
        return self._resolve_subject(
            subject,
            evidence_item_id=evidence_item_id,
            artifact_bytes=artifact_bytes,
            strict_api_objects=False,
        )

    def resolve_receipt(
        self, raw_receipt: CrossRepoReceipt | object
    ) -> CrossRepoValidationResult:
        """Resolve a canonical v2 receipt and fetch its artifact remotely."""

        try:
            _validate_input_budget(raw_receipt)
            receipt = (
                raw_receipt
                if isinstance(raw_receipt, CrossRepoReceipt)
                else parse_canonical_receipt(raw_receipt)
            )
            if not isinstance(receipt, CrossRepoReceipt):
                raise ValueError(
                    "cross-repository online validation requires a canonical v2 receipt"
                )
        except (ValidationError, ValueError) as exc:
            return CrossRepoValidationResult(
                CrossRepoStatus.FAIL, (str(exc),), api_calls=self._calls
            )

        if not self._batch_active:
            self.start_batch()
        try:
            subject = receipt.cross_repo_subject
            self._verify_pr(subject)
            self._verify_commit(subject.revision.sha)
            self._verify_pr_membership(subject, subject.revision.sha)
            artifact = self._fetch_artifact(subject)
            if _sha256_prefixed(artifact) != subject.artifact_source.artifact_sha256:
                raise ResolutionFailure("immutable artifact digest did not match")
            fetched_contract = self._fetch_contract(subject, strict_api_object=True)
            self._verify_contract_bytes(
                subject,
                receipt.evidence_item_id,
                fetched_contract,
                expected_check_type=receipt.check_type,
                expected_check_value=receipt.check_value,
            )
            if subject.revision.kind == "merge_commit":
                self._verify_merge_ancestry(subject)
        except (ResolutionFailure, ValidationError, ValueError) as exc:
            return CrossRepoValidationResult(
                CrossRepoStatus.FAIL, (str(exc),), api_calls=self._calls
            )
        return CrossRepoValidationResult(CrossRepoStatus.PASS, api_calls=self._calls)

    def _resolve_subject(
        self,
        subject: CrossRepoSubject,
        *,
        evidence_item_id: str,
        artifact_bytes: bytes,
        strict_api_objects: bool,
    ) -> CrossRepoValidationResult:
        try:
            local_errors = _check_local_digests(
                subject,
                evidence_item_id=evidence_item_id,
                contract_bytes=None,
                artifact_bytes=artifact_bytes,
            )
            if local_errors:
                return CrossRepoValidationResult(CrossRepoStatus.FAIL, local_errors)
            self._verify_pr(subject)
            self._verify_commit(subject.revision.sha)
            self._verify_pr_membership(subject, subject.revision.sha)
            fetched_contract = self._fetch_contract(
                subject, strict_api_object=strict_api_objects
            )
            self._verify_contract_bytes(subject, evidence_item_id, fetched_contract)
            if subject.revision.kind == "merge_commit":
                self._verify_merge_ancestry(subject)
        except (ResolutionFailure, ValidationError) as exc:
            return CrossRepoValidationResult(
                CrossRepoStatus.FAIL, (str(exc),), api_calls=self._calls
            )
        return CrossRepoValidationResult(CrossRepoStatus.PASS, api_calls=self._calls)

    def _verify_pr(self, subject: CrossRepoSubject | CrossRepoSubjectV2) -> None:
        payload = self._json_get(
            f"repos/{PRODUCT_REPOSITORY}/pulls/{subject.pull_request.number}",
            immutable=False,
        )
        if not isinstance(payload, dict):
            raise ResolutionFailure("GitHub PR response was not an object")
        required_fields = {
            "number",
            "state",
            "merged_at",
            "merge_commit_sha",
            "base",
            "head",
        }
        if not required_fields.issubset(payload):
            raise ResolutionFailure("GitHub PR response omitted required metadata")
        if payload.get("number") != subject.pull_request.number:
            raise ResolutionFailure("GitHub PR number did not match the subject")
        base = _api_repo_ref(payload.get("base"), "base")
        head = _api_repo_ref(payload.get("head"), "head")
        declared = subject.pull_request
        if _api_identity(base) != declared.repository:
            raise ResolutionFailure(
                "GitHub PR base repository did not match the subject"
            )
        if base["ref"] != declared.base.ref or base["sha"] != declared.base.sha:
            raise ResolutionFailure("GitHub PR base ref/SHA did not match the subject")
        if _api_identity(head) != declared.head.repository_identity:
            raise ResolutionFailure(
                "GitHub PR head repository did not match the subject"
            )
        if head["ref"] != declared.head.ref or head["sha"] != declared.head.sha:
            raise ResolutionFailure("GitHub PR head ref/SHA did not match the subject")
        observed_state = _observed_pr_state(payload)
        if observed_state != declared.state:
            raise ResolutionFailure("GitHub PR state did not match the subject")
        observed_merge = payload.get("merge_commit_sha")
        if declared.state == "OPEN":
            if observed_merge is not None or declared.merge_commit_sha is not None:
                raise ResolutionFailure("open PR cannot claim a merge commit")
        elif observed_merge != declared.merge_commit_sha:
            raise ResolutionFailure("GitHub merge commit did not match the subject")

    def _verify_commit(self, sha: str) -> None:
        payload = self._json_get(
            f"repos/{PRODUCT_REPOSITORY}/git/commits/{quote(sha, safe='')}",
            immutable=True,
        )
        if not isinstance(payload, dict):
            raise ResolutionFailure("revision commit response was not an object")
        if payload.get("sha") != sha:
            raise ResolutionFailure("revision SHA was not returned by the product repo")

    def _verify_pr_membership(
        self, subject: CrossRepoSubject | CrossRepoSubjectV2, sha: str
    ) -> None:
        payload = self._json_get(
            f"repos/{PRODUCT_REPOSITORY}/commits/{quote(sha, safe='')}/pulls",
            immutable=True,
        )
        if not isinstance(payload, list):
            raise ResolutionFailure("commit-to-PR membership response was malformed")
        for item in payload:
            if (
                not isinstance(item, dict)
                or item.get("number") != subject.pull_request.number
            ):
                continue
            base = item.get("base")
            if (
                isinstance(base, dict)
                and _api_identity_from_repo(base.get("repo")) == subject.repository
            ):
                return
        raise ResolutionFailure("revision is not a member of the declared product PR")

    def _fetch_artifact(self, subject: CrossRepoSubjectV2) -> bytes:
        """Fetch the exact immutable artifact named by a v2 subject."""

        source = subject.artifact_source
        payload = self._json_get(
            f"repos/{source.repository.full_name}/contents/"
            f"{quote(source.path, safe='/')}?ref={quote(source.commit_sha, safe='')}",
            immutable=True,
        )
        if not isinstance(payload, dict):
            raise ResolutionFailure("artifact contents response was not an object")
        if payload.get("tombstone") is True:
            raise ResolutionFailure("artifact contents response was tombstoned")
        if payload.get("path") != source.path:
            raise ResolutionFailure("artifact contents response path did not match")
        if payload.get("type") != "file":
            raise ResolutionFailure(
                "artifact contents object must be a regular file (symlink and "
                "submodule objects are rejected)"
            )
        if any(
            key in payload
            for key in ("target", "submodule_git_url", "submodule", "symlink")
        ):
            raise ResolutionFailure("artifact contents object carried link metadata")
        if payload.get("encoding") != "base64":
            raise ResolutionFailure("artifact contents response was not base64")
        encoded = payload.get("content")
        if not isinstance(encoded, str) or len(encoded) > (MAX_ARTIFACT_BYTES * 2):
            raise ResolutionFailure(
                "artifact contents response exceeded the size bound"
            )
        encoded = re.sub(r"\s+", "", encoded)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (UnicodeError, ValueError, binascii.Error) as exc:
            raise ResolutionFailure("artifact contents were not valid base64") from exc
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ResolutionFailure("artifact bytes exceeded the 1 MiB bound")
        return data

    def _fetch_contract(
        self, subject: CrossRepoSubject | CrossRepoSubjectV2, *, strict_api_object: bool
    ) -> bytes:
        source = subject.contract_source
        self._verify_contract_commit(source.commit_sha)
        if strict_api_object:
            self._verify_contract_reachability(source.commit_sha)
        payload = self._json_get(
            f"repos/{OCC_REPOSITORY}/contents/{quote(source.path, safe='/')}?ref={quote(source.commit_sha, safe='')}",
            immutable=True,
        )
        if not isinstance(payload, dict):
            raise ResolutionFailure("contract contents response was not an object")
        if strict_api_object and payload.get("type") != "file":
            raise ResolutionFailure(
                "contract contents object must be a regular file (symlink and "
                "submodule objects are rejected)"
            )
        if strict_api_object and (
            payload.get("tombstone") is True
            or any(
                key in payload
                for key in ("target", "submodule_git_url", "submodule", "symlink")
            )
        ):
            raise ResolutionFailure("contract contents object carried link metadata")
        if payload.get("path") != source.path or payload.get("encoding") != "base64":
            raise ResolutionFailure(
                "contract contents response was not the requested file"
            )
        encoded = payload.get("content")
        if not isinstance(encoded, str) or len(encoded) > (MAX_CONTRACT_BYTES * 2):
            raise ResolutionFailure(
                "contract contents response exceeded the size bound"
            )
        encoded = re.sub(r"\s+", "", encoded)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (UnicodeError, ValueError, binascii.Error) as exc:
            raise ResolutionFailure("contract contents were not valid base64") from exc
        if len(data) > MAX_CONTRACT_BYTES:
            raise ResolutionFailure("contract bytes exceeded the 1 MiB bound")
        return data

    def _verify_contract_commit(self, sha: str) -> None:
        payload = self._json_get(
            f"repos/{OCC_REPOSITORY}/git/commits/{quote(sha, safe='')}",
            immutable=True,
        )
        if not isinstance(payload, dict):
            raise ResolutionFailure("contract commit response was not an object")
        if payload.get("sha") != sha:
            raise ResolutionFailure("contract commit did not exist in the OCC repo")

    def _verify_contract_reachability(self, sha: str) -> None:
        """Prove the pinned OCC contract commit is on canonical ``dev``."""

        payload = self._json_get(
            f"repos/{OCC_REPOSITORY}/compare/{quote(sha, safe='')}...dev",
            immutable=True,
        )
        if not isinstance(payload, dict):
            raise ResolutionFailure("contract reachability response was not an object")
        if payload.get("status") not in {"identical", "ahead"}:
            raise ResolutionFailure(
                "pinned OCC contract commit is not reachable from canonical dev"
            )
        base_commit = payload.get("base_commit")
        merge_base_commit = payload.get("merge_base_commit")
        if not isinstance(base_commit, dict) or base_commit.get("sha") != sha:
            raise ResolutionFailure(
                "contract reachability response did not bind the pinned commit"
            )
        if (
            not isinstance(merge_base_commit, dict)
            or merge_base_commit.get("sha") != sha
        ):
            raise ResolutionFailure(
                "contract reachability response did not prove ancestor binding"
            )

    def _verify_contract_bytes(
        self,
        subject: CrossRepoSubject | CrossRepoSubjectV2,
        evidence_item_id: str,
        data: bytes,
        *,
        expected_check_type: str | None = None,
        expected_check_value: str | None = None,
    ) -> None:
        source = subject.contract_source
        if _sha256_prefixed(data) != source.file_sha256:
            raise ResolutionFailure("immutable contract file digest did not match")
        try:
            parsed = yaml.safe_load(data)
            _validate_input_budget(parsed)
            _validate_contract_identity(parsed)
            actual = _compute_contract_entry_sha256(parsed, evidence_item_id)
            if expected_check_type is not None or expected_check_value is not None:
                if not isinstance(parsed, dict):
                    raise ValueError("contract bytes do not contain a mapping")
                entries = parsed.get("dod_evidence")
                if not isinstance(entries, list):
                    raise ValueError("contract has no dod_evidence list")
                entry = next(
                    (
                        item
                        for item in entries
                        if isinstance(item, dict) and item.get("id") == evidence_item_id
                    ),
                    None,
                )
                if not isinstance(entry, dict):
                    raise ValueError("contract evidence item was not found")
                checks = entry.get("checks")
                if not isinstance(checks, list):
                    raise ValueError("contract evidence item has no checks list")
                if not any(
                    isinstance(check, dict)
                    and check.get("check_type") == expected_check_type
                    and check.get("check_value") == expected_check_value
                    for check in checks
                ):
                    raise ValueError(
                        "receipt check_type/check_value did not match the bound contract"
                    )
        except (TypeError, ValueError, yaml.YAMLError) as exc:
            raise ResolutionFailure(
                f"immutable contract could not be parsed: {exc}"
            ) from exc
        if actual != source.entry_sha256:
            raise ResolutionFailure("immutable contract entry digest did not match")

    def _verify_merge_ancestry(
        self, subject: CrossRepoSubject | CrossRepoSubjectV2
    ) -> None:
        merge_sha = subject.revision.sha
        durable_base = subject.pull_request.base.sha
        payload = self._json_get(
            f"repos/{PRODUCT_REPOSITORY}/compare/{quote(merge_sha, safe='')}...{quote(durable_base, safe='')}",
            immutable=True,
        )
        if not isinstance(payload, dict):
            raise ResolutionFailure("compare response was not an object")
        if payload.get("status") not in {"identical", "ahead"}:
            raise ResolutionFailure(
                "merge commit was not equal to or an ancestor of the durable base"
            )
        base_commit = payload.get("base_commit")
        merge_base_commit = payload.get("merge_base_commit")
        if not isinstance(base_commit, dict) or base_commit.get("sha") != merge_sha:
            raise ResolutionFailure(
                "compare response base commit did not match merge commit"
            )
        if (
            not isinstance(merge_base_commit, dict)
            or merge_base_commit.get("sha") != merge_sha
        ):
            raise ResolutionFailure("compare response did not prove merge ancestry")

    def _json_get(self, path: str, *, immutable: bool) -> dict[str, Any] | list[Any]:
        if self._monotonic() >= self._deadline:
            raise ResolutionFailure(
                "cross-repo validation exceeded the 15 second deadline"
            )
        cached = self._immutable_cache.get(path) if immutable else None
        headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
        if cached is not None and (etag := cached.headers.get("etag")):
            headers["If-None-Match"] = etag
        response = self._request(path, headers=headers)
        if response.status_code == 304:
            if cached is None:
                raise ResolutionFailure("GitHub returned 304 without a cached object")
            response = cached
        if response.status_code != 200:
            raise ResolutionFailure(f"GitHub API returned HTTP {response.status_code}")
        try:
            value = json.loads(response.body)
        except (RecursionError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResolutionFailure("GitHub API returned malformed JSON") from exc
        if not isinstance(value, (dict, list)):
            raise ResolutionFailure("GitHub API returned an unexpected JSON shape")
        if immutable:
            self._immutable_cache[path] = response
        return value

    def _request(self, path: str, *, headers: dict[str, str]) -> ApiResponse:
        while True:
            if self._calls >= self._max_calls:
                raise ResolutionFailure(
                    "cross-repo validation exceeded the 12-call budget"
                )
            remaining = self._deadline - self._monotonic()
            if remaining <= 0:
                raise ResolutionFailure(
                    "cross-repo validation exceeded the 15 second deadline"
                )
            self._calls += 1
            try:
                response = self._transport.request(
                    path,
                    headers=headers,
                    timeout=min(self._request_timeout, remaining),
                )
            except (OSError, TimeoutError, TransportError) as exc:
                raise ResolutionFailure(str(exc)) from exc
            except Exception as exc:
                raise ResolutionFailure("GitHub transport failed") from exc
            if self._monotonic() >= self._deadline:
                raise ResolutionFailure(
                    "cross-repo validation exceeded the 15 second deadline"
                )
            if (
                isinstance(response.status_code, bool)
                or not isinstance(response.status_code, int)
                or not isinstance(response.headers, dict)
                or any(
                    not isinstance(key, str)
                    or not isinstance(value, str)
                    or len(key) > 256
                    or len(value) > 4096
                    or any(
                        ord(char) < 0x20 or ord(char) == 0x7F
                        for char in f"{key}{value}"
                    )
                    for key, value in response.headers.items()
                )
            ):
                raise ResolutionFailure("GitHub response envelope was malformed")
            if not isinstance(response.body, bytes):
                raise ResolutionFailure("GitHub response body was malformed")
            if len(response.body) > MAX_HTTP_BODY_BYTES:
                raise ResolutionFailure(
                    "GitHub response exceeded the bounded body size"
                )
            if response.status_code == 429 and not self._retry_used:
                retry_after = response.headers.get("retry-after")
                if retry_after is None:
                    raise ResolutionFailure("GitHub API rate limit exceeded")
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    raise ResolutionFailure("GitHub API rate limit exceeded") from None
                if (
                    not math.isfinite(delay)
                    or delay < 0
                    or delay > min(1.0, self._deadline - self._monotonic())
                ):
                    raise ResolutionFailure("GitHub API rate limit exceeded")
                if delay:
                    self._sleep(delay)
                self._retry_used = True
                continue
            return response


class ResolutionFailure(RuntimeError):
    """Internal fail-closed result used for all API/mismatch failures."""


def _api_repo_ref(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResolutionFailure(f"GitHub {label} repository metadata was missing")
    repo = value.get("repo")
    if not isinstance(repo, dict):
        raise ResolutionFailure(f"GitHub {label} repository metadata was missing")
    owner = repo.get("owner")
    if not isinstance(owner, dict) or not isinstance(owner.get("login"), str):
        raise ResolutionFailure(f"GitHub {label} owner metadata was malformed")
    if not isinstance(repo.get("name"), str) or not isinstance(value.get("ref"), str):
        raise ResolutionFailure(f"GitHub {label} metadata was malformed")
    sha = value.get("sha")
    if not isinstance(sha, str) or _SHA_RE.fullmatch(sha) is None:
        raise ResolutionFailure(f"GitHub {label} SHA metadata was malformed")
    return {
        "owner": owner["login"],
        "name": repo["name"],
        "canonical_remote": _api_remote(repo, owner["login"], repo["name"], label),
        "ref": value["ref"],
        "sha": sha,
    }


def _api_identity(value: dict[str, Any]) -> RepositoryIdentity:
    owner = cast(str, value["owner"])
    name = cast(str, value["name"])
    return RepositoryIdentity(
        github_owner=owner,
        github_name=name,
        canonical_remote=value["canonical_remote"],
    )


def _api_identity_from_repo(value: object) -> RepositoryIdentity:
    if not isinstance(value, dict):
        raise ResolutionFailure("GitHub commit-to-PR membership repository was missing")
    owner_data = value.get("owner")
    owner = owner_data.get("login") if isinstance(owner_data, dict) else None
    name = value.get("name")
    if not isinstance(owner, str) or not isinstance(name, str):
        raise ResolutionFailure(
            "GitHub commit-to-PR membership repository was malformed"
        )
    return RepositoryIdentity(
        github_owner=owner,
        github_name=name,
        canonical_remote=_api_remote(value, owner, name, "membership"),
    )


def _api_remote(repo: dict[str, Any], owner: str, name: str, label: str) -> str:
    expected = f"https://github.com/{owner}/{name}.git"
    supplied = repo.get("clone_url")
    if supplied is None:
        supplied = repo.get("html_url")
        if isinstance(supplied, str) and not supplied.endswith(".git"):
            supplied = f"{supplied}.git"
    if not isinstance(supplied, str) or supplied != expected:
        raise ResolutionFailure(
            f"GitHub {label} repository URL did not match its identity"
        )
    return expected


def _observed_pr_state(payload: dict[str, Any]) -> str:
    state = payload.get("state")
    merged_at = payload.get("merged_at")
    if merged_at is not None and (not isinstance(merged_at, str) or not merged_at):
        raise ResolutionFailure("GitHub PR merged_at metadata was malformed")
    if state == "open" and merged_at is None:
        return "OPEN"
    if state == "closed" and merged_at is not None:
        return "MERGED"
    raise ResolutionFailure("GitHub PR was closed-unmerged or had malformed state")


def _parse_http_response(stdout: bytes) -> tuple[int | None, dict[str, str], bytes]:
    """Parse the final ``gh api --include`` response without trusting output."""

    status_matches = list(re.finditer(rb"(?m)^HTTP/\S+\s+(\d{3})\b", stdout))
    if status_matches:
        match = status_matches[-1]
        separator = re.search(rb"\r?\n\r?\n", stdout[match.end() :])
        if separator is None:
            return int(match.group(1)), {}, b""
        separator_start = match.end() + separator.start()
        separator_end = match.end() + separator.end()
        header_block = stdout[match.start() : separator_start]
        response_headers: dict[str, str] = {}
        for line in header_block.splitlines()[1:]:
            if b":" not in line:
                continue
            name, value = line.split(b":", 1)
            response_headers[name.decode("ascii", "ignore").strip().lower()] = (
                value.decode("ascii", "ignore").strip()
            )
        return int(match.group(1)), response_headers, stdout[separator_end:]
    return None, {}, b""


__all__ = [
    "ApiResponse",
    "ArtifactSource",
    "CanonicalReceipt",
    "ContractSource",
    "CrossRepoReceipt",
    "CrossRepoStatus",
    "CrossRepoSubject",
    "CrossRepoSubjectV2",
    "CrossRepoSubjectResolver",
    "CrossRepoValidationResult",
    "CONTRACT_PATH",
    "GhCliTransport",
    "GitHubTransport",
    "PRODUCT_NAME",
    "PRODUCT_OWNER",
    "PRODUCT_REPOSITORY",
    "PRODUCT_REMOTE",
    "RepositoryIdentity",
    "RepositoryRef",
    "RevisionSubject",
    "PullRequestSubject",
    "SUBJECT_SCHEMA_VERSION",
    "SUBJECT_SCHEMA_VERSION_V2",
    "SUBJECT_PURPOSE",
    "parse_canonical_receipt",
    "validate_offline",
]
