# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
# ruff: noqa: C901, E501, EM101, EM102, N818, PLR0912, PLR0913, PLR0915, PLR2004, S105, S603, TRY003, TRY004, TRY301

"""Bounded, fail-closed validation for the canonical cross-repository subject.

``omnibase_core`` owns ``ModelCrossRepoSubject``.  OCC deliberately does not
vendor a second copy of that model: the import is resolved at runtime so this
repository can be released before the core model is published.  Until the
model is importable, offline validation may only return ``UNEVALUATED`` and
online validation fails closed.

The online resolver consumes one immutable, injectable GitHub snapshot.  It
uses only the fixed public repositories, exact commit SHAs and explicit PR
identity in the subject.  No branch, checkout, PR prose, or caller-supplied
repository can widen the authority boundary.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib
import json
import math
import re
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol
from urllib.parse import quote

import yaml
from omnibase_core.enums.ticket.enum_receipt_status import EnumReceiptStatus
from omnibase_core.models.contracts.ticket.model_dod_receipt import ModelDodReceipt
from pydantic import ValidationError

PRODUCT_OWNER = "OmniNode-ai"
PRODUCT_NAME = "omnibase_infra"
PRODUCT_REPOSITORY = f"{PRODUCT_OWNER}/{PRODUCT_NAME}"
PRODUCT_REMOTE = f"https://github.com/{PRODUCT_REPOSITORY}.git"
OCC_OWNER = "OmniNode-ai"
OCC_NAME = "onex_change_control"
OCC_REPOSITORY = f"{OCC_OWNER}/{OCC_NAME}"
OCC_REMOTE = f"https://github.com/{OCC_REPOSITORY}.git"
SUBJECT_SCHEMA_VERSION = "occ-cross-repo-subject/v1"
SUBJECT_PURPOSE = "evidence_only"

MAX_HTTP_BODY_BYTES = 2_097_152
MAX_CONTRACT_BYTES = 1_048_576
MAX_RECEIPT_BYTES = 2_097_152
MAX_RECEIPT_AGGREGATE_BYTES = 128 * 1_048_576
MAX_RECEIPT_COUNT = 50_000
MAX_INPUT_DEPTH = 64
MAX_INPUT_NODES = 50_000
MAX_GITHUB_COMPONENT_LENGTH = 100
MAX_CANONICAL_REMOTE_LENGTH = 256
MAX_REF_LENGTH = 256
MAX_CONTRACT_PATH_LENGTH = 512
MAX_API_CALLS = 12
REQUEST_TIMEOUT_SECONDS = 5.0
TOTAL_TIMEOUT_SECONDS = 15.0

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GITHUB_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_REF_FORBIDDEN_RE = re.compile(r"[\x00-\x20\x7f~^:?*\\\[]")
_SUBJECT_MODEL_MODULE = "omnibase_core.models.contracts.ticket.model_cross_repo_subject"


class CrossRepoStatus(StrEnum):
    """Result states shared by offline and online validation."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNEVALUATED = "UNEVALUATED"


class CanonicalSubjectUnavailable(RuntimeError):
    """The released core dependency does not yet expose the subject model."""


class ResolutionFailure(RuntimeError):
    """An online mismatch or transport failure that can never become PASS."""


@dataclass(frozen=True, slots=True)
class ApiResponse:
    """Minimal bounded transport response."""

    status_code: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class CrossRepoValidationResult:
    """Auditable bounded outcome."""

    status: CrossRepoStatus
    details: tuple[str, ...] = ()
    api_calls: int = 0


@dataclass(frozen=True, slots=True)
class ParsedCrossRepoReceipt:
    """A legacy receipt plus its canonical core-owned subject model."""

    receipt: ModelDodReceipt
    subject: object


@dataclass(frozen=True, slots=True)
class _RepositoryView:
    owner: str
    name: str
    remote: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True)
class _RefView:
    repository: _RepositoryView
    ref: str
    sha: str


@dataclass(frozen=True, slots=True)
class _PullRequestView:
    repository: _RepositoryView
    number: int
    base: _RefView
    head: _RefView
    state: Literal["OPEN", "MERGED"]
    merge_commit_sha: str | None


@dataclass(frozen=True, slots=True)
class _SubjectView:
    repository: _RepositoryView
    pull_request: _PullRequestView
    revision_kind: Literal["head", "merge_commit"]
    revision_sha: str
    contract_repository: _RepositoryView
    contract_commit_sha: str
    contract_path: str
    contract_file_sha256: str
    contract_entry_sha256: str
    artifact_sha256: str


def _load_subject_model() -> object | None:
    """Load the core model without turning an unreleased dependency into one."""

    try:
        module = importlib.import_module(_SUBJECT_MODEL_MODULE)
    except (ImportError, ModuleNotFoundError):
        return None
    model = getattr(module, "ModelCrossRepoSubject", None)
    return model if callable(getattr(model, "model_validate", None)) else None


