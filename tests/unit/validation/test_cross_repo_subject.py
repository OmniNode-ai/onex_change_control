# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Threat fixtures for the versioned cross-repository receipt subject."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from onex_change_control.scripts.check_cross_repo_subject import _load_mapping
from onex_change_control.validation.cross_repo_subject import (
    ApiResponse,
    CrossRepoStatus,
    CrossRepoSubject,
    CrossRepoSubjectResolver,
    CrossRepoValidationResult,
    _compute_contract_entry_sha256,
    _parse_http_response,
    validate_offline,
)

PRODUCT = {
    "github_owner": "OmniNode-ai",
    "github_name": "omnibase_infra",
    "canonical_remote": "https://github.com/OmniNode-ai/omnibase_infra.git",
}
OCC = {
    "github_owner": "OmniNode-ai",
    "github_name": "onex_change_control",
    "canonical_remote": "https://github.com/OmniNode-ai/onex_change_control.git",
}
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
MERGE_SHA = "c" * 40
OCC_SHA = "d" * 40
ARTIFACT = b"trusted artifact bytes\n"
CONTRACT = yaml.safe_dump(
    {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-17486",
        "dod_evidence": [
            {
                "id": "dod-canonical-request-binding",
                "description": "A contract-bound evidence item",
                "checks": [{"check_type": "test_passes", "check_value": "true"}],
            }
        ],
    },
    sort_keys=True,
).encode()
CONTRACT_FILE_DIGEST = f"sha256:{hashlib.sha256(CONTRACT).hexdigest()}"
CONTRACT_ENTRY_DIGEST = _compute_contract_entry_sha256(
    yaml.safe_load(CONTRACT), "dod-canonical-request-binding"
)
ARTIFACT_DIGEST = f"sha256:{hashlib.sha256(ARTIFACT).hexdigest()}"


def _ref(repo: dict[str, str], ref: str, sha: str) -> dict[str, object]:
    return {**repo, "ref": ref, "sha": sha}


def _subject(*, state: str = "OPEN", kind: str = "head") -> dict[str, object]:
    merge = None if state == "OPEN" else MERGE_SHA
    revision_sha = HEAD_SHA if kind == "head" else MERGE_SHA
    return {
        "schema_version": "occ-cross-repo-subject/v1",
        "purpose": "evidence_only",
        "repository": PRODUCT,
        "pull_request": {
            "repository": PRODUCT,
            "number": 123,
            "base": _ref(PRODUCT, "dev", BASE_SHA),
            "head": _ref(PRODUCT, "feature", HEAD_SHA),
            "state": state,
            "merge_commit_sha": merge,
        },
        "revision": {"kind": kind, "sha": revision_sha},
        "contract_source": {
            "repository": OCC,
            "commit_sha": OCC_SHA,
            "path": "contracts/OMN-17486.yaml",
            "file_sha256": CONTRACT_FILE_DIGEST,
            "entry_sha256": CONTRACT_ENTRY_DIGEST,
        },
        "artifact_sha256": ARTIFACT_DIGEST,
    }


def _api_repo(repo: dict[str, str]) -> dict[str, object]:
    return {
        "owner": {"login": repo["github_owner"]},
        "name": repo["github_name"],
        "clone_url": repo["canonical_remote"],
    }


def _api_ref(repo: dict[str, str], ref: str, sha: str) -> dict[str, object]:
    return {"repo": _api_repo(repo), "ref": ref, "sha": sha}


@dataclass
class FixtureTransport:
    responses: dict[str, ApiResponse]
    calls: list[str] = field(default_factory=list)

    def request(
        self, path: str, *, headers: dict[str, str], timeout: float
    ) -> ApiResponse:
        del headers, timeout
        self.calls.append(path)
        return self.responses[path]


