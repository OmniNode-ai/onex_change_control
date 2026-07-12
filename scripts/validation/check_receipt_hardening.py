# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Receipt hardening gate (OMN-13060, retro item A-5; per-entry hash OMN-14411).

Enforces three invariants on DoD receipts produced on/after the gate
introduction date (``run_timestamp >= 2026-06-12T00:00:00Z``):

1. A contract hash binding must be present: ``contract_entry_sha256``
   (preferred, OMN-13888) or ``contract_sha256`` (legacy, whole-file).
   Hand-authored sha-less receipts (the 2530/2533/2534 failure mode) are
   rejected at commit time instead of wedging the OCC PR at the merge gate.
2. The binding must match the pinned contract at the staged state
   (OMN-14411):
   - ``contract_entry_sha256``, when present, is authoritative and is
     checked against ``compute_contract_entry_sha256`` of the receipt's own
     ``dod_evidence[evidence_item_id]`` — the same per-entry hash the
     append-only gate (``validator_occ_append_only``) already enforces.
     Because this hash only folds in the receipt's own entry plus the
     immutable header, appending a *new* dod_evidence item to the contract
     does not change it, so prior receipts stay valid across appends.
   - ``contract_sha256`` (legacy, no ``contract_entry_sha256`` present) is
     checked against the whole-file ``sha256(contracts/<ticket_id>.yaml)``.
     This binding is inherently incompatible with the append-only contract
     model — it goes stale on *every* append to the contract, regardless of
     which entry changed — and exists only for receipts minted before
     OMN-13888. Do not mint new receipts against it.
   A mismatch means the bound entry (or, for legacy receipts, the whole
   file) mutated after the receipt was produced — rerun probes, regenerate
   the receipt.
3. PASS receipts may not use a session-local / generic verifier alias
   (``agent``, ``automated``, bare container ids, ...). A PASS receipt
   must name an identifiable independent verifier.

Receipts with ``run_timestamp`` before the introduction date are exempt:
2,870 legacy receipts on dev lack the field (907 more carry stale hashes,
382 carry denylisted verifiers) and are migration debt owned by their own
tickets — this gate is a ratchet on NEW receipts, not a retro-block.
``pre-commit run --all-files`` (which OCC CI runs) must stay green on the
legacy corpus.

Exit codes: 0 = all enforced receipts clean; 1 = violations found.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from omnibase_core.enums.ticket.enum_receipt_status import EnumReceiptStatus
from omnibase_core.models.contracts.ticket.model_dod_receipt import ModelDodReceipt

try:
    from omnibase_core.validation.validator_receipt_gate import (
        ContractEntryNotFoundError,
        compute_contract_entry_sha256,
    )