def _model_validate_subject(raw: object, subject_model: object | None) -> object:
    model = subject_model if subject_model is not None else _load_subject_model()
    if model is None:
        raise CanonicalSubjectUnavailable(
            "omnibase_core ModelCrossRepoSubject is unavailable; install the "
            "released core version containing OMN-17580 before online validation"
        )
    validator = getattr(model, "model_validate", None)
    if not callable(validator):
        raise CanonicalSubjectUnavailable(
            "canonical subject model does not expose model_validate"
        )
    try:
        return validator(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid canonical cross-repo subject: {exc}") from exc


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _sha(value: object, field_name: str) -> str:
    value_text = _text(value, field_name)
    if _SHA_RE.fullmatch(value_text) is None:
        raise ValueError(
            f"{field_name} must be exactly 40 lowercase hexadecimal characters"
        )
    return value_text


def _digest(value: object, field_name: str) -> str:
    value_text = _text(value, field_name)
    if _DIGEST_RE.fullmatch(value_text) is None:
        raise ValueError(f"{field_name} must be sha256:<64 lowercase hex>")
    return value_text


def _repository(value: object, field_name: str) -> _RepositoryView:
    if value is None:
        raise ValueError(f"{field_name} must be an object")
    owner = _text(getattr(value, "github_owner", None), f"{field_name}.github_owner")
    name = _text(getattr(value, "github_name", None), f"{field_name}.github_name")
    remote = _text(
        getattr(value, "canonical_remote", None), f"{field_name}.canonical_remote"
    )
    if (
        _GITHUB_COMPONENT_RE.fullmatch(owner) is None
        or _GITHUB_COMPONENT_RE.fullmatch(name) is None
        or len(owner) > MAX_GITHUB_COMPONENT_LENGTH
        or len(name) > MAX_GITHUB_COMPONENT_LENGTH
    ):
        raise ValueError(f"{field_name} contains an invalid GitHub owner/name")
    if len(remote) > MAX_CANONICAL_REMOTE_LENGTH or any(
        ord(char) < 0x20 or ord(char) == 0x7F for char in remote
    ):
        raise ValueError(f"{field_name}.canonical_remote contains a control character")
    expected = f"https://github.com/{owner}/{name}.git"
    if remote != expected:
        raise ValueError(
            f"{field_name}.canonical_remote must equal the credential-free "
            f"canonical GitHub URL {expected!r}"
        )
    return _RepositoryView(owner, name, remote)


def _ref(value: object, field_name: str) -> _RefView:
    repository = _repository(value, field_name)
    ref = _text(getattr(value, "ref", None), f"{field_name}.ref")
    sha = _sha(getattr(value, "sha", None), f"{field_name}.sha")
    if (
        len(ref) > MAX_REF_LENGTH
        or not ref.strip()
        or ref != ref.strip()
        or _REF_FORBIDDEN_RE.search(ref)
    ):
        raise ValueError(f"{field_name}.ref must be a bounded non-whitespace Git ref")
    return _RefView(repository, ref, sha)


def _subject_view(subject: object) -> _SubjectView:
    if (
        _text(getattr(subject, "schema_version", None), "schema_version")
        != SUBJECT_SCHEMA_VERSION
    ):
        raise ValueError("subject schema_version is not occ-cross-repo-subject/v1")
    if _text(getattr(subject, "purpose", None), "purpose") != SUBJECT_PURPOSE:
        raise ValueError("subject purpose must be evidence_only")

    repository = _repository(getattr(subject, "repository", None), "repository")
    if repository != _RepositoryView(PRODUCT_OWNER, PRODUCT_NAME, PRODUCT_REMOTE):
        raise ValueError("subject repository is not the compiled product allowlist")

    pull_request_object = getattr(subject, "pull_request", None)
    pull_request_repository = _repository(
        getattr(pull_request_object, "repository", None), "pull_request.repository"
    )
    number = getattr(pull_request_object, "number", None)
    if (
        isinstance(number, bool)
        or not isinstance(number, int)
        or not 1 <= number <= 2_147_483_647
    ):
        raise ValueError("pull_request.number must be a bounded positive integer")
    base = _ref(getattr(pull_request_object, "base", None), "pull_request.base")
    head = _ref(getattr(pull_request_object, "head", None), "pull_request.head")
    if pull_request_repository != repository or base.repository != repository:
        raise ValueError(
            "PR repository and base repository must equal the product repository"
        )
    if head.repository != repository:
        raise ValueError("fork PR heads are not permitted by the product policy")
    state = _text(getattr(pull_request_object, "state", None), "pull_request.state")
    if state not in {"OPEN", "MERGED"}:
        raise ValueError("pull_request.state must be OPEN or MERGED")
    merge_commit_sha_object = getattr(pull_request_object, "merge_commit_sha", None)
    merge_commit_sha = (
        None
        if merge_commit_sha_object is None
        else _sha(merge_commit_sha_object, "pull_request.merge_commit_sha")
    )
    if state == "OPEN" and merge_commit_sha is not None:
        raise ValueError("OPEN pull requests must not claim a merge commit")
    if state == "MERGED" and merge_commit_sha is None:
        raise ValueError("MERGED pull requests require merge_commit_sha")

    revision_object = getattr(subject, "revision", None)
    revision_kind = _text(getattr(revision_object, "kind", None), "revision.kind")
    if revision_kind not in {"head", "merge_commit"}:
        raise ValueError("revision.kind must be head or merge_commit")
    revision_sha = _sha(getattr(revision_object, "sha", None), "revision.sha")
    if state == "OPEN" and revision_kind != "head":
        raise ValueError("OPEN pull requests require a head revision")
    if state == "MERGED" and revision_kind != "merge_commit":
        raise ValueError("MERGED pull requests require a merge_commit revision")
    if revision_kind == "head" and revision_sha != head.sha:
        raise ValueError("head revision.sha must equal pull_request.head.sha")
    if revision_kind == "merge_commit" and revision_sha != merge_commit_sha:
        raise ValueError("merge_commit revision.sha must equal merge_commit_sha")

    contract = getattr(subject, "contract_source", None)
    contract_repository = _repository(
        getattr(contract, "repository", None), "contract_source.repository"
    )
    if contract_repository != _RepositoryView(OCC_OWNER, OCC_NAME, OCC_REMOTE):
        raise ValueError(
            "contract_source.repository is not the canonical OCC repository"
        )
    contract_commit_sha = _sha(
        getattr(contract, "commit_sha", None), "contract_source.commit_sha"
    )
    contract_path = _text(getattr(contract, "path", None), "contract_source.path")
    if (
        not contract_path
        or len(contract_path) > MAX_CONTRACT_PATH_LENGTH
        or contract_path != contract_path.strip()
        or contract_path.startswith("/")
        or "\\" in contract_path
        or any(part in {"", ".", ".."} for part in contract_path.split("/"))
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in contract_path)
        or not contract_path.startswith("contracts/")
        or not contract_path.endswith((".yaml", ".yml"))
    ):
        raise ValueError(
            "contract_source.path must be a canonical relative contract path"
        )
    contract_file_sha256 = _digest(
        getattr(contract, "file_sha256", None), "contract_source.file_sha256"
    )
    contract_entry_sha256 = _digest(
        getattr(contract, "entry_sha256", None), "contract_source.entry_sha256"
    )
    artifact_sha256 = _digest(
        getattr(subject, "artifact_sha256", None), "artifact_sha256"
    )
    return _SubjectView(
        repository,
        _PullRequestView(
            pull_request_repository,
            number,
            base,
            head,
            state,  # type: ignore[arg-type]
            merge_commit_sha,
        ),
        revision_kind,  # type: ignore[arg-type]
        revision_sha,
        contract_repository,
        contract_commit_sha,
        contract_path,
        contract_file_sha256,
        contract_entry_sha256,
        artifact_sha256,
    )