def _fixtures(*, merged: bool = False) -> FixtureTransport:
    subject = _subject(
        state="MERGED" if merged else "OPEN", kind="merge_commit" if merged else "head"
    )
    pr = subject["pull_request"]
    assert isinstance(pr, dict)
    base = pr["base"]
    head = pr["head"]
    assert isinstance(base, dict)
    assert isinstance(head, dict)
    revision_sha = MERGE_SHA if merged else HEAD_SHA
    pr_payload = {
        "number": 123,
        "state": "closed" if merged else "open",
        "merged_at": "2026-09-02T00:00:00Z" if merged else None,
        "merge_commit_sha": MERGE_SHA if merged else None,
        "base": _api_ref(PRODUCT, "dev", BASE_SHA),
        "head": _api_ref(PRODUCT, "feature", HEAD_SHA),
    }
    responses: dict[str, ApiResponse] = {
        "repos/OmniNode-ai/omnibase_infra/pulls/123": _json(pr_payload),
        f"repos/OmniNode-ai/omnibase_infra/git/commits/{revision_sha}": _json(
            {"sha": revision_sha}
        ),
        f"repos/OmniNode-ai/omnibase_infra/commits/{revision_sha}/pulls": _json(
            [{"number": 123, "base": _api_ref(PRODUCT, "dev", BASE_SHA)}]
        ),
        f"repos/OmniNode-ai/onex_change_control/git/commits/{OCC_SHA}": _json(
            {"sha": OCC_SHA}
        ),
        "repos/OmniNode-ai/onex_change_control/contents/contracts/OMN-17486.yaml?ref="
        + OCC_SHA: _json(
            {
                "path": "contracts/OMN-17486.yaml",
                "encoding": "base64",
                "content": base64.b64encode(CONTRACT).decode(),
            }
        ),
    }
    if merged:
        responses[
            f"repos/OmniNode-ai/omnibase_infra/compare/{MERGE_SHA}...{BASE_SHA}"
        ] = _json(
            {
                "status": "ahead",
                "base_commit": {"sha": MERGE_SHA},
                "merge_base_commit": {"sha": MERGE_SHA},
            }
        )
    return FixtureTransport(responses)


def _json(value: object, status: int = 200) -> ApiResponse:
    import json

    return ApiResponse(status, {"etag": '"fixture"'}, json.dumps(value).encode())


@pytest.mark.unit
def test_valid_subject_is_offline_unevaluated_without_network() -> None:
    result = validate_offline(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        contract_bytes=CONTRACT,
        artifact_bytes=ARTIFACT,
    )
    assert result == CrossRepoValidationResult(
        CrossRepoStatus.UNEVALUATED,
        ("cross-repo subject requires the online GitHub snapshot",),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("activate", True),
        ("authorization", "execute"),
        ("repository", {**PRODUCT, "canonical_remote": "https://evil.example/x.git"}),
    ],
)
def test_subject_rejects_spoof_and_activation_fields(field: str, value: object) -> None:
    raw = _subject()
    raw[field] = value
    with pytest.raises(ValidationError):
        CrossRepoSubject.model_validate(raw)


@pytest.mark.unit
def test_fork_head_and_wrong_case_are_rejected() -> None:
    fork = _subject()
    pr = fork["pull_request"]
    assert isinstance(pr, dict)
    pr["head"] = _ref(
        {
            "github_owner": "attacker",
            "github_name": "omnibase_infra",
            "canonical_remote": "https://github.com/attacker/omnibase_infra.git",
        },
        "feature",
        HEAD_SHA,
    )
    with pytest.raises(ValidationError):
        CrossRepoSubject.model_validate(fork)

    wrong_case = _subject()
    wrong_case["repository"] = {
        **PRODUCT,
        "github_owner": "omninode-ai",
        "canonical_remote": "https://github.com/omninode-ai/omnibase_infra.git",
    }
    with pytest.raises(ValidationError):
        CrossRepoSubject.model_validate(wrong_case)


@pytest.mark.unit
def test_shape_rejects_short_uppercase_sha_and_oversized_nested_values() -> None:
    for key, value in (("commit_sha", "A" * 40), ("commit_sha", "a" * 39)):
        raw = _subject()
        source = raw["contract_source"]
        assert isinstance(source, dict)
        source[key] = value
        with pytest.raises(ValidationError):
            CrossRepoSubject.model_validate(raw)

    raw = _subject()
    pr = raw["pull_request"]
    assert isinstance(pr, dict)
    head = pr["head"]
    assert isinstance(head, dict)
    head["ref"] = "x" * 257
    with pytest.raises(ValidationError):
        CrossRepoSubject.model_validate(raw)