except ImportError:
    ContractEntryNotFoundError = LookupError

    def compute_contract_entry_sha256(
        contract_data: object, evidence_item_id: str
    ) -> str:
        """Compute the OMN-13888 per-entry contract hash locally.

        Hosted OCC CI can run against an older omnibase_core wheel that has the
        whole-file hash helper but not the per-entry helper. Keep this gate
        self-contained so new receipts can still prefer contract_entry_sha256.
        """
        entry: dict[str, object] | None = None
        if isinstance(contract_data, dict):
            items = contract_data.get("dod_evidence", [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("id") == evidence_item_id:
                        entry = item
                        break
        if entry is None:
            msg = f"dod_evidence item {evidence_item_id!r} not found in contract"
            raise ContractEntryNotFoundError(msg)
        header = {
            key: (contract_data.get(key) if isinstance(contract_data, dict) else None)
            for key in ("ticket_id", "schema_version")
        }
        blob = json.dumps(
            {"header": header, "entry": entry},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return f"sha256:{hashlib.sha256(blob.encode('utf-8')).hexdigest()}"


from omnibase_core.validation.validator_receipt_gate import (
    compute_contract_sha256,
)
from pydantic import ValidationError

# Receipts produced on/after this UTC instant are subject to the gate.
# Earlier receipts are legacy migration debt (see module docstring).
HARDENING_CUTOFF = datetime(2026, 6, 12, 0, 0, 0, tzinfo=UTC)

# Session-local / generic verifier aliases that cannot satisfy independent
# verification for a PASS receipt. Exact match after strip().lower().
# Seeded from the 2026-06-12 verifier survey on OCC dev (retro A-5(c)).
DENYLISTED_VERIFIERS = frozenset(
    {
        "agent",
        "automated",
        "self",
        "local",
        "session",
        "manual",
        "human",
        "foreground-orchestrator",
        "local-pytest",
        "local-pre-push",
        "runner-session",
    }
)

# Pattern-based denials: bare docker container ids and runner-session-scoped
# aliases are container names, not verifier identities.
DENYLISTED_VERIFIER_PATTERNS = (
    re.compile(r"^[0-9a-f]{12}$"),  # bare docker container id
    re.compile(r"^runner-session\b"),
    re.compile(r"^session-"),
)


def _is_denylisted_verifier(verifier: str) -> bool:
    normalized = verifier.strip().lower()
    if normalized in DENYLISTED_VERIFIERS:
        return True
    return any(p.match(normalized) for p in DENYLISTED_VERIFIER_PATTERNS)


def _supersession_candidates(receipt_path: Path) -> list[Path]:
    """Return append-only supersession files that may replace receipt_path."""
    if receipt_path.suffix != ".yaml":
        return []
    stem = receipt_path.name[: -len(receipt_path.suffix)]
    return sorted(receipt_path.parent.glob(f"{stem}.supersede.*{receipt_path.suffix}"))


def _contract_hash_violation(
    receipt: ModelDodReceipt, contract_path: Path
) -> str | None:
    """Return a violation string if receipt's contract hash binding is stale.

    Honors the entry-hash-first dual-accept policy (OMN-13888 mint order,
    OMN-14411 gate parity): the caller has already confirmed at least one of
    ``contract_entry_sha256`` / ``contract_sha256`` is set. Returns ``None``
    when the receipt is bound cleanly to the current contract.
    """
    contract_entry_sha256 = getattr(receipt, "contract_entry_sha256", None)
    if contract_entry_sha256 is not None:
        try:
            contract_data = yaml.safe_load(contract_path.read_text())
        except (OSError, yaml.YAMLError) as exc:
            return f"unreadable contract YAML at {contract_path}: {exc}"
        try:
            expected_entry = compute_contract_entry_sha256(
                contract_data, receipt.evidence_item_id
            )
        except ContractEntryNotFoundError:
            return (
                f"dod_evidence item {receipt.evidence_item_id!r} not found in "
                f"{contract_path}; contract_entry_sha256 cannot be validated. "
                "The entry was removed or renamed after this receipt was "
                "produced (append-only violation) — do not fabricate a hash."
            )
        if contract_entry_sha256 != expected_entry:
            return (
                "contract_entry_sha256 mismatch — receipt has "
                f"{contract_entry_sha256!r} but "
                f"dod_evidence[{receipt.evidence_item_id!r}] in {contract_path} "
                f"hashes to {expected_entry!r}. That entry was edited after this "
                "receipt was produced; rerun probes and regenerate the receipt."
            )
        return None

    expected_whole = f"sha256:{compute_contract_sha256(contract_path)}"
    if receipt.contract_sha256 != expected_whole:
        return (
            f"contract_sha256 mismatch — receipt has {receipt.contract_sha256!r} "
            f"but sha256({contract_path}) is {expected_whole!r}. The contract "
            "mutated after this receipt was produced; rerun probes and "
            "regenerate the receipt (mint contract_entry_sha256 per OMN-13888 "
            "so future appends to other entries do not invalidate it again)."
        )
    return None


def _receipt_binding_violations(
    receipt: ModelDodReceipt, contracts_dir: Path
) -> list[str]:
    """Return contract-binding + verifier violation fragments for one receipt.

    Shared core for both the primary per-file check and supersession
    replacement validation (OMN-14411), so the two paths cannot drift out of
    sync. Fragments carry no receipt/candidate path prefix; callers prepend
    their own.
    """
    violations: list[str] = []

    contract_entry_sha256 = getattr(receipt, "contract_entry_sha256", None)
    if receipt.contract_sha256 is None and contract_entry_sha256 is None:
        violations.append(
            "missing contract_sha256 (OMN-13060/A-5). Tool-generate the "
            "receipt; never hand-author. Prefer contract_entry_sha256 "
            f"(OMN-13888) — the per-entry hash of "
            f"dod_evidence[{receipt.evidence_item_id!r}] in "
            f"contracts/{receipt.ticket_id}.yaml — which survives later "
            "appends to the contract; legacy contract_sha256 is "
            f"sha256(contracts/{receipt.ticket_id}.yaml)."
        )
    else:
        contract_path = contracts_dir / f"{receipt.ticket_id}.yaml"
        if not contract_path.is_file():
            violations.append(
                f"contract {contract_path} does not exist for ticket "
                f"{receipt.ticket_id}"
            )
        else:
            mismatch = _contract_hash_violation(receipt, contract_path)
            if mismatch is not None:
                violations.append(mismatch)

    if receipt.status is EnumReceiptStatus.PASS and _is_denylisted_verifier(
        receipt.verifier
    ):
        violations.append(
            f"PASS receipt uses session-local verifier alias "
            f"{receipt.verifier!r} (OMN-13060/A-5). Name an identifiable "
            "independent verifier."
        )

    return violations


def _valid_supersession_replacement(
    receipt_path: Path, contracts_dir: Path
) -> tuple[bool, list[str]]:
    """Return whether a sibling supersession cleanly replaces receipt_path.

    OCC receipts are append-only: after a contract changes, the old receipt is
    preserved and a net-new ``command.supersede.NNNN.yaml`` carries the rebound
    receipt under ``replacement``. This gate should validate the authoritative
    replacement instead of requiring mutation of the immutable base receipt.
    """
    candidates = _supersession_candidates(receipt_path)
    if not candidates:
        return False, []

    target = receipt_path.as_posix()
    errors: list[str] = []
    for candidate in candidates:
        try:
            raw = yaml.safe_load(candidate.read_text())
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"{candidate}: unreadable supersession YAML: {exc}")
            continue
        if not isinstance(raw, dict) or raw.get("supersedes") != target:
            continue
        replacement = raw.get("replacement")
        if not isinstance(replacement, dict):
            errors.append(f"{candidate}: supersession has no mapping replacement")
            continue
        try:
            receipt = ModelDodReceipt.model_validate(replacement)
        except ValidationError as exc:
            errors.append(
                f"{candidate}: replacement fails ModelDodReceipt validation: {exc}"
            )
            continue

        violations = _receipt_binding_violations(receipt, contracts_dir)
        if violations:
            errors.extend(f"{candidate}: replacement {v}" for v in violations)
            continue
        return True, []

    return False, errors


def _validate_hardened_receipt(
    receipt_path: Path, receipt: ModelDodReceipt, contracts_dir: Path
) -> list[str]:
    return [
        f"{receipt_path}: {fragment}"
        for fragment in _receipt_binding_violations(receipt, contracts_dir)
    ]


def _coerce_timestamp(raw: object) -> datetime | None:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


_TIMESTAMP_KEYS = ("run_timestamp", "verified_at")


def _extract_receipt_timestamp(raw: dict[str, object]) -> datetime | None:
    """Extract the receipt's production timestamp from raw YAML data.

    Prefers top-level ``run_timestamp`` (the ModelDodReceipt field), then
    falls back to ``verified_at`` at any nesting depth — pre-schema legacy
    receipts (e.g. ``OMN-10362/dod-00*``) carry only that key, sometimes
    nested. Returns None when no parseable timestamp exists anywhere; the
    caller exempts such files because a timestamp-less artifact cannot
    parse as ModelDodReceipt and is already rejected as NONPASS by the
    receipt gate — this hook is the sha/verifier ratchet, not the schema
    enforcer.
    """
    top = _coerce_timestamp(raw.get("run_timestamp"))
    if top is not None:
        return top
    return _walk_for_timestamp(raw)


def _walk_for_timestamp(node: object) -> datetime | None:
    """Depth-first search for the first parseable timestamp key."""
    if isinstance(node, dict):
        for key in _TIMESTAMP_KEYS:
            found = _coerce_timestamp(node.get(key))
            if found is not None:
                return found
        children: list[object] = list(node.values())
    elif isinstance(node, list):
        children = list(node)
    else:
        return None
    for child in children:
        found = _walk_for_timestamp(child)
        if found is not None:
            return found
    return None


def _validate_receipt_model(
    receipt_path: Path, raw: dict[str, object]
) -> tuple[ModelDodReceipt | None, str | None]:
    """Validate a receipt across old/new omnibase_core receipt schemas."""
    try:
        return ModelDodReceipt.model_validate(raw), None
    except ValidationError as exc:
        if "contract_entry_sha256" not in raw:
            return (
                None,
                f"{receipt_path}: receipt fails ModelDodReceipt validation: {exc}",
            )
        legacy_raw = dict(raw)
        contract_entry_sha256 = legacy_raw.pop("contract_entry_sha256")
        try:
            receipt = ModelDodReceipt.model_validate(legacy_raw)
        except ValidationError:
            return (
                None,
                f"{receipt_path}: receipt fails ModelDodReceipt validation: {exc}",
            )
        object.__setattr__(receipt, "contract_entry_sha256", contract_entry_sha256)
        return receipt, None


def check_receipt_file(receipt_path: Path, contracts_dir: Path) -> list[str]:
    """Return violation strings for one receipt file (empty = clean)."""
    if ".supersede." in receipt_path.name:
        return []

    try:
        raw = yaml.safe_load(receipt_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        return [f"{receipt_path}: unreadable receipt YAML: {exc}"]
    if not isinstance(raw, dict):
        return [f"{receipt_path}: receipt YAML is not a mapping"]

    if ".supersede." in receipt_path.name:
        return []

    # Legacy exemption decided on the raw timestamp BEFORE model validation,
    # so pre-cutoff receipts with historical schema quirks never block.
    # No timestamp at all → exempt: such a file cannot parse as
    # ModelDodReceipt and the receipt gate already rejects it as NONPASS.
    run_ts = _extract_receipt_timestamp(raw)
    if run_ts is None or run_ts < HARDENING_CUTOFF:
        return []

    superseded, supersession_errors = _valid_supersession_replacement(
        receipt_path, contracts_dir
    )
    if superseded:
        return []
    if supersession_errors:
        return supersession_errors

    receipt, error = _validate_receipt_model(receipt_path, raw)
    if error is not None:
        return [error]
    if receipt is None:
        return [f"{receipt_path}: receipt validation returned no model"]

    return _validate_hardened_receipt(receipt_path, receipt, contracts_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Receipt hardening gate: staged DoD receipts produced on/after "
            f"{HARDENING_CUTOFF.date()} must carry a matching contract_sha256 "
            "and a non-session-local verifier."
        )
    )
    parser.add_argument(
        "files", nargs="*", help="Receipt YAML paths (from pre-commit)."
    )
    parser.add_argument(
        "--contracts-dir",
        default="contracts",
        help="Directory containing OMN-XXXX.yaml ticket contracts.",
    )
    args = parser.parse_args(argv)
    contracts_dir = Path(args.contracts_dir)

    all_violations: list[str] = []
    for file_arg in args.files:
        path = Path(file_arg)
        if not path.is_file():
            continue  # deleted/renamed paths are not this gate's concern
        all_violations.extend(check_receipt_file(path, contracts_dir))

    if all_violations:
        print(f"Receipt hardening gate: {len(all_violations)} violation(s):\n")
        for violation in all_violations:
            print(f"  {violation}")
        print(
            "\nFix the receipt (tool-generate; bind the current contract hash); "
            "never bypass the gate."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
