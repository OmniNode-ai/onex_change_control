# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18270 — CLI for serializing a contract's DoD model into a ticket body.

Dry run is the DEFAULT and there is no flag that makes this process write to
Linear, because it cannot: this package's imperative-contract guard blocks raw
HTTP from ``src/``, the same reason the OMN-18236 gate consumes a
``{ticket_id: body}`` JSON file rather than fetching one. So the split is:

* the workflow layer (or a lane holding the credential) fetches bodies into
  ``--ticket-bodies``, exactly the file ``check-ac-binding-acceptance`` reads;
* this command computes the new body and, with ``--emit-body DIR``, writes
  ``DIR/<ticket>.md`` for the caller to apply through the Linear surface it
  already uses;
* the diff printed here is generated from the SAME
  :class:`~onex_change_control.models.model_ac_section_plan.ModelAcSectionPlan`
  that produced those bytes, so an apply cannot diverge from the diff somebody
  approved.

Exit codes, chosen to match the sibling gate CLIs: ``0`` every contract
serialized cleanly (whether or not anything changed), ``1`` one or more
refusals, ``2`` usage error. A refusal is never downgraded to a warning — the
refusals exist precisely because the tempting alternative is to write a short
section and call the ticket repaired.

Installed as the ``onex-serialize-ac-section`` console entry point rather than
left loose under ``scripts/``, which this repository's scripts guard denies.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from onex_change_control.serialization.ac_section import (
    AcSectionRefusalError,
    plan_acceptance_criteria_update,
)

if TYPE_CHECKING:
    from onex_change_control.models.model_ac_section_plan import ModelAcSectionPlan

_CONTRACT_SUFFIX = ".yaml"


def _ticket_id(path: Path) -> str:
    return path.name[: -len(_CONTRACT_SUFFIX)]


def _load_ticket_bodies(path: Path | None) -> dict[str, str]:
    """The ``{ticket_id: body}`` map, or an empty one.

    Empty means every lookup misses, and a miss is reported as a refusal rather
    than as "nothing to do" — same direction as the OMN-18236 gate. "I could
    not read the ticket" must not resolve to "so it needs no change".
    """
    if path is None or not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {
        str(key): str(value) for key, value in loaded.items() if isinstance(value, str)
    }


def _diff(plan: ModelAcSectionPlan) -> str:
    return "".join(
        difflib.unified_diff(
            plan.old_body.splitlines(keepends=True),
            plan.new_body.splitlines(keepends=True),
            fromfile=f"{plan.ticket_id} (Linear, as read)",
            tofile=f"{plan.ticket_id} (serialized from contract)",
            n=3,
        )
    )


def run(
    paths: list[Path], *, ticket_bodies_path: Path | None, emit_body_dir: Path | None
) -> tuple[int, list[str]]:
    """Plan every contract in ``paths``. Returns ``(exit_code, report_lines)``."""
    bodies = _load_ticket_bodies(ticket_bodies_path)
    report: list[str] = []
    refused = 0
    for path in sorted(paths):
        if not path.name.startswith("OMN-") or not path.name.endswith(_CONTRACT_SUFFIX):
            continue
        ticket_id = _ticket_id(path)
        try:
            contract = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            refused += 1
            report.append(f"REFUSED {ticket_id} ac_section_contract_unreadable: {exc}")
            continue
        if not isinstance(contract, dict):
            refused += 1
            report.append(
                f"REFUSED {ticket_id} ac_section_contract_unreadable: not a mapping"
            )
            continue
        body = bodies.get(ticket_id)
        if body is None:
            refused += 1
            report.append(
                f"REFUSED {ticket_id} ac_section_ticket_unreadable: no body for "
                f"{ticket_id} in --ticket-bodies"
            )
            continue
        try:
            plan = plan_acceptance_criteria_update(ticket_id, contract, body)
        except AcSectionRefusalError as exc:
            refused += 1
            report.append(f"REFUSED {ticket_id} {exc}")
            continue
        if not plan.changed:
            report.append(
                f"UNCHANGED {ticket_id} "
                f"({len(plan.labels)} criteria: {', '.join(plan.labels) or 'none'})"
            )
            continue
        report.append(
            f"CHANGED {ticket_id} "
            f"({len(plan.labels)} criteria: {', '.join(plan.labels) or 'none'}; "
            f"binds: {', '.join(plan.bound_labels) or 'none'})"
        )
        report.append(_diff(plan))
        if emit_body_dir is not None:
            emit_body_dir.mkdir(parents=True, exist_ok=True)
            target = emit_body_dir / f"{ticket_id}.md"
            target.write_text(plan.new_body, encoding="utf-8")
            report.append(f"  body written to {target}")
    return (1 if refused else 0), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="onex-serialize-ac-section",
        description=(
            "Render a contract's requirements[].acceptance[] into the managed "
            "acceptance-criteria section of its Linear ticket body. Dry run "
            "only: prints the diff and, with --emit-body, the bytes to apply."
        ),
    )
    parser.add_argument("files", nargs="*", type=Path, default=[])
    parser.add_argument(
        "--ticket-bodies",
        type=Path,
        default=None,
        help=(
            "JSON {ticket_id: body} fetched from Linear by the workflow layer. "
            "A ticket absent from it is REFUSED, never skipped."
        ),
    )
    parser.add_argument(
        "--emit-body",
        type=Path,
        default=None,
        help="Directory to write <ticket>.md for each changed ticket.",
    )
    args = parser.parse_args(argv)
    if not args.files:
        parser.error("at least one contract path is required")

    exit_code, report = run(
        list(args.files),
        ticket_bodies_path=args.ticket_bodies,
        emit_body_dir=args.emit_body,
    )
    for line in report:
        print(line)
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
