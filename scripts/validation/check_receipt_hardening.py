# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Receipt hardening gate (OMN-13060, retro item A-5).

Enforces three invariants on DoD receipts produced on/after the gate
introduction date (``run_timestamp >= 2026-06-12T00:00:00Z``):

1. ``contract_sha256`` must be present. Hand-authored sha-less receipts
   (the 2530/2533/2534 failure mode) are rejected at commit time instead
   of wedging the OCC PR at the merge gate.
2. ``contract_sha256`` must match ``sha256(contracts/<ticket_id>.yaml)``
   at the staged state. A mismatch means the contract mutated after the
   receipt was produced — rerun probes, regenerate the receipt.
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
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from omnibase_core.enums.ticket.enum_receipt_status import EnumReceiptStatus
from omnibase_core.models.contracts.ticket.model_dod_receipt import ModelDodReceipt
from omnibase_core.validation.validator_receipt_gate import compute_contract_sha256
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

    try:
        receipt = ModelDodReceipt.model_validate(raw)
    except ValidationError as exc:
        return [f"{receipt_path}: receipt fails ModelDodReceipt validation: {exc}"]

    violations: list[str] = []

    if receipt.contract_sha256 is None:
        violations.append(
            f"{receipt_path}: missing contract_sha256 (OMN-13060/A-5). "
            "Tool-generate the receipt; never hand-author. The field must be "
            f"sha256(contracts/{receipt.ticket_id}.yaml)."
        )
    else:
        contract_path = contracts_dir / f"{receipt.ticket_id}.yaml"
        if not contract_path.is_file():
            violations.append(
                f"{receipt_path}: contract {contract_path} does not exist for "
                f"ticket {receipt.ticket_id}"
            )
        else:
            expected = f"sha256:{compute_contract_sha256(contract_path)}"
            if receipt.contract_sha256 != expected:
                violations.append(
                    f"{receipt_path}: contract_sha256 mismatch — receipt has "
                    f"{receipt.contract_sha256!r} but sha256({contract_path}) is "
                    f"{expected!r}. The contract mutated after this receipt was "
                    "produced; rerun probes and regenerate the receipt."
                )

    if receipt.status is EnumReceiptStatus.PASS and _is_denylisted_verifier(
        receipt.verifier
    ):
        violations.append(
            f"{receipt_path}: PASS receipt uses session-local verifier alias "
            f"{receipt.verifier!r} (OMN-13060/A-5). Name an identifiable "
            "independent verifier."
        )

    return violations


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
