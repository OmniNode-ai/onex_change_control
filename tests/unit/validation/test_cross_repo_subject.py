# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
# ruff: noqa: E501, EM101, TRY003, TRY004

"""Adversarial controls for the bounded cross-repository subject resolver."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from onex_change_control.scripts.check_cross_repo_subject import main as subject_cli
from onex_change_control.validation.cross_repo_subject import (
    ApiResponse,
    CrossRepoStatus,
    CrossRepoSubjectResolver,
    ParsedCrossRepoReceipt,
    _compute_contract_entry_sha256,
    parse_canonical_receipt,
    validate_offline,
)

PRODUCT_OWNER = "OmniNode-ai"
PRODUCT_NAME = "omnibase_infra"
PRODUCT_REMOTE = f"https://github.com/{PRODUCT_OWNER}/{PRODUCT_NAME}.git"
OCC_REMOTE = "https://github.com/OmniNode-ai/onex_change_control.git"
HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
CONTRACT_SHA = "c" * 40
MERGE_SHA = "d" * 40


def _repo(owner: str = PRODUCT_OWNER, name: str = PRODUCT_NAME) -> dict[str, str]:
    return {
        "github_owner": owner,
        "github_name": name,
        "canonical_remote": f"https://github.com/{owner}/{name}.git",
    }


def _ref(ref: str, sha: str, repo: dict[str, str] | None = None) -> dict[str, Any]:
    value = dict(repo or _repo())
    value.update({"ref": ref, "sha": sha})
    return value


def _contract_data() -> dict[str, Any]:
    return {
        "ticket_id": "OMN-17582",
        "schema_version": "1.0.0",
        "dod_evidence": [
            {
                "id": "dod-cross-repo-resolver",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": "uv run pytest tests/unit/validation/test_cross_repo_subject.py -q",
                    }
                ],
            }
        ],
    }


def _contract_bytes() -> bytes:
    return yaml.safe_dump(_contract_data(), sort_keys=False).encode()


def _subject(
    *,
    state: str = "OPEN",
    head_sha: str = HEAD_SHA,
    repository: dict[str, str] | None = None,
) -> dict[str, Any]:
    product = dict(repository or _repo())
    merge = None if state == "OPEN" else MERGE_SHA
    revision_kind = "head" if state == "OPEN" else "merge_commit"
    revision_sha = head_sha if state == "OPEN" else MERGE_SHA
    return {
        "schema_version": "occ-cross-repo-subject/v1",
        "purpose": "evidence_only",
        "repository": product,
        "pull_request": {
            "repository": dict(product),
            "number": 42,
            "base": _ref("dev", BASE_SHA, product),
            "head": _ref("codex/omn-17582", head_sha, product),
            "state": state,
            "merge_commit_sha": merge,
        },
        "revision": {"kind": revision_kind, "sha": revision_sha},
        "contract_source": {
            "repository": _repo("OmniNode-ai", "onex_change_control"),
            "commit_sha": CONTRACT_SHA,
            "path": "contracts/OMN-17582.yaml",
            "file_sha256": f"sha256:{hashlib.sha256(_contract_bytes()).hexdigest()}",
            "entry_sha256": f"sha256:{hashlib.sha256(b'not-used').hexdigest()}",
        },
        "artifact_sha256": f"sha256:{hashlib.sha256(b'artifact').hexdigest()}",
    }


def _subject_model(value: object) -> object:
    """Small strict adapter standing in for the unreleased Core model."""

    if not isinstance(value, dict):
        raise ValueError("subject must be a mapping")
    expected = {
        "schema_version",
        "purpose",
        "repository",
        "pull_request",
        "revision",
        "contract_source",
        "artifact_sha256",
    }
    if set(value) != expected:
        raise ValueError("subject model rejected extra or missing fields")

    def convert(item: object) -> object:
        if isinstance(item, dict):
            return SimpleNamespace(
                **{key: convert(child) for key, child in item.items()}
            )
        return item

    return convert(value)


class FakeSubjectModel:
    @classmethod
    def model_validate(cls, value: object) -> object:
        return _subject_model(value)


class FakeTransport:
    def __init__(self, responses: dict[str, ApiResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def request(
        self, path: str, *, headers: dict[str, str], timeout: float
    ) -> ApiResponse:
        del headers, timeout
        self.calls.append(path)
        return self.responses.get(
            path, ApiResponse(404, {"content-type": "application/json"}, b"{}")
        )


class SequenceTransport:
    def __init__(self, responses: list[ApiResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def request(
        self, path: str, *, headers: dict[str, str], timeout: float
    ) -> ApiResponse:
        del path, headers, timeout
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def _response(value: object, status: int = 200) -> ApiResponse:
    return ApiResponse(
        status,
        {"content-type": "application/json", "etag": '"fixed"'},
        json.dumps(value).encode(),
    )


def _api_responses(
    subject: dict[str, Any], *, merged: bool = False
) -> dict[str, ApiResponse]:
    pr = subject["pull_request"]
    base = pr["base"]
    head = pr["head"]
    product = {
        "owner": {"login": PRODUCT_OWNER},
        "name": PRODUCT_NAME,
        "clone_url": PRODUCT_REMOTE,
    }
    repo_pr = {"repo": product, "ref": base["ref"], "sha": base["sha"]}
    repo_head = {"repo": product, "ref": head["ref"], "sha": head["sha"]}
    pr_payload = {
        "number": pr["number"],
        "state": "open" if not merged else "closed",
        "merged_at": None if not merged else "2026-09-03T12:00:00Z",
        "merge_commit_sha": None if not merged else MERGE_SHA,
        "base": repo_pr,
        "head": repo_head,
    }
    membership = {
        "number": pr["number"],
        "base": {"repo": product, "ref": base["ref"], "sha": base["sha"]},
    }
    contract_repo = {
        "owner": {"login": "OmniNode-ai"},
        "name": "onex_change_control",
        "clone_url": OCC_REMOTE,
    }
    contract_bytes = _contract_bytes()
    content_path = subject["contract_source"]["path"]
    content_commit = subject["contract_source"]["commit_sha"]
    revision_sha = subject["revision"]["sha"]
    result = {
        f"repos/{PRODUCT_OWNER}/{PRODUCT_NAME}/pulls/{pr['number']}": _response(
            pr_payload
        ),
        f"repos/{PRODUCT_OWNER}/{PRODUCT_NAME}/git/commits/{revision_sha}": _response(
            {"sha": revision_sha}
        ),
        f"repos/{PRODUCT_OWNER}/{PRODUCT_NAME}/commits/{revision_sha}/pulls": _response(
            [membership]
        ),
        f"repos/OmniNode-ai/onex_change_control/git/commits/{content_commit}": _response(
            {"sha": content_commit}
        ),
        f"repos/OmniNode-ai/onex_change_control/contents/{content_path}?ref={content_commit}": _response(
            {
                "path": content_path,
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(contract_bytes).decode(),
                "repository": contract_repo,
            }
        ),
    }
    if merged:
        result[
            f"repos/{PRODUCT_OWNER}/{PRODUCT_NAME}/compare/{MERGE_SHA}...{base['sha']}"
        ] = _response(
            {
                "status": "identical",
                "base_commit": {"sha": MERGE_SHA},
                "merge_base_commit": {"sha": MERGE_SHA},
            }
        )
    return result


def _receipt(subject: dict[str, Any], *, status: str = "PASS") -> dict[str, Any]:
    contract = _contract_data()
    contract_entry = _compute_contract_entry_sha256(contract, "dod-cross-repo-resolver")
    return {
        "schema_version": "2.0.0",
        "ticket_id": "OMN-17582",
        "evidence_item_id": "dod-cross-repo-resolver",
        "check_type": "command",
        "check_value": contract["dod_evidence"][0]["checks"][0]["check_value"],
        "status": status,
        "run_timestamp": datetime.now(UTC).isoformat(),
        "commit_sha": subject["revision"]["sha"],
        "runner": "ci-job",
        "verifier": "independent-ci",
        "probe_command": "true",
        "probe_stdout": "ok",
        "pr_number": None,
        "contract_sha256": subject["contract_source"]["file_sha256"],
        "contract_entry_sha256": contract_entry,
        "cross_repo_subject": subject,
    }


def test_offline_valid_subject_is_unevaluated_and_network_free() -> None:
    subject = _subject()
    artifact = b"artifact"
    result = validate_offline(
        subject, subject_model=FakeSubjectModel, artifact_bytes=artifact
    )
    assert result.status is CrossRepoStatus.UNEVALUATED


def test_offline_fallback_shape_is_unevaluated_before_core_release() -> None:
    result = validate_offline(_subject())
    assert result.status is CrossRepoStatus.UNEVALUATED
    mismatch = validate_offline(_subject(), artifact_bytes=b"wrong")
    assert mismatch.status is CrossRepoStatus.FAIL


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda value: value["pull_request"].update(number=0), "number"),
        (
            lambda value: value["pull_request"]["head"].update(github_owner="fork"),
            "fork",
        ),
        (lambda value: value["contract_source"].update(path="../secret.yaml"), "path"),
        (lambda value: value.update(extra=True), "fields"),
    ],
)
def test_offline_shape_mutations_fail_closed(mutation: Any, expected: str) -> None:
    subject = _subject()
    mutation(subject)
    result = validate_offline(subject, subject_model=FakeSubjectModel)
    assert result.status is CrossRepoStatus.FAIL
    assert expected in " ".join(result.details).lower()


def test_offline_digest_mutation_fails() -> None:
    subject = _subject()
    result = validate_offline(
        subject,
        subject_model=FakeSubjectModel,
        artifact_bytes=b"wrong",
    )
    assert result.status is CrossRepoStatus.FAIL


def test_online_open_resolves_exact_identity_and_contract() -> None:
    subject = _subject()
    contract = _contract_data()
    subject["contract_source"]["entry_sha256"] = _compute_contract_entry_sha256(
        contract, "dod-cross-repo-resolver"
    )
    transport = FakeTransport(_api_responses(subject))
    resolver = CrossRepoSubjectResolver(
        transport=transport, subject_model=FakeSubjectModel
    )
    result = resolver.resolve(
        subject,
        evidence_item_id="dod-cross-repo-resolver",
        check_type="command",
        check_value=contract["dod_evidence"][0]["checks"][0]["check_value"],
        commit_sha=HEAD_SHA,
        contract_sha256=subject["contract_source"]["file_sha256"],
        contract_entry_sha256=subject["contract_source"]["entry_sha256"],
        artifact_bytes=b"artifact",
    )
    assert result.status is CrossRepoStatus.PASS
    assert len(transport.calls) == 5


def test_online_merged_requires_compare_ancestry() -> None:
    subject = _subject(state="MERGED")
    contract = _contract_data()
    subject["contract_source"]["entry_sha256"] = _compute_contract_entry_sha256(
        contract, "dod-cross-repo-resolver"
    )
    transport = FakeTransport(_api_responses(subject, merged=True))
    resolver = CrossRepoSubjectResolver(
        transport=transport, subject_model=FakeSubjectModel
    )
    result = resolver.resolve(
        subject,
        evidence_item_id="dod-cross-repo-resolver",
        check_type="command",
        check_value=contract["dod_evidence"][0]["checks"][0]["check_value"],
        commit_sha=MERGE_SHA,
        contract_sha256=subject["contract_source"]["file_sha256"],
        contract_entry_sha256=subject["contract_source"]["entry_sha256"],
        artifact_bytes=b"artifact",
    )
    assert result.status is CrossRepoStatus.PASS
    assert len(transport.calls) == 6


def test_online_fork_pr_is_rejected_before_api() -> None:
    subject = _subject()
    subject["pull_request"]["head"]["github_owner"] = "fork"
    subject["pull_request"]["head"]["canonical_remote"] = (
        "https://github.com/fork/omnibase_infra.git"
    )
    transport = FakeTransport({})
    resolver = CrossRepoSubjectResolver(
        transport=transport, subject_model=FakeSubjectModel
    )
    result = resolver.resolve(
        subject,
        evidence_item_id="dod-cross-repo-resolver",
        check_type="command",
        check_value="true",
        commit_sha=HEAD_SHA,
        contract_sha256=subject["contract_source"]["file_sha256"],
        contract_entry_sha256=subject["contract_source"]["entry_sha256"],
        artifact_bytes=b"artifact",
    )
    assert result.status is CrossRepoStatus.FAIL
    assert transport.calls == []


def test_transport_failure_never_passes() -> None:
    subject = _subject()
    transport = FakeTransport(
        {
            f"repos/{PRODUCT_OWNER}/{PRODUCT_NAME}/pulls/42": _response(
                {"error": "unauthorized"}, status=401
            )
        }
    )
    resolver = CrossRepoSubjectResolver(
        transport=transport, subject_model=FakeSubjectModel
    )
    result = resolver.resolve(
        subject,
        evidence_item_id="dod-cross-repo-resolver",
        check_type="command",
        check_value="true",
        commit_sha=HEAD_SHA,
        contract_sha256=subject["contract_source"]["file_sha256"],
        contract_entry_sha256=subject["contract_source"]["entry_sha256"],
        artifact_bytes=b"artifact",
    )
    assert result.status is CrossRepoStatus.FAIL


def test_rate_limit_allows_only_one_bounded_retry() -> None:
    subject = _subject()
    contract = _contract_data()
    subject["contract_source"]["entry_sha256"] = _compute_contract_entry_sha256(
        contract, "dod-cross-repo-resolver"
    )
    valid = _api_responses(subject)
    ordered = [
        _response({}, status=429),
        *[valid[path] for path in valid],
    ]
    ordered[0] = ApiResponse(
        429, {"Retry-After": "0", "content-type": "application/json"}, b"{}"
    )
    transport = SequenceTransport(ordered)
    resolver = CrossRepoSubjectResolver(
        transport=transport, subject_model=FakeSubjectModel
    )
    result = resolver.resolve(
        subject,
        evidence_item_id="dod-cross-repo-resolver",
        check_type="command",
        check_value=contract["dod_evidence"][0]["checks"][0]["check_value"],
        commit_sha=HEAD_SHA,
        contract_sha256=subject["contract_source"]["file_sha256"],
        contract_entry_sha256=subject["contract_source"]["entry_sha256"],
        artifact_bytes=b"artifact",
    )
    assert result.status is CrossRepoStatus.PASS
    assert result.api_calls == 6


def test_call_budget_and_malformed_json_fail_closed() -> None:
    subject = _subject()
    malformed = ApiResponse(200, {"content-type": "application/json"}, b"not-json")
    transport = SequenceTransport([malformed])
    resolver = CrossRepoSubjectResolver(
        transport=transport, subject_model=FakeSubjectModel, max_calls=1
    )
    result = resolver.resolve(
        subject,
        evidence_item_id="dod-cross-repo-resolver",
        check_type="command",
        check_value="true",
        commit_sha=HEAD_SHA,
        contract_sha256=subject["contract_source"]["file_sha256"],
        contract_entry_sha256=subject["contract_source"]["entry_sha256"],
        artifact_bytes=b"artifact",
    )
    assert result.status is CrossRepoStatus.FAIL
    assert result.api_calls == 1


def test_static_subject_schema_is_strict_and_matches_fixture() -> None:
    schema_path = Path("schemas/occ_cross_repo_subject_v1.schema.yaml")
    schema = yaml.safe_load(schema_path.read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_subject())
    invalid = _subject()
    invalid["extra"] = True
    assert list(Draft202012Validator(schema).iter_errors(invalid))
    receipt_schema = yaml.safe_load(
        Path("schemas/occ_receipt_v2.schema.yaml").read_text()
    )
    Draft202012Validator.check_schema(receipt_schema)
    registry = Registry().with_resources(
        [
            (schema["$id"], Resource.from_contents(schema)),
            (receipt_schema["$id"], Resource.from_contents(receipt_schema)),
        ]
    )
    Draft202012Validator(receipt_schema, registry=registry).validate(
        _receipt(_subject())
    )


def test_online_v2_fails_closed_when_core_subject_model_is_unreleased() -> None:
    subject = _subject()
    receipt = _receipt(subject)
    transport = FakeTransport({})
    resolver = CrossRepoSubjectResolver(transport=transport)
    result = resolver.resolve_receipt(receipt)
    assert result.status is CrossRepoStatus.FAIL
    assert result.api_calls == 0
    assert transport.calls == []


def test_v2_receipt_requires_core_subject_and_binds_commit() -> None:
    subject = _subject()
    subject["contract_source"]["entry_sha256"] = _compute_contract_entry_sha256(
        _contract_data(), "dod-cross-repo-resolver"
    )
    receipt = _receipt(subject)
    parsed = parse_canonical_receipt(receipt, subject_model=FakeSubjectModel)
    assert isinstance(parsed, ParsedCrossRepoReceipt)
    assert parsed.receipt.commit_sha == HEAD_SHA
    receipt["commit_sha"] = BASE_SHA
    with pytest.raises(ValueError, match="commit_sha"):
        parse_canonical_receipt(receipt, subject_model=FakeSubjectModel)


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("contract_sha256", "contract_source.file_sha256"),
        ("contract_entry_sha256", "contract_source.entry_sha256"),
    ],
)
def test_online_v2_rejects_receipt_subject_contract_digest_mismatch(
    field: str, expected: str
) -> None:
    subject = _subject()
    subject["contract_source"]["entry_sha256"] = _compute_contract_entry_sha256(
        _contract_data(), "dod-cross-repo-resolver"
    )
    receipt = _receipt(subject)
    receipt[field] = f"sha256:{'0' * 64}"
    transport = FakeTransport(_api_responses(subject))
    resolver = CrossRepoSubjectResolver(
        transport=transport, subject_model=FakeSubjectModel
    )
    result = resolver.resolve_receipt(receipt)
    assert result.status is CrossRepoStatus.FAIL
    assert expected in " ".join(result.details)
    assert transport.calls == []


def test_offline_cli_reports_valid_v2_as_unevaluated(tmp_path: Path) -> None:
    subject = _subject()
    subject["contract_source"]["entry_sha256"] = _compute_contract_entry_sha256(
        _contract_data(), "dod-cross-repo-resolver"
    )
    receipt = _receipt(subject)
    receipt_path = tmp_path / "receipt.yaml"
    receipt_path.write_text(yaml.safe_dump(receipt), encoding="utf-8")
    assert subject_cli(["--offline", str(receipt_path)]) == 0


def test_offline_cli_rejects_nonpass_v2_receipt(tmp_path: Path) -> None:
    subject = _subject()
    subject["contract_source"]["entry_sha256"] = _compute_contract_entry_sha256(
        _contract_data(), "dod-cross-repo-resolver"
    )
    receipt = _receipt(subject, status="FAIL")
    receipt_path = tmp_path / "receipt.yaml"
    receipt_path.write_text(yaml.safe_dump(receipt), encoding="utf-8")
    assert subject_cli(["--offline", str(receipt_path)]) == 1
