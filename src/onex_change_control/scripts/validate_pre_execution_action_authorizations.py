# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Validate the canonical pre-execution action-authorization registry.

The registry is intentionally separate from production-promotion and
staging-namespace grants.  A pre-execution authorization is a bounded,
single-use permit for exactly one action and binds all of the following:

* the Linear ticket and its central OCC contract path;
* the immutable OCC commit and SHA-256 digest of that contract at that commit;
* one action, source commit, and immutable artifact digest;
* the exact database, schema, service, and principal; and
* an issuer, absolute expiry, unique nonce, and one-time-use lifecycle.

This validator verifies the cryptographic contract binding with ``git show``.
An action executor must additionally atomically claim the nonce before the
action begins; a YAML registry cannot itself serialize concurrent consumers.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REQUIRED_FIELDS = frozenset(
    {
        "authorization_id",
        "ticket_id",
        "contract_path",
        "contract_commit_sha",
        "contract_sha256",
        "action_id",
        "source_sha",
        "artifact_sha256",
        "target_database",
        "target_schema",
        "target_service",
        "target_principal",
        "execute_enabled",
        "issuer",
        "nonce",
        "issued_at",
        "expires_at",
        "one_time_use",
        "reason",
    }
)
OPTIONAL_FIELDS = frozenset({"consumed", "consumed_at", "consumed_by_correlation_id"})

