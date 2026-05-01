# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Auto-scaffold OCC contract + receipt stubs for a Linear ticket.

Generates:
  - contracts/OMN-XXXX.yaml (ticket contract)
  - drift/dod_receipts/OMN-XXXX/dod-NNN/command.yaml (receipt stubs per DoD item)

Receipt stubs use status: PENDING — they must be filled with actual evidence
before receipt-gate will PASS.

Usage:
    uv run scripts/auto_scaffold_contract.py OMN-9582
    uv run scripts/auto_scaffold_contract.py OMN-9582 --title "Title" \
        --description "..."
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

from onex_change_control.models.model_ticket_contract import ModelTicketContract

_TICKET_ID_RE = re.compile(r"^OMN-\d{1,6}$", re.IGNORECASE)
_DOD_CHECKBOX_RE = re.compile(r"^\s*- \[ \]\s*(.+)$", re.MULTILINE)

_SEAM_SIGNALS: dict[str, str] = {
    "kafka": "topics",
    "topic": "topics",
    "consumer": "topics",
    "producer": "topics",
    "schema": "events",
    "payload": "events",
    "event model": "events",
    "modelhook": "events",
    "spi": "protocols",
    "protocol": "protocols",
    "envelope": "envelopes",
    "endpoint": "public_api",
    "route": "public_api",
    " api": "public_api",
    "rest": "public_api",
}


def _detect_seam_signals(text: str) -> list[str]:
    lower = text.lower()
    seen: dict[str, None] = {}
    for keyword, surface in _SEAM_SIGNALS.items():
        if keyword in lower:
            seen[surface] = None
    return list(seen.keys())


def _extract_dod_items(description: str) -> list[str]:
    return _DOD_CHECKBOX_RE.findall(description)


def _build_dod_evidence(
    dod_items: list[str],
) -> list[dict[str, object]]:
    if not dod_items:
        return [
            {
                "id": "dod-001",
                "description": "Tests exist and pass",
                "source": "generated",
                "checks": [
                    {
                        "check_type": "test_passes",
                        "check_value": "uv run pytest tests/ -v",
                    }
                ],
            },
            {
                "id": "dod-002",
                "description": "Pre-commit hooks pass",
                "source": "generated",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": "pre-commit run --all-files",
                    }
                ],
            },
        ]

    items: list[dict[str, object]] = []
    for i, text in enumerate(dod_items, 1):
        items.append(
            {
                "id": f"dod-{i:03d}",
                "description": text.strip(),
                "source": "linear",
                "linear_dod_text": text.strip(),
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": (f"# TODO: verify: {text.strip()}"),
                    }
                ],
            }
        )
    return items


def _build_contract_yaml(
    ticket_id: str,
    title: str,
    description: str,
    dod_items: list[str],
) -> str:
    seam_surfaces = _detect_seam_signals(title + " " + description)
    is_seam = len(seam_surfaces) > 0
    interface_change = is_seam

    completeness = "ENRICHED" if dod_items else "STUB"

    contract_data: dict[str, object] = {
        "schema_version": "1.0.0",
        "ticket_id": ticket_id,
        "title": title,
        "summary": title,
        "is_seam_ticket": is_seam,
        "interface_change": interface_change,
        "interfaces_touched": seam_surfaces if is_seam else [],
        "contract_completeness": completeness,
        "evidence_requirements": [
            {
                "kind": "tests",
                "description": "All tests pass",
                "command": "uv run pytest tests/ -v",
            },
            {
                "kind": "ci",
                "description": "CI passes",
                "command": "pre-commit run --all-files",
            },
        ],
        "emergency_bypass": {
            "enabled": False,
            "justification": "",
            "follow_up_ticket_id": "",
        },
        "dod_evidence": _build_dod_evidence(dod_items),
    }

    ModelTicketContract.model_validate(contract_data)

    return yaml.dump(
        contract_data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def _build_receipt_stub(
    ticket_id: str,
    dod_id: str,
    check_type: str,
    check_value: str,
) -> str:
    receipt_data: dict[str, object] = {
        "schema_version": "1.0.0",
        "ticket_id": ticket_id,
        "evidence_item_id": dod_id,
        "check_type": check_type,
        "check_value": check_value,
        "status": "PENDING",
    }
    return yaml.dump(
        receipt_data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def generate_stubs(
    ticket_id: str,
    title: str,
    description: str,
    repo_root: Path,
) -> list[Path]:
    ticket_id = ticket_id.upper()
    if not _TICKET_ID_RE.match(ticket_id):
        print(f"Error: invalid ticket ID format: {ticket_id!r}", file=sys.stderr)
        sys.exit(1)

    dod_items = _extract_dod_items(description)
    contract_yaml = _build_contract_yaml(ticket_id, title, description, dod_items)

    contract_dir = repo_root / "contracts"
    contract_path = contract_dir / f"{ticket_id}.yaml"

    created_paths: list[Path] = []
    if contract_path.exists():
        print(f"[skip] contract already exists: {contract_path}")
        contract_yaml = contract_path.read_text(encoding="utf-8")
    else:
        contract_dir.mkdir(parents=True, exist_ok=True)
        contract_path.write_text(contract_yaml, encoding="utf-8")
        print(f"[created] {contract_path}")
        created_paths.append(contract_path)

    receipt_dir = repo_root / "drift" / "dod_receipts" / ticket_id
    contract_data = yaml.safe_load(contract_yaml)
    dod_evidence = contract_data.get("dod_evidence", [])

    for item in dod_evidence:
        dod_id = item["id"]
        for check in item.get("checks", []):
            check_type = check["check_type"]
            receipt_subdir = receipt_dir / str(dod_id)
            receipt_path = receipt_subdir / f"{check_type}.yaml"

            if receipt_path.exists():
                print(f"[skip] receipt already exists: {receipt_path}")
                continue

            receipt_subdir.mkdir(parents=True, exist_ok=True)
            receipt_yaml = _build_receipt_stub(
                ticket_id=ticket_id,
                dod_id=str(dod_id),
                check_type=check_type,
                check_value=str(check["check_value"]),
            )
            receipt_path.write_text(receipt_yaml)
            print(f"[created] {receipt_path}")
            created_paths.append(receipt_path)

    return created_paths


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Auto-scaffold OCC contract + receipt stubs for a Linear ticket",
    )
    parser.add_argument(
        "ticket_id",
        help="Linear ticket ID (e.g., OMN-9582)",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Ticket title (default: uses ticket_id as placeholder)",
    )
    parser.add_argument(
        "--description",
        default="",
        help="Ticket description (used for DoD extraction and seam detection)",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Path to onex_change_control repo root (default: auto-detect)",
    )
    args = parser.parse_args(argv)

    title = args.title or args.ticket_id

    if args.repo_root:
        repo_root = Path(args.repo_root)
    else:
        repo_root = Path(__file__).resolve().parent.parent

    if not (repo_root / "contracts").exists():
        print(
            f"Error: {repo_root} does not appear to be onex_change_control root "
            f"(no contracts/ dir)",
            file=sys.stderr,
        )
        sys.exit(1)

    paths = generate_stubs(
        ticket_id=args.ticket_id,
        title=title,
        description=args.description,
        repo_root=repo_root,
    )
    print(f"\nGenerated {len(paths)} file(s)")


if __name__ == "__main__":
    main()