@pytest.mark.unit
def test_open_subject_cannot_claim_a_merge_commit() -> None:
    raw = _subject()
    pr = raw["pull_request"]
    assert isinstance(pr, dict)
    pr["merge_commit_sha"] = MERGE_SHA
    with pytest.raises(ValidationError):
        CrossRepoSubject.model_validate(raw)


@pytest.mark.unit
def test_open_subject_resolves_with_exact_identity_and_bounded_calls() -> None:
    transport = _fixtures()
    result = CrossRepoSubjectResolver(transport=transport).resolve(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.PASS
    assert result.api_calls == 5
    assert len(transport.calls) == 5


@pytest.mark.unit
def test_merged_subject_requires_merge_ancestry_and_validates_squash_commit() -> None:
    transport = _fixtures(merged=True)
    result = CrossRepoSubjectResolver(transport=transport).resolve(
        _subject(state="MERGED", kind="merge_commit"),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.PASS
    assert result.api_calls == 6


@pytest.mark.unit
def test_wrong_repo_sha_and_stale_head_are_rejected() -> None:
    transport = _fixtures()
    transport.responses[f"repos/OmniNode-ai/omnibase_infra/git/commits/{HEAD_SHA}"] = (
        _json({"sha": "e" * 40})
    )
    result = CrossRepoSubjectResolver(transport=transport).resolve(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL

    transport = _fixtures()
    transport.responses["repos/OmniNode-ai/omnibase_infra/pulls/123"] = _json(
        {
            "number": 123,
            "state": "open",
            "merged_at": None,
            "merge_commit_sha": None,
            "base": _api_ref(PRODUCT, "dev", BASE_SHA),
            "head": _api_ref(PRODUCT, "feature", "e" * 40),
        }
    )
    result = CrossRepoSubjectResolver(transport=transport).resolve(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL


@pytest.mark.unit
def test_missing_membership_and_unrelated_ancestry_fail_closed() -> None:
    transport = _fixtures()
    transport.responses[
        f"repos/OmniNode-ai/omnibase_infra/commits/{HEAD_SHA}/pulls"
    ] = _json([])
    result = CrossRepoSubjectResolver(transport=transport).resolve(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL

    transport = _fixtures(merged=True)
    transport.responses[
        f"repos/OmniNode-ai/omnibase_infra/compare/{MERGE_SHA}...{BASE_SHA}"
    ] = _json(
        {
            "status": "diverged",
            "base_commit": {"sha": MERGE_SHA},
            "merge_base_commit": {"sha": "e" * 40},
        }
    )
    result = CrossRepoSubjectResolver(transport=transport).resolve(
        _subject(state="MERGED", kind="merge_commit"),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL


@pytest.mark.unit
@pytest.mark.parametrize("status", [401, 403, 404, 422, 429])
def test_transport_status_never_becomes_pass(status: int) -> None:
    transport = _fixtures()
    transport.responses["repos/OmniNode-ai/omnibase_infra/pulls/123"] = _json(
        {}, status
    )
    result = CrossRepoSubjectResolver(
        transport=transport, sleep=lambda _: None
    ).resolve(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL
    assert result.api_calls <= 12
    if status == 429:
        assert result.api_calls == 1


@pytest.mark.unit
def test_timeout_and_malformed_json_are_unavailable() -> None:
    class TimeoutTransport:
        def request(
            self, path: str, *, headers: dict[str, str], timeout: float
        ) -> ApiResponse:
            del path, headers, timeout
            raise TimeoutError("fixture timeout")  # noqa: EM101, TRY003

    result = CrossRepoSubjectResolver(transport=TimeoutTransport()).resolve(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL
    assert result.api_calls == 1

    transport = _fixtures()
    transport.responses["repos/OmniNode-ai/omnibase_infra/pulls/123"] = ApiResponse(
        200, {}, b"not-json"
    )
    result = CrossRepoSubjectResolver(transport=transport).resolve(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL


@pytest.mark.unit
def test_response_after_deadline_and_malformed_envelope_fail_closed() -> None:
    class AdvancingTransport:
        def request(
            self, path: str, *, headers: dict[str, str], timeout: float
        ) -> ApiResponse:
            del path, headers, timeout
            clock[0] = 16.0
            return _json({"ok": True})

    clock = [0.0]
    result = CrossRepoSubjectResolver(
        transport=AdvancingTransport(), monotonic=lambda: clock[0]
    ).resolve(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL
    assert result.api_calls == 1

    class MalformedTransport:
        def request(
            self, path: str, *, headers: dict[str, str], timeout: float
        ) -> ApiResponse:
            del path, headers, timeout
            return ApiResponse(200, {}, "not-bytes")  # type: ignore[arg-type]

    result = CrossRepoSubjectResolver(transport=MalformedTransport()).resolve(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL


@pytest.mark.unit
def test_artifact_and_contract_digest_mismatches_fail_offline() -> None:
    result = validate_offline(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        contract_bytes=b"wrong",
        artifact_bytes=b"wrong",
    )
    assert result.status is CrossRepoStatus.FAIL
    assert "contract file digest" in " ".join(result.details)
    assert "artifact digest" in " ".join(result.details)


@pytest.mark.unit
def test_wrong_contract_ticket_and_non_bytes_fail_closed() -> None:
    wrong_contract = CONTRACT.replace(b"OMN-17486", b"OMN-99999")
    raw = _subject()
    source = raw["contract_source"]
    assert isinstance(source, dict)
    source["file_sha256"] = f"sha256:{hashlib.sha256(wrong_contract).hexdigest()}"
    result = validate_offline(
        raw,
        evidence_item_id="dod-canonical-request-binding",
        contract_bytes=wrong_contract,
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL
    assert "OMN-17486" in " ".join(result.details)

    result = validate_offline(
        _subject(),
        contract_bytes="not bytes",  # type: ignore[arg-type]
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL
    assert "contract_bytes must be bytes" in result.details


@pytest.mark.unit
def test_generic_transport_exception_becomes_bounded_failure() -> None:
    class BrokenTransport:
        def request(
            self, path: str, *, headers: dict[str, str], timeout: float
        ) -> ApiResponse:
            del path, headers, timeout
            raise RuntimeError("fixture transport broke")  # noqa: EM101, TRY003

    result = CrossRepoSubjectResolver(transport=BrokenTransport()).resolve(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL
    assert result.api_calls == 1
    assert result.details == ("GitHub transport failed",)


@pytest.mark.unit
def test_http_parser_preserves_blank_lines_in_body() -> None:
    status, headers, body = _parse_http_response(
        b"""HTTP/2 200\r\nETag: "fixture"\r\n\r\n{"content":\n\n"ok"}\n"""
    )
    assert status == 200
    assert headers == {"etag": '"fixture"'}
    assert body == b'{"content":\n\n"ok"}\n'


@pytest.mark.unit
def test_schema_is_allowlisted_and_rejects_spoof() -> None:
    import jsonschema

    schema_path = Path("schemas/occ_cross_repo_subject_v1.schema.yaml")
    schema = yaml.safe_load(schema_path.read_bytes())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(_subject(), schema)

    spoof = _subject()
    spoof["repository"] = {
        "github_owner": "attacker",
        "github_name": "omnibase_infra",
        "canonical_remote": "https://github.com/attacker/omnibase_infra.git",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(spoof, schema)


@pytest.mark.unit
def test_whitespace_only_ref_is_rejected() -> None:
    raw = _subject()
    pull_request = raw["pull_request"]
    assert isinstance(pull_request, dict)
    base = pull_request["base"]
    assert isinstance(base, dict)
    base["ref"] = "   "
    with pytest.raises(ValidationError):
        CrossRepoSubject.model_validate(raw)


@pytest.mark.unit
def test_tombstone_without_replacement_is_inert(tmp_path: Path) -> None:
    path = tmp_path / "command.supersede.0001.yaml"
    path.write_text(
        "schema_version: '1.0.0'\nticket_id: OMN-1\ntombstone: true\n",
        encoding="utf-8",
    )
    assert _load_mapping(path) == {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-1",
        "tombstone": True,
    }


@pytest.mark.unit
def test_call_budget_is_hard_bound() -> None:
    transport = _fixtures()
    result = CrossRepoSubjectResolver(transport=transport, max_calls=1).resolve(
        _subject(),
        evidence_item_id="dod-canonical-request-binding",
        artifact_bytes=ARTIFACT,
    )
    assert result.status is CrossRepoStatus.FAIL
    assert result.api_calls == 1
    assert len(transport.calls) == 1