def _validate_input_budget(value: object) -> None:
    """Reject recursive or oversized untrusted mappings before model parsing."""

    nodes = 0
    seen: set[int] = set()
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_INPUT_NODES:
            raise ValueError("subject input exceeds the object-count bound")
        if depth > MAX_INPUT_DEPTH:
            raise ValueError("subject input exceeds the nesting-depth bound")
        if isinstance(current, str):
            if len(current.encode("utf-8")) > MAX_HTTP_BODY_BYTES:
                raise ValueError("subject input contains an oversized string")
        elif isinstance(current, dict):
            object_id = id(current)
            if object_id in seen:
                raise ValueError("subject input contains a recursive mapping")
            seen.add(object_id)
            if len(current) > MAX_INPUT_NODES:
                raise ValueError("subject input contains too many mapping keys")
            stack.extend((key, depth + 1) for key in current)
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, (list, tuple, set, frozenset)):
            object_id = id(current)
            if object_id in seen:
                raise ValueError("subject input contains a recursive sequence")
            seen.add(object_id)
            if len(current) > MAX_INPUT_NODES:
                raise ValueError("subject input contains too many sequence items")
            stack.extend((child, depth + 1) for child in current)


def _validate_offline_mapping(raw: object) -> None:
    """Perform bounded shape checks when the unreleased core model is absent."""

    if not isinstance(raw, dict):
        raise ValueError("subject must contain a mapping")
    expected = {
        "schema_version",
        "purpose",
        "repository",
        "pull_request",
        "revision",
        "contract_source",
        "artifact_sha256",
    }
    if set(raw) != expected:
        raise ValueError("subject fields must exactly match the canonical v1 shape")
    if raw.get("schema_version") != SUBJECT_SCHEMA_VERSION:
        raise ValueError("subject schema_version is not occ-cross-repo-subject/v1")
    if raw.get("purpose") != SUBJECT_PURPOSE:
        raise ValueError("subject purpose must be evidence_only")
    repository = raw.get("repository")
    if not isinstance(repository, dict):
        raise ValueError("repository must be a mapping")
    if set(repository) != {"github_owner", "github_name", "canonical_remote"}:
        raise ValueError("repository fields are not canonical")
    if repository != {
        "github_owner": PRODUCT_OWNER,
        "github_name": PRODUCT_NAME,
        "canonical_remote": PRODUCT_REMOTE,
    }:
        raise ValueError("subject repository is not the compiled product allowlist")
    pull_request = raw.get("pull_request")
    if not isinstance(pull_request, dict):
        raise ValueError("pull_request must be a mapping")
    allowed_pr = {
        "repository",
        "number",
        "base",
        "head",
        "state",
        "merge_commit_sha",
    }
    if set(pull_request) != allowed_pr:
        raise ValueError("pull_request contains an unsupported field")
    number = pull_request.get("number")
    if (
        isinstance(number, bool)
        or not isinstance(number, int)
        or not 1 <= number <= 2_147_483_647
    ):
        raise ValueError("pull_request.number is not bounded")
    if pull_request.get("repository") != repository:
        raise ValueError("pull_request.repository is not the product repository")
    for ref_name in ("base", "head"):
        ref = pull_request.get(ref_name)
        if not isinstance(ref, dict):
            raise ValueError(f"pull_request.{ref_name} must be a mapping")
        if set(ref) != {
            "github_owner",
            "github_name",
            "canonical_remote",
            "ref",
            "sha",
        }:
            raise ValueError(f"pull_request.{ref_name} fields are not canonical")
        if (
            ref.get("github_owner") != PRODUCT_OWNER
            or ref.get("github_name") != PRODUCT_NAME
        ):
            raise ValueError("fork PR heads are not permitted by the product policy")
        if ref.get("canonical_remote") != PRODUCT_REMOTE:
            raise ValueError("PR ref remote is not the product remote")
        _sha(ref.get("sha"), f"pull_request.{ref_name}.sha")
        ref_value = _text(ref.get("ref"), f"pull_request.{ref_name}.ref")
        if (
            len(ref_value) > MAX_REF_LENGTH
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in ref_value)
            or not ref_value.strip()
            or ref_value != ref_value.strip()
            or _REF_FORBIDDEN_RE.search(ref_value)
        ):
            raise ValueError(f"pull_request.{ref_name}.ref is not canonical")
    if pull_request.get("state") not in {"OPEN", "MERGED"}:
        raise ValueError("pull_request.state must be OPEN or MERGED")
    merge = pull_request.get("merge_commit_sha")
    if pull_request["state"] == "OPEN" and merge is not None:
        raise ValueError("OPEN pull requests must not claim a merge commit")
    if pull_request["state"] == "MERGED":
        _sha(merge, "pull_request.merge_commit_sha")
    revision = raw.get("revision")
    if not isinstance(revision, dict) or set(revision) != {"kind", "sha"}:
        raise ValueError("revision fields are not canonical")
    _sha(revision.get("sha"), "revision.sha")
    if revision.get("kind") not in {"head", "merge_commit"}:
        raise ValueError("revision.kind is not canonical")
    if pull_request["state"] == "OPEN" and revision["kind"] != "head":
        raise ValueError("OPEN pull requests require a head revision")
    if pull_request["state"] == "MERGED" and revision["kind"] != "merge_commit":
        raise ValueError("MERGED pull requests require a merge_commit revision")
    contract = raw.get("contract_source")
    if not isinstance(contract, dict):
        raise ValueError("contract_source must be a mapping")
    if set(contract) != {
        "repository",
        "commit_sha",
        "path",
        "file_sha256",
        "entry_sha256",
    }:
        raise ValueError("contract_source fields are not canonical")
    if contract.get("repository") != {
        "github_owner": OCC_OWNER,
        "github_name": OCC_NAME,
        "canonical_remote": OCC_REMOTE,
    }:
        raise ValueError("contract_source repository is not canonical OCC")
    _sha(contract.get("commit_sha"), "contract_source.commit_sha")
    path = _text(contract.get("path"), "contract_source.path")
    if (
        not path
        or len(path) > MAX_CONTRACT_PATH_LENGTH
        or path != path.strip()
        or path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in path)
        or not path.startswith("contracts/")
        or not path.endswith((".yaml", ".yml"))
    ):
        raise ValueError("contract_source.path is not a canonical contract path")
    _digest(contract.get("file_sha256"), "contract_source.file_sha256")
    _digest(contract.get("entry_sha256"), "contract_source.entry_sha256")
    _digest(raw.get("artifact_sha256"), "artifact_sha256")