AUTHORIZATION_ID_RE = re.compile(
    r"^action-auth-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
TICKET_ID_RE = re.compile(r"^OMN-[1-9][0-9]*$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ACTION_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
ISSUER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32,128}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$")

ContractLoader = Callable[[str, str], bytes]


@dataclass(frozen=True)
class ModelPreExecutionAuthorizationValidationResult:
    """The fail-closed result of validating a registry file."""

    passed: bool
    errors: list[str]
    entry_count: int


def parse_iso8601(value: str) -> datetime | None:
    """Parse a strict ISO-8601 instant."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_contract_at_commit(commit_sha: str, contract_path: str) -> bytes:
    """Read one canonical contract blob from a locally available OCC commit."""
    git_executable = shutil.which("git")
    if git_executable is None:
        message = "git executable is unavailable for contract binding"
        raise ValueError(message)
    completed = subprocess.run(  # noqa: S603
        [git_executable, "show", f"{commit_sha}:{contract_path}"],
        cwd=_repository_root(),
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        message = f"cannot read {contract_path!r} at OCC commit {commit_sha}: {detail}"
        raise ValueError(message)
    return completed.stdout


def _error_for_exact_fields(index: int, entry: dict[str, Any]) -> list[str]:
    present = set(entry)
    missing = REQUIRED_FIELDS - present
    extra = present - REQUIRED_FIELDS - OPTIONAL_FIELDS
    errors: list[str] = []
    if missing:
        errors.append(f"Entry[{index}]: missing required fields: {sorted(missing)}")
    if extra:
        errors.append(f"Entry[{index}]: unexpected fields: {sorted(extra)}")
    return errors


def _error_for_contract_identity(index: int, entry: dict[str, Any]) -> list[str]:
    prefix = f"Entry[{index}]"
    errors: list[str] = []
    if not isinstance(
        entry["authorization_id"], str
    ) or not AUTHORIZATION_ID_RE.fullmatch(entry["authorization_id"]):
        errors.append(f"{prefix}: authorization_id must match action-auth-<uuid4>")
    if not isinstance(entry["ticket_id"], str) or not TICKET_ID_RE.fullmatch(
        entry["ticket_id"]
    ):
        errors.append(f"{prefix}: ticket_id must match OMN-<positive integer>")
    expected_path = f"contracts/{entry['ticket_id']}.yaml"
    if entry["contract_path"] != expected_path:
        errors.append(
            f"{prefix}: contract_path must be exactly {expected_path!r}, got "
            f"{entry['contract_path']!r}"
        )
    for field, pattern, description in (
        ("contract_commit_sha", SHA1_RE, "40 lowercase hex characters"),
        ("source_sha", SHA1_RE, "40 lowercase hex characters"),
        ("contract_sha256", SHA256_RE, "sha256:<64 lowercase hex>"),
        ("artifact_sha256", SHA256_RE, "sha256:<64 lowercase hex>"),
    ):
        value = entry[field]
        if not isinstance(value, str) or not pattern.fullmatch(value):
            errors.append(f"{prefix}: {field} must be {description}, got {value!r}")
    return errors


def _error_for_action_and_target(index: int, entry: dict[str, Any]) -> list[str]:
    prefix = f"Entry[{index}]"
    errors: list[str] = []
    for field, pattern, description in (
        ("action_id", ACTION_ID_RE, "a bounded lowercase action identifier"),
        ("issuer", ISSUER_RE, "a bounded issuer identity"),
        ("nonce", NONCE_RE, "32-128 lowercase hex characters"),
    ):
        value = entry[field]
        if not isinstance(value, str) or not pattern.fullmatch(value):
            errors.append(f"{prefix}: {field} must be {description}, got {value!r}")
    for field in (
        "target_database",
        "target_schema",
        "target_service",
        "target_principal",
    ):
        value = entry[field]
        if not isinstance(value, str) or not IDENTITY_RE.fullmatch(value):
            errors.append(
                f"{prefix}: {field} must be one exact lowercase identity without "
                f"wildcards, got {value!r}"
            )
    if entry["execute_enabled"] is not False:
        errors.append(
            f"{prefix}: execute_enabled must be exactly false for a pre-execution "
            "authorization"
        )
    if entry["one_time_use"] is not True:
        errors.append(f"{prefix}: one_time_use must be exactly true")
    if not isinstance(entry["reason"], str) or not entry["reason"].strip():
        errors.append(f"{prefix}: reason must be a non-empty string")
    return errors


def _error_for_timestamps(
    index: int, entry: dict[str, Any], now: datetime
) -> list[str]:
    prefix = f"Entry[{index}]"
    parsed: dict[str, datetime | None] = {}
    errors: list[str] = []
    for field in ("issued_at", "expires_at"):
        value = entry[field]
        if not isinstance(value, str) or not ISO8601_RE.fullmatch(value):
            errors.append(f"{prefix}: {field} must be an ISO-8601 instant")
            parsed[field] = None
        else:
            parsed[field] = parse_iso8601(value)
    issued_at = parsed.get("issued_at")
    expires_at = parsed.get("expires_at")
    if issued_at is not None and expires_at is not None and expires_at <= issued_at:
        errors.append(f"{prefix}: expires_at must be strictly after issued_at")
    if expires_at is not None and expires_at <= now:
        errors.append(f"{prefix}: authorization is EXPIRED")
    return errors


def _error_for_lifecycle(index: int, entry: dict[str, Any]) -> list[str]:
    prefix = f"Entry[{index}]"
    consumed = entry.get("consumed", False)
    consumed_at = entry.get("consumed_at")
    correlation_id = entry.get("consumed_by_correlation_id")
    errors: list[str] = []
    if not isinstance(consumed, bool):
        errors.append(f"{prefix}: consumed must be a bool when present")
        return errors
    if consumed:
        if not isinstance(consumed_at, str) or not ISO8601_RE.fullmatch(consumed_at):
            errors.append(
                f"{prefix}: consumed authorization requires ISO-8601 consumed_at"
            )
        if not isinstance(correlation_id, str) or not UUID_RE.fullmatch(correlation_id):
            errors.append(
                f"{prefix}: consumed authorization requires UUID "
                "consumed_by_correlation_id"
            )
    elif consumed_at is not None or correlation_id is not None:
        errors.append(
            f"{prefix}: unconsumed authorization may not carry consumption markers"
        )
    return errors


def _error_for_contract_binding(
    index: int, entry: dict[str, Any], contract_loader: ContractLoader
) -> list[str]:
    prefix = f"Entry[{index}]"
    if not isinstance(entry["contract_path"], str):
        return [f"{prefix}: contract_path must be a string"]
    if not isinstance(entry["contract_commit_sha"], str):
        return [f"{prefix}: contract_commit_sha must be a string"]
    if not isinstance(entry["contract_sha256"], str):
        return [f"{prefix}: contract_sha256 must be a string"]
    try:
        blob = contract_loader(entry["contract_commit_sha"], entry["contract_path"])
    except ValueError as exc:
        return [f"{prefix}: contract binding failed: {exc}"]
    actual = f"sha256:{hashlib.sha256(blob).hexdigest()}"
    if actual != entry["contract_sha256"]:
        return [
            f"{prefix}: contract_sha256 mismatch for "
            f"{entry['contract_commit_sha']}:{entry['contract_path']}"
        ]
    return []


def _validate_entry(
    index: int,
    entry: Any,
    *,
    now: datetime,
    contract_loader: ContractLoader,
) -> list[str]:
    if not isinstance(entry, dict):
        return [f"Entry[{index}]: must be a mapping"]
    errors = _error_for_exact_fields(index, entry)
    if errors:
        return errors
    errors = [
        *_error_for_contract_identity(index, entry),
        *_error_for_action_and_target(index, entry),
        *_error_for_timestamps(index, entry, now),
        *_error_for_lifecycle(index, entry),
    ]
    # Do not ask the loader to read an invalid path/ref shape. The entry is
    # already rejected, and this avoids turning a useful validation finding
    # into an exception from an injected loader.
    if not errors:
        errors.extend(_error_for_contract_binding(index, entry, contract_loader))
    return errors


def validate_pre_execution_authorizations(
    file_path: Path,
    *,
    now: datetime | None = None,
    contract_loader: ContractLoader = _load_contract_at_commit,
) -> ModelPreExecutionAuthorizationValidationResult:
    """Validate the registry without creating, consuming, or executing anything."""
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return ModelPreExecutionAuthorizationValidationResult(
            passed=False, errors=[f"Cannot parse {file_path}: {exc}"], entry_count=0
        )
    if not isinstance(data, dict) or set(data) != {"entries"}:
        return ModelPreExecutionAuthorizationValidationResult(
            passed=False,
            errors=[f"{file_path} must contain exactly one top-level key: entries"],
            entry_count=0,
        )
    entries = data["entries"]
    if not isinstance(entries, list):
        return ModelPreExecutionAuthorizationValidationResult(
            passed=False, errors=[f"{file_path} entries must be a list"], entry_count=0
        )
    evaluation_time = now or datetime.now(UTC)
    errors: list[str] = []
    authorization_ids: set[str] = set()
    nonces: set[str] = set()
    for index, entry in enumerate(entries):
        errors.extend(
            _validate_entry(
                index, entry, now=evaluation_time, contract_loader=contract_loader
            )
        )
        if isinstance(entry, dict):
            for field, seen in (
                ("authorization_id", authorization_ids),
                ("nonce", nonces),
            ):
                value = entry.get(field)
                if isinstance(value, str):
                    if value in seen:
                        errors.append(f"Entry[{index}]: duplicate {field} {value!r}")
                    seen.add(value)
    return ModelPreExecutionAuthorizationValidationResult(
        passed=not errors, errors=errors, entry_count=len(entries)
    )


def main(argv: list[str] | None = None) -> int:
    """Run the canonical registry validator."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--file",
        default="grants/pre_execution_action_authorizations.yaml",
        help="Path to the pre-execution action-authorization registry.",
    )
    args = parser.parse_args(argv)
    result = validate_pre_execution_authorizations(Path(args.file))
    if not result.passed:
        print(f"FAIL: {args.file} has {len(result.errors)} violation(s):")
        for error in result.errors:
            print(f"  - {error}")
        return 1
    print(f"PASS: {args.file} — {result.entry_count} authorization(s) validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
