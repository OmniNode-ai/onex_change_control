# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Governed-lane compose-config grant anchor (OMN-20260).

Every refusal is paired with a control the same validator accepts, because a
validator that refuses everything would pass a refusal test and prove nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from onex_change_control.scripts.validate_governed_lane_grants import (
    ALLOWED_LANES,
    main,
    validate_governed_lane_grants,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ANCHOR = REPO_ROOT / "grants" / "governed_lane_grants.yaml"
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "grant_id": "grant-6f1c2b3a-1d2e-4f50-8a9b-0c1d2e3f4a5b",
        "target_kind": "compose_config",
        "runtime_lane": "stability-test",
        "compose_project": "omnibase-infra-stability-test",
        "compose_files": [
            "docker/docker-compose.infra.yml",
            "docker/docker-compose.stability-test.yml",
        ],
        "env_files": [],
        "profiles": [],
        "services": ["redpanda"],
        "compose_ref": "0" * 40,
        "rendered_digest": "sha256:" + "a" * 64,
        "requested_by": "jonahgabriel",
        "approved_by": "jake-b-omni",
        "expires_at": "2026-10-02T12:00:00Z",
        "created_at": "2026-10-01T11:00:00Z",
        "reason": "OMN-20260 loopback admin bind",
    }
    entry.update(overrides)
    return entry


def _write(tmp_path: Path, entries: list[Any], name: str = "g.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump({"entries": entries}), encoding="utf-8")
    return path


def _validate(tmp_path: Path, entries: list[Any], **kw: Any) -> list[str]:
    result = validate_governed_lane_grants(_write(tmp_path, entries), now=NOW, **kw)
    return result.errors


@pytest.mark.unit
def test_committed_anchor_is_valid_and_at_rest() -> None:
    result = validate_governed_lane_grants(ANCHOR)
    assert result.passed, result.errors
    assert result.entry_count == 0


@pytest.mark.unit
def test_well_formed_entry_is_accepted(tmp_path: Path) -> None:
    assert _validate(tmp_path, [_entry()]) == []


@pytest.mark.unit
def test_allowed_lanes_are_pinned() -> None:
    assert frozenset({"stability-test", "judge"}) == ALLOWED_LANES


@pytest.mark.unit
@pytest.mark.parametrize("lane", ["prod", "dev", "lakshman"])
def test_lane_outside_the_governed_set_is_refused(tmp_path: Path, lane: str) -> None:
    errors = _validate(
        tmp_path, [_entry(runtime_lane=lane, compose_project=f"omnibase-infra-{lane}")]
    )
    assert any("runtime_lane must be one of" in e for e in errors)


@pytest.mark.unit
def test_judge_lane_is_accepted(tmp_path: Path) -> None:
    entry = _entry(
        runtime_lane="judge",
        compose_project="omnibase-infra-judge",
        compose_files=["docker/docker-compose.judge.yml"],
        env_files=["docker/runtime-policy.env", "docker/judge.env"],
        profiles=["judge"],
    )
    assert _validate(tmp_path, [entry]) == []


@pytest.mark.unit
def test_project_must_match_lane(tmp_path: Path) -> None:
    errors = _validate(tmp_path, [_entry(compose_project="omnibase-infra-judge")])
    assert any("compose_project must be" in e for e in errors)


@pytest.mark.unit
def test_self_granted_entry_is_refused(tmp_path: Path) -> None:
    errors = _validate(tmp_path, [_entry(approved_by="JonahGabriel")])
    assert any("self_granted" in e for e in errors)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_kind", "image"),
        ("compose_ref", "main"),
        ("compose_ref", "abc1234"),
        ("rendered_digest", "a" * 64),
        ("compose_files", []),
        ("compose_files", ["docker/../secrets.yml"]),
        ("compose_files", ["/etc/compose.yml"]),
        ("compose_files", ["docker/a.yml", "docker/a.yml"]),
        ("env_files", ["../home.env"]),
        ("services", []),
        ("services", ["Redpanda; rm"]),
        ("expires_at", "2026-10-02"),
        ("reason", " "),
    ],
)
def test_malformed_field_is_refused(tmp_path: Path, field: str, value: Any) -> None:
    errors = _validate(tmp_path, [_entry(**{field: value})])
    assert errors, f"{field}={value!r} was accepted"


@pytest.mark.unit
def test_missing_and_extra_fields_are_refused(tmp_path: Path) -> None:
    entry = _entry(image_digest="sha256:" + "b" * 64)
    del entry["rendered_digest"]
    errors = _validate(tmp_path, [entry])
    assert any("missing required fields" in e for e in errors)
    assert any("unexpected fields" in e for e in errors)


@pytest.mark.unit
def test_expired_and_inverted_windows_are_refused(tmp_path: Path) -> None:
    assert any(
        "EXPIRED" in e
        for e in _validate(
            tmp_path,
            [
                _entry(
                    created_at="2026-09-01T00:00:00Z", expires_at="2026-09-02T00:00:00Z"
                )
            ],
        )
    )
    assert any(
        "strictly after" in e
        for e in _validate(tmp_path, [_entry(expires_at="2026-10-01T10:00:00Z")])
    )


@pytest.mark.unit
def test_duplicate_grant_id_is_refused(tmp_path: Path) -> None:
    errors = _validate(tmp_path, [_entry(), _entry()])
    assert any("duplicate grant_id" in e for e in errors)


@pytest.mark.unit
def test_requester_may_not_approve_an_added_entry(tmp_path: Path) -> None:
    errors = _validate(tmp_path, [_entry()], requester="jake-b-omni")
    assert any("opened this change" in e for e in errors)
    # Control: a different requester is not refused.
    assert _validate(tmp_path, [_entry()], requester="jonahgabriel") == []


@pytest.mark.unit
def test_requester_check_is_diff_scoped_and_fails_closed(tmp_path: Path) -> None:
    base = _write(tmp_path, [_entry()], name="base.yaml")
    # The entry exists at base, so an unrelated PR by its approver is not refused.
    assert (
        _validate(tmp_path, [_entry()], requester="jake-b-omni", base_file=base) == []
    )
    # An unreadable base treats every entry as new: refuses more, never less.
    missing = tmp_path / "absent.yaml"
    errors = _validate(tmp_path, [_entry()], requester="jake-b-omni", base_file=missing)
    assert any("opened this change" in e for e in errors)


@pytest.mark.unit
def test_cli_exit_codes(tmp_path: Path) -> None:
    good = _write(tmp_path, [], name="good.yaml")
    bad = _write(tmp_path, [_entry(approved_by="jonahgabriel")], name="bad.yaml")
    assert main(["--file", str(good)]) == 0
    assert main(["--file", str(bad)]) == 1
    assert main(["--file", str(good), "--requester", " "]) == 1
    assert main(["--file", str(tmp_path / "nope.yaml")]) == 1