def _sha256_prefixed(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _compute_contract_entry_sha256(contract_data: object, evidence_item_id: str) -> str:
    if not isinstance(contract_data, dict):
        raise ValueError("contract bytes do not contain a mapping")
    entries = contract_data.get("dod_evidence")
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
        raise ValueError(f"contract evidence item {evidence_item_id!r} was not found")
    canonical = {
        "header": {
            "ticket_id": contract_data.get("ticket_id"),
            "schema_version": contract_data.get("schema_version"),
        },
        "entry": entry,
    }
    blob = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return _sha256_prefixed(blob.encode("utf-8"))


def _verify_local_contract(
    data: bytes,
    *,
    ticket_id: str | None,
    evidence_item_id: str,
    check_type: str | None,
    check_value: str | None,
    expected_file_sha256: str,
    expected_entry_sha256: str,
) -> None:
    if len(data) > MAX_CONTRACT_BYTES:
        raise ValueError("contract bytes exceed the 1 MiB bound")
    if _sha256_prefixed(data) != expected_file_sha256:
        raise ValueError("contract file digest does not match the receipt")
    try:
        parsed = yaml.safe_load(data)
    except yaml.YAMLError as exc:
        raise ValueError("contract bytes are not valid YAML") from exc
    if not isinstance(parsed, dict):
        raise ValueError("contract bytes do not contain a mapping")
    if ticket_id is not None and parsed.get("ticket_id") != ticket_id:
        raise ValueError("contract bytes do not identify the receipt ticket")
    actual_entry = _compute_contract_entry_sha256(parsed, evidence_item_id)
    if actual_entry != expected_entry_sha256:
        raise ValueError("contract entry digest does not match the receipt")
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
    if not isinstance(entry, dict) or not isinstance(entry.get("checks"), list):
        raise ValueError("contract evidence item has no checks list")
    if (
        check_type is not None
        and check_value is not None
        and not any(
            isinstance(check, dict)
            and check.get("check_type") == check_type
            and check.get("check_value") == check_value
            for check in entry["checks"]
        )
    ):
        raise ValueError("receipt check_type/check_value did not match the contract")


def validate_offline(
    raw_subject: object,
    *,
    evidence_item_id: str | None = None,
    contract_sha256: str | None = None,
    contract_entry_sha256: str | None = None,
    contract_bytes: bytes | None = None,
    artifact_bytes: bytes | None = None,
    subject_model: object | None = None,
) -> CrossRepoValidationResult:
    """Validate local shape/digests without making any network call."""

    try:
        _validate_input_budget(raw_subject)
        try:
            subject = _model_validate_subject(raw_subject, subject_model)
            view = _subject_view(subject)
        except CanonicalSubjectUnavailable:
            _validate_offline_mapping(raw_subject)
            view = None
        errors: list[str] = []
        raw_contract = (
            raw_subject.get("contract_source")
            if isinstance(raw_subject, dict)
            else None
        )
        expected_artifact_sha256 = (
            view.artifact_sha256
            if view is not None
            else raw_subject.get("artifact_sha256")
            if isinstance(raw_subject, dict)
            else None
        )
        expected_contract_sha256 = (
            view.contract_file_sha256
            if view is not None
            else raw_contract.get("file_sha256")
            if isinstance(raw_contract, dict)
            else None
        )
        expected_contract_entry_sha256 = (
            view.contract_entry_sha256
            if view is not None
            else raw_contract.get("entry_sha256")
            if isinstance(raw_contract, dict)
            else None
        )
        if contract_bytes is not None:
            if not isinstance(contract_bytes, bytes):
                errors.append("contract_bytes must be bytes")
            elif (
                contract_sha256 is None
                or contract_entry_sha256 is None
                or evidence_item_id is None
            ):
                errors.append(
                    "contract digests and evidence_item_id are required with contract bytes"
                )
            elif (
                contract_sha256 != expected_contract_sha256
                or contract_entry_sha256 != expected_contract_entry_sha256
            ):
                errors.append("contract digests do not match the subject")
            else:
                try:
                    _verify_local_contract(
                        contract_bytes,
                        ticket_id=None,
                        evidence_item_id=evidence_item_id,
                        check_type=None,
                        check_value=None,
                        expected_file_sha256=contract_sha256,
                        expected_entry_sha256=contract_entry_sha256,
                    )
                except ValueError as exc:
                    # Offline subject-only callers do not have receipt identity;
                    # they still get the exact digest mismatch rather than PASS.
                    errors.append(str(exc))
        if artifact_bytes is not None:
            if not isinstance(artifact_bytes, bytes):
                errors.append("artifact_bytes must be bytes")
            elif (
                expected_artifact_sha256 is not None
                and _sha256_prefixed(artifact_bytes) != expected_artifact_sha256
            ):
                errors.append("artifact digest does not match the subject")
        if errors:
            return CrossRepoValidationResult(CrossRepoStatus.FAIL, tuple(errors))
    except (CanonicalSubjectUnavailable, ValidationError, ValueError) as exc:
        return CrossRepoValidationResult(CrossRepoStatus.FAIL, (str(exc),))
    return CrossRepoValidationResult(
        CrossRepoStatus.UNEVALUATED,
        ("cross-repo subject requires the online GitHub snapshot",),
    )


def parse_canonical_receipt(
    raw: object, *, subject_model: object | None = None
) -> ModelDodReceipt | ParsedCrossRepoReceipt:
    """Parse v1 receipts normally and v2 receipts through the core subject model."""

    _validate_input_budget(raw)
    if not isinstance(raw, dict):
        raise ValueError("receipt must contain a mapping")
    version = raw.get("schema_version")
    carries_subject = "cross_repo_subject" in raw
    if carries_subject and version != "2.0.0":
        raise ValueError("cross_repo_subject is permitted only on receipt schema 2.0.0")
    if version == "2.0.0" and not carries_subject:
        raise ValueError("receipt schema 2.0.0 requires cross_repo_subject")
    if version not in {"1.0.0", "2.0.0"}:
        raise ValueError("receipt schema_version must be exactly 1.0.0 or 2.0.0")
    base_raw = dict(raw)
    subject_raw = base_raw.pop("cross_repo_subject", None)
    try:
        receipt = ModelDodReceipt.model_validate(base_raw)
    except ValidationError as exc:
        raise ValueError(f"invalid canonical receipt: {exc}") from exc
    if not carries_subject:
        return receipt
    if receipt.status is not EnumReceiptStatus.PASS:
        raise ValueError("cross-repository receipts must have status PASS")
    subject = _model_validate_subject(subject_raw, subject_model)
    view = _subject_view(subject)
    if receipt.pr_number is not None:
        raise ValueError("cross-repository receipts must use nested PR identity only")
    if receipt.commit_sha != view.revision_sha:
        raise ValueError("receipt commit_sha must equal subject revision.sha")
    return ParsedCrossRepoReceipt(receipt, subject)


class GitHubTransport(Protocol):
    """Injectable read-only GitHub transport boundary."""

    def request(
        self, path: str, *, headers: dict[str, str], timeout: float
    ) -> ApiResponse: ...


class GhCliTransport(GitHubTransport):
    """Read-only GitHub REST transport through the ``gh`` CLI."""

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
            raise ResolutionFailure("GitHub request failed") from exc
        if completed.returncode != 0 and not completed.stdout:
            raise ResolutionFailure("gh api exited without an HTTP response")
        if len(completed.stdout) > MAX_HTTP_BODY_BYTES:
            raise ResolutionFailure("GitHub response exceeded the bounded body size")
        status, response_headers, body = _parse_http_response(completed.stdout)
        if status is None:
            raise ResolutionFailure("GitHub response did not contain an HTTP status")
        return ApiResponse(status, response_headers, body)


class CrossRepoSubjectResolver:
    """Resolve one or more subjects within a global bounded API batch."""

    def __init__(
        self,
        *,
        transport: GitHubTransport | None = None,
        subject_model: object | None = None,
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
            or not 0 < max_calls <= MAX_API_CALLS
        ):
            raise ValueError("max_calls must be in [1, 12]")
        self._transport = transport if transport is not None else GhCliTransport()
        self._subject_model = subject_model
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
        return self._calls

    def start_batch(self) -> None:
        """Start the one global batch used by a preflight invocation."""

        self._calls = 0
        self._deadline = self._monotonic() + self._total_timeout
        self._retry_used = False
        self._batch_active = True
        self._immutable_cache.clear()

    def resolve(
        self,
        raw_subject: object,
        *,
        evidence_item_id: str,
        check_type: str,
        check_value: str,
        commit_sha: str,
        contract_sha256: str,
        contract_entry_sha256: str,
        artifact_bytes: bytes,
        contract_bytes: bytes | None = None,
    ) -> CrossRepoValidationResult:
        """Resolve a subject with caller-supplied immutable artifact bytes."""

        try:
            _validate_input_budget(raw_subject)
            subject = _model_validate_subject(raw_subject, self._subject_model)
            view = _subject_view(subject)
            self._check_common_bindings(
                view,
                evidence_item_id=evidence_item_id,
                check_type=check_type,
                check_value=check_value,
                commit_sha=commit_sha,
                contract_sha256=contract_sha256,
                contract_entry_sha256=contract_entry_sha256,
                artifact_bytes=artifact_bytes,
                contract_bytes=contract_bytes,
                ticket_id=None,
            )
        except (CanonicalSubjectUnavailable, ValidationError, ValueError) as exc:
            return CrossRepoValidationResult(
                CrossRepoStatus.FAIL, (str(exc),), self._calls
            )
        if not self._batch_active:
            self.start_batch()
        return self._resolve_view(
            view,
            evidence_item_id=evidence_item_id,
            check_type=check_type,
            check_value=check_value,
            commit_sha=commit_sha,
            contract_sha256=contract_sha256,
            contract_entry_sha256=contract_entry_sha256,
            ticket_id=None,
        )

    def resolve_receipt(
        self,
        raw_receipt: ModelDodReceipt | ParsedCrossRepoReceipt | object,
        *,
        artifact_bytes: bytes | None = None,
        contract_bytes: bytes | None = None,
    ) -> CrossRepoValidationResult:
        """Resolve a parsed v2 receipt; v1 receipts are never online subjects."""

        try:
            parsed = (
                raw_receipt
                if isinstance(raw_receipt, ParsedCrossRepoReceipt)
                else parse_canonical_receipt(
                    raw_receipt, subject_model=self._subject_model
                )
            )
            if not isinstance(parsed, ParsedCrossRepoReceipt):
                raise ValueError(
                    "online cross-repository validation requires receipt schema 2.0.0"
                )
            view = _subject_view(parsed.subject)
            receipt = parsed.receipt
            if receipt.contract_sha256 is None or receipt.contract_entry_sha256 is None:
                raise ValueError(
                    "cross-repository receipts require both contract digests"
                )
            if artifact_bytes is not None:
                if not isinstance(artifact_bytes, bytes):
                    raise ValueError("artifact_bytes must be bytes")
                if _sha256_prefixed(artifact_bytes) != view.artifact_sha256:
                    raise ValueError("artifact digest does not match the subject")
            elif not view.artifact_sha256:
                raise ValueError("cross-repository subject is missing artifact digest")
            if contract_bytes is not None:
                _verify_local_contract(
                    contract_bytes,
                    ticket_id=receipt.ticket_id,
                    evidence_item_id=receipt.evidence_item_id,
                    check_type=receipt.check_type,
                    check_value=receipt.check_value,
                    expected_file_sha256=receipt.contract_sha256,
                    expected_entry_sha256=receipt.contract_entry_sha256,
                )
        except (CanonicalSubjectUnavailable, ValidationError, ValueError) as exc:
            return CrossRepoValidationResult(
                CrossRepoStatus.FAIL, (str(exc),), self._calls
            )
        if not self._batch_active:
            self.start_batch()
        return self._resolve_view(
            view,
            evidence_item_id=receipt.evidence_item_id,
            check_type=receipt.check_type,
            check_value=receipt.check_value,
            commit_sha=receipt.commit_sha,
            contract_sha256=receipt.contract_sha256,
            contract_entry_sha256=receipt.contract_entry_sha256,
            ticket_id=receipt.ticket_id,
        )

    def _check_common_bindings(
        self,
        view: _SubjectView,
        *,
        evidence_item_id: str,
        check_type: str,
        check_value: str,
        commit_sha: str,
        contract_sha256: str,
        contract_entry_sha256: str,
        artifact_bytes: bytes,
        contract_bytes: bytes | None,
        ticket_id: str | None,
    ) -> None:
        if commit_sha != view.revision_sha:
            raise ValueError("receipt commit_sha must equal subject revision.sha")
        if (
            _DIGEST_RE.fullmatch(contract_sha256) is None
            or _DIGEST_RE.fullmatch(contract_entry_sha256) is None
        ):
            raise ValueError("contract digests must be sha256:<64 lowercase hex>")
        if contract_sha256 != view.contract_file_sha256:
            raise ValueError("contract file digest does not match the subject")
        if contract_entry_sha256 != view.contract_entry_sha256:
            raise ValueError("contract entry digest does not match the subject")
        if not isinstance(artifact_bytes, bytes):
            raise ValueError("artifact_bytes must be bytes")
        if len(artifact_bytes) > MAX_CONTRACT_BYTES:
            raise ValueError("artifact bytes exceed the bounded size")
        if _sha256_prefixed(artifact_bytes) != view.artifact_sha256:
            raise ValueError("artifact digest does not match the subject")
        if contract_bytes is not None:
            _verify_local_contract(
                contract_bytes,
                ticket_id=ticket_id,
                evidence_item_id=evidence_item_id,
                check_type=check_type,
                check_value=check_value,
                expected_file_sha256=contract_sha256,
                expected_entry_sha256=contract_entry_sha256,
            )

    def _resolve_view(
        self,
        view: _SubjectView,
        *,
        evidence_item_id: str,
        check_type: str,
        check_value: str,
        commit_sha: str,
        contract_sha256: str,
        contract_entry_sha256: str,
        ticket_id: str | None,
    ) -> CrossRepoValidationResult:
        try:
            self._verify_pr(view)
            self._verify_commit(PRODUCT_REPOSITORY, commit_sha)
            self._verify_pr_membership(view, commit_sha)
            contract_bytes = self._fetch_contract(view)
            _verify_local_contract(
                contract_bytes,
                ticket_id=ticket_id,
                evidence_item_id=evidence_item_id,
                check_type=check_type,
                check_value=check_value,
                expected_file_sha256=contract_sha256,
                expected_entry_sha256=contract_entry_sha256,
            )
            if view.revision_kind == "merge_commit":
                self._verify_merge_ancestry(view)
        except (ResolutionFailure, ValidationError, ValueError) as exc:
            return CrossRepoValidationResult(
                CrossRepoStatus.FAIL, (str(exc),), self._calls
            )
        return CrossRepoValidationResult(CrossRepoStatus.PASS, api_calls=self._calls)

    def _verify_pr(self, view: _SubjectView) -> None:
        payload = self._json_get(
            f"repos/{PRODUCT_REPOSITORY}/pulls/{view.pull_request.number}",
            immutable=False,
        )
        if not isinstance(payload, dict):
            raise ResolutionFailure("GitHub PR response was not an object")
        required = {"number", "state", "merged_at", "merge_commit_sha", "base", "head"}
        if not required.issubset(payload):
            raise ResolutionFailure("GitHub PR response omitted required metadata")
        if payload.get("number") != view.pull_request.number:
            raise ResolutionFailure("GitHub PR number did not match the subject")
        base = _api_ref(payload.get("base"), "base")
        head = _api_ref(payload.get("head"), "head")
        if base != view.pull_request.base:
            raise ResolutionFailure("GitHub PR base identity did not match the subject")
        if head != view.pull_request.head:
            raise ResolutionFailure("GitHub PR head identity did not match the subject")
        observed_state = _observed_pr_state(payload)
        if observed_state != view.pull_request.state:
            raise ResolutionFailure("GitHub PR state did not match the subject")
        observed_merge = payload.get("merge_commit_sha")
        if view.pull_request.state == "OPEN":
            if observed_merge is not None:
                raise ResolutionFailure("open PR cannot claim a merge commit")
        elif observed_merge != view.pull_request.merge_commit_sha:
            raise ResolutionFailure("GitHub merge commit did not match the subject")

    def _verify_commit(self, repository: str, sha: str) -> None:
        payload = self._json_get(
            f"repos/{repository}/git/commits/{quote(sha, safe='')}", immutable=True
        )
        if not isinstance(payload, dict) or payload.get("sha") != sha:
            raise ResolutionFailure(
                "GitHub commit object did not match the requested SHA"
            )

    def _verify_pr_membership(self, view: _SubjectView, sha: str) -> None:
        payload = self._json_get(
            f"repos/{PRODUCT_REPOSITORY}/commits/{quote(sha, safe='')}/pulls",
            immutable=True,
        )
        if not isinstance(payload, list):
            raise ResolutionFailure("commit-to-PR membership response was malformed")
        for item in payload:
            if (
                not isinstance(item, dict)
                or item.get("number") != view.pull_request.number
            ):
                continue
            base = item.get("base")
            if (
                isinstance(base, dict)
                and _api_ref(base, "membership base") == view.pull_request.base
            ):
                return
        raise ResolutionFailure("revision is not a member of the declared product PR")

    def _fetch_contract(self, view: _SubjectView) -> bytes:
        self._verify_commit(OCC_REPOSITORY, view.contract_commit_sha)
        payload = self._json_get(
            f"repos/{OCC_REPOSITORY}/contents/{quote(view.contract_path, safe='/')}"
            f"?ref={quote(view.contract_commit_sha, safe='')}",
            immutable=True,
        )
        if not isinstance(payload, dict):
            raise ResolutionFailure("contract contents response was not an object")
        if payload.get("path") != view.contract_path or payload.get("type") != "file":
            raise ResolutionFailure(
                "contract contents object was not the requested regular file"
            )
        if payload.get("encoding") != "base64" or any(
            key in payload
            for key in ("target", "symlink", "submodule", "submodule_git_url")
        ):
            raise ResolutionFailure(
                "contract contents object carried link or unsupported metadata"
            )
        encoded = payload.get("content")
        if not isinstance(encoded, str) or len(encoded) > MAX_CONTRACT_BYTES * 2:
            raise ResolutionFailure("contract contents exceeded the bounded size")
        try:
            data = base64.b64decode(re.sub(r"\s+", "", encoded), validate=True)
        except (UnicodeError, ValueError, binascii.Error) as exc:
            raise ResolutionFailure("contract contents were not valid base64") from exc
        if len(data) > MAX_CONTRACT_BYTES:
            raise ResolutionFailure("contract bytes exceeded the bounded size")
        return data

    def _verify_contract_digests_only(
        self, data: bytes, *, expected_file_sha256: str, expected_entry_sha256: str
    ) -> None:
        if _sha256_prefixed(data) != expected_file_sha256:
            raise ResolutionFailure("immutable contract file digest did not match")
        try:
            parsed = yaml.safe_load(data)
            if not isinstance(parsed, dict):
                raise ValueError("contract bytes do not contain a mapping")
            entries = parsed.get("dod_evidence")
            if not isinstance(entries, list):
                raise ValueError("contract has no dod_evidence list")
            entry_ids = [item.get("id") for item in entries if isinstance(item, dict)]
            if len(entry_ids) != len(set(entry_ids)):
                raise ValueError("contract contains duplicate evidence item ids")
        except (TypeError, ValueError, yaml.YAMLError) as exc:
            raise ResolutionFailure(
                f"immutable contract could not be parsed: {exc}"
            ) from exc
        # Subject-only resolution cannot infer which receipt item owns the
        # digest. The receipt form below performs the exact entry binding.
        if (
            not isinstance(expected_entry_sha256, str)
            or _DIGEST_RE.fullmatch(expected_entry_sha256) is None
        ):
            raise ResolutionFailure("contract entry digest was malformed")

    def _verify_merge_ancestry(self, view: _SubjectView) -> None:
        merge_sha = view.pull_request.merge_commit_sha
        if merge_sha is None:
            raise ResolutionFailure("merged PR is missing merge_commit_sha")
        payload = self._json_get(
            f"repos/{PRODUCT_REPOSITORY}/compare/{quote(merge_sha, safe='')}..."
            f"{quote(view.pull_request.base.sha, safe='')}",
            immutable=True,
        )
        if not isinstance(payload, dict) or payload.get("status") not in {
            "identical",
            "ahead",
        }:
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

    def _json_get(
        self, path: str, *, immutable: bool
    ) -> dict[str, object] | list[object]:
        if self._monotonic() >= self._deadline:
            raise ResolutionFailure(
                "cross-repo validation exceeded the 15 second deadline"
            )
        cached = self._immutable_cache.get(path) if immutable else None
        headers = {"Accept": "application/vnd.github+json"}
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
            except (OSError, TimeoutError, ResolutionFailure) as exc:
                raise ResolutionFailure("GitHub transport failed") from exc
            except (
                Exception
            ) as exc:  # fallback-ok: all unknown transport failures fail closed
                raise ResolutionFailure("GitHub transport failed") from exc
            if self._monotonic() >= self._deadline:
                raise ResolutionFailure(
                    "cross-repo validation exceeded the 15 second deadline"
                )
            if (
                isinstance(response.status_code, bool)
                or not isinstance(response.status_code, int)
                or not isinstance(response.headers, dict)
                or not isinstance(response.body, bytes)
                or len(response.body) > MAX_HTTP_BODY_BYTES
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
            response = ApiResponse(
                response.status_code,
                {key.lower(): value for key, value in response.headers.items()},
                response.body,
            )
            if response.status_code == 429 and not self._retry_used:
                retry_after = response.headers.get("retry-after")
                if retry_after is None:
                    raise ResolutionFailure("GitHub API rate limit exceeded")
                try:
                    delay = float(retry_after)
                except ValueError as exc:
                    raise ResolutionFailure("GitHub API rate limit exceeded") from exc
                if (
                    not math.isfinite(delay)
                    or delay < 0
                    or delay > min(1.0, self._deadline - self._monotonic())
                ):
                    raise ResolutionFailure("GitHub API rate limit exceeded")
                self._retry_used = True
                if delay:
                    self._sleep(delay)
                continue
            return response


def _api_remote(repo: dict[str, object], owner: str, name: str, label: str) -> str:
    expected = f"https://github.com/{owner}/{name}.git"
    supplied = repo.get("clone_url")
    if supplied is None:
        supplied = repo.get("html_url")
        if isinstance(supplied, str) and not supplied.endswith(".git"):
            supplied = f"{supplied}.git"
    if supplied != expected:
        raise ResolutionFailure(
            f"GitHub {label} repository URL did not match its identity"
        )
    return expected


def _api_identity_from_repo(value: object) -> _RepositoryView:
    if not isinstance(value, dict):
        raise ResolutionFailure("GitHub repository metadata was missing")
    owner_data = value.get("owner")
    owner = owner_data.get("login") if isinstance(owner_data, dict) else None
    name = value.get("name")
    if not isinstance(owner, str) or not isinstance(name, str):
        raise ResolutionFailure("GitHub repository metadata was malformed")
    return _RepositoryView(owner, name, _api_remote(value, owner, name, "membership"))


def _api_ref(value: object, label: str) -> _RefView:
    if not isinstance(value, dict):
        raise ResolutionFailure(f"GitHub {label} metadata was missing")
    repo = value.get("repo")
    if not isinstance(repo, dict):
        raise ResolutionFailure(f"GitHub {label} repository metadata was missing")
    owner_data = repo.get("owner")
    owner = owner_data.get("login") if isinstance(owner_data, dict) else None
    name = repo.get("name")
    ref = value.get("ref")
    sha = value.get("sha")
    if (
        not isinstance(owner, str)
        or not isinstance(name, str)
        or not isinstance(ref, str)
    ):
        raise ResolutionFailure(f"GitHub {label} metadata was malformed")
    if not isinstance(sha, str) or _SHA_RE.fullmatch(sha) is None:
        raise ResolutionFailure(f"GitHub {label} SHA metadata was malformed")
    repository = _RepositoryView(owner, name, _api_remote(repo, owner, name, label))
    if not ref.strip() or ref != ref.strip() or _REF_FORBIDDEN_RE.search(ref):
        raise ResolutionFailure(f"GitHub {label} ref metadata was malformed")
    return _RefView(repository, ref, sha)


def _observed_pr_state(payload: dict[str, object]) -> Literal["OPEN", "MERGED"]:
    state = payload.get("state")
    merged_at = payload.get("merged_at")
    if state == "open" and merged_at is None:
        return "OPEN"
    if state == "closed" and isinstance(merged_at, str) and merged_at:
        return "MERGED"
    raise ResolutionFailure("GitHub PR was closed-unmerged or had malformed state")


def _parse_http_response(stdout: bytes) -> tuple[int | None, dict[str, str], bytes]:
    """Parse the final response from ``gh api --include``."""

    status_matches = list(re.finditer(rb"(?m)^HTTP/\S+\s+(\d{3})\b", stdout))
    if not status_matches:
        return None, {}, b""
    match = status_matches[-1]
    separator = re.search(rb"\r?\n\r?\n", stdout[match.end() :])
    if separator is None:
        return int(match.group(1)), {}, b""
    separator_start = match.end() + separator.start()
    separator_end = match.end() + separator.end()
    response_headers: dict[str, str] = {}
    for line in stdout[match.start() : separator_start].splitlines()[1:]:
        if b":" not in line:
            continue
        name, value = line.split(b":", 1)
        response_headers[name.decode("ascii", "ignore").strip().lower()] = value.decode(
            "ascii", "ignore"
        ).strip()
    return int(match.group(1)), response_headers, stdout[separator_end:]


__all__ = [
    "MAX_API_CALLS",
    "MAX_HTTP_BODY_BYTES",
    "MAX_RECEIPT_AGGREGATE_BYTES",
    "MAX_RECEIPT_BYTES",
    "MAX_RECEIPT_COUNT",
    "OCC_REMOTE",
    "OCC_REPOSITORY",
    "PRODUCT_NAME",
    "PRODUCT_OWNER",
    "PRODUCT_REMOTE",
    "PRODUCT_REPOSITORY",
    "SUBJECT_PURPOSE",
    "SUBJECT_SCHEMA_VERSION",
    "ApiResponse",
    "CanonicalSubjectUnavailable",
    "CrossRepoStatus",
    "CrossRepoSubjectResolver",
    "CrossRepoValidationResult",
    "GhCliTransport",
    "GitHubTransport",
    "ParsedCrossRepoReceipt",
    "ResolutionFailure",
    "_compute_contract_entry_sha256",
    "_parse_http_response",
    "parse_canonical_receipt",
    "validate_offline",
]
