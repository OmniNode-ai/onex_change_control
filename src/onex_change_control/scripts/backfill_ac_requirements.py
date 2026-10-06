# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-19046 — CLI for the one-time acceptance-criteria backfill.

Dry run is the DEFAULT. ``--write`` is the only thing that touches a file,
for the same reason ``onex-serialize-ac-section`` has no apply path at all: a
sweep over 370 contracts that writes by default is a sweep somebody runs once
by accident.

This package's imperative-contract guard blocks raw HTTP from ``src/``, so
this command does not fetch anything. It consumes the same
``{ticket_id: body}`` JSON file that ``check-ac-binding-acceptance`` and
``onex-serialize-ac-section`` consume, fetched by whichever surface already
holds the tracker credential. A ticket absent from that map is REFUSED, never
skipped.

Exit codes match the sibling gate CLIs: ``0`` every contract planned cleanly
(whether or not anything changed), ``1`` one or more refusals, ``2`` usage
error. A refusal is never downgraded to a warning — the refusals are the
point, and the tempting alternative is to write a plausible model and call the
corpus repaired.

Installed as a console entry point rather than left loose under ``scripts/``,
which this repository's scripts guard denies.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from onex_change_control.serialization.ac_requirements import (
    AcRequirementsRefusalError,
    plan_requirements_backfill,
)

_CONTRACT_SUFFIX = ".yaml"


def _load_ticket_bodies(path: Path | None) -> dict[str, str]:
    """The ``{ticket_id: body}`` map, or an empty one.

    Empty means every lookup misses, and a miss is a refusal rather than
    "nothing to do" — the same direction the OMN-18236 gate takes.
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


def run(
    paths: list[Path], *, ticket_bodies_path: Path | None, write: bool
) -> tuple[int, list[str]]:
    """Plan (and optionally apply) the backfill. Returns ``(exit, report)``."""
    bodies = _load_ticket_bodies(ticket_bodies_path)
    report: list[str] = []
    refused = 0
    changed = 0
    unchanged = 0
    for path in sorted(paths):
        if not path.name.startswith("OMN-") or not path.name.endswith(_CONTRACT_SUFFIX):
            continue
        ticket_id = path.name[: -len(_CONTRACT_SUFFIX)]
        try:
            text = path.read_text(encoding="utf-8")
            contract = yaml.safe_load(text)
        except (OSError, yaml.YAMLError) as exc:
            refused += 1
            report.append(
                f"REFUSED {ticket_id} ac_requirements_contract_unreadable: {exc}"
            )
            continue
        if not isinstance(contract, dict):
            refused += 1
            report.append(
                f"REFUSED {ticket_id} ac_requirements_contract_unreadable: "
                "not a mapping"
            )
            continue
        try:
            plan = plan_requirements_backfill(
                ticket_id, contract, text, bodies.get(ticket_id)
            )
        except AcRequirementsRefusalError as exc:
            refused += 1
            report.append(f"REFUSED {ticket_id} {exc}")
            continue
        if not plan.changed:
            unchanged += 1
            report.append(
                f"UNCHANGED {ticket_id} "
                f"({len(plan.labels)} bound: {', '.join(plan.labels) or 'none'})"
            )
            continue
        changed += 1
        report.append(
            f"CHANGED {ticket_id} "
            f"({len(plan.labels)} criteria: {', '.join(plan.labels)})"
        )
        if write:
            path.write_text(plan.new_text, encoding="utf-8")
            report.append(f"  written to {path}")
    report.append(
        f"-- {changed} changed, {unchanged} unchanged, {refused} refused "
        f"({'APPLIED' if write else 'DRY RUN'})"
    )
    return (1 if refused else 0), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="onex-backfill-ac-requirements",
        description=(
            "Lift a ticket's acceptance criteria into its contract's "
            "requirements[].acceptance[]. Dry run unless --write."
        ),
    )
    parser.add_argument("files", nargs="*", type=Path, default=[])
    parser.add_argument(
        "--ticket-bodies",
        type=Path,
        default=None,
        help=(
            "JSON {ticket_id: body} fetched by the caller. A ticket absent "
            "from it is REFUSED, never skipped."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply the plan. Without it nothing on disk is touched.",
    )
    args = parser.parse_args(argv)
    if not args.files:
        parser.error("at least one contract path is required")

    exit_code, report = run(
        list(args.files),
        ticket_bodies_path=args.ticket_bodies,
        write=bool(args.write),
    )
    for line in report:
        print(line)
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
