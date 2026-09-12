# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18236 — CLI for the acceptance-criterion binding gate.

Two modes, and the difference between them is what evidence is available, never
how strict the gate feels like being.

``--local`` runs only the rules that need no ticket body: malformed labels,
binding records for unclaimed criteria, duplicate records. This is the
pre-commit mode. It exists because a commit has no Linear read and no PR, and a
hook that pretended otherwise would either block every commit or quietly pass
everything.

Hosted mode needs ``--ticket-bodies``, the ``{ticket_id: body}`` JSON the
workflow layer fetches from Linear — the same file and the same fetch step the
canonical-shape gate already consumes, read rather than re-fetched because this
package's imperative-contract guard blocks raw HTTP from ``src/``. In this mode
a missing file, an unreadable one, or an absent ticket key is RED. There is no
offline pass.

Installed as the ``check-ac-binding-acceptance`` console entry point, beside
every other gate CLI this package exposes, rather than as a loose file under
``scripts/``: the repository's scripts guard default-denies new scripts there
and directs new work to a registered surface, which this is.

Exit codes: 0 clean, 1 findings, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from onex_change_control.validation.ac_binding_acceptance import (
    AcBindingFinding,
    check_contract_ac_bindings,
    check_local_ac_bindings,
)

_TICKET_ID_SUFFIX = ".yaml"


def _ticket_id(path: Path) -> str:
    return path.name[: -len(_TICKET_ID_SUFFIX)]


def _load_contract(path: Path) -> tuple[object | None, str]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")), ""
    except (OSError, yaml.YAMLError) as exc:
        return None, str(exc)


def _load_ticket_bodies(path: Path | None) -> dict[str, str]:
    """Read the ``{ticket_id: body}`` map the workflow layer wrote.

    An unreadable or malformed file yields an EMPTY map rather than an
    exception, and an empty map means every ticket lookup misses, which the
    gate reports as ``ac_binding_ticket_unreadable`` — RED. Failing that way
    round keeps "the fetch step broke" and "Linear had nothing for this ticket"
    on the same, refusing side of the line.
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
    paths: list[Path], *, local_only: bool, ticket_bodies_path: Path | None
) -> tuple[int, list[AcBindingFinding]]:
    bodies = {} if local_only else _load_ticket_bodies(ticket_bodies_path)
    findings: list[AcBindingFinding] = []
    for path in paths:
        if not path.name.startswith("OMN-"):
            continue
        if not path.name.endswith(_TICKET_ID_SUFFIX):
            continue
        ticket_id = _ticket_id(path)
        contract, error = _load_contract(path)
        if error:
            findings.append(
                AcBindingFinding(
                    rule="ac_binding_contract_unreadable",
                    subject=ticket_id,
                    message=f"could not read {path}: {error}",
                )
            )
            continue
        if local_only:
            findings.extend(check_local_ac_bindings(ticket_id, contract))
            continue
        findings.extend(
            check_contract_ac_bindings(ticket_id, contract, bodies.get(ticket_id))
        )
    return (1 if findings else 0), findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check-ac-binding-acceptance",
        description=(
            "Acceptance-criterion bindings must name a criterion the ticket "
            "has, and stay pinned to the revision they were accepted against."
        ),
    )
    parser.add_argument("files", nargs="*", type=Path, default=[])
    parser.add_argument(
        "--local",
        action="store_true",
        help=(
            "run only the rules that need no ticket body (pre-commit mode). "
            "Omitting it requires --ticket-bodies."
        ),
    )
    parser.add_argument(
        "--ticket-bodies",
        type=Path,
        default=None,
        help=(
            "JSON {ticket_id: body} written by the workflow's Linear fetch "
            "step. Absent or unreadable is RED in hosted mode."
        ),
    )
    args = parser.parse_args(argv)

    if not args.local and args.ticket_bodies is None:
        print(
            "error: hosted mode needs --ticket-bodies; pass --local to run "
            "only the rules that do not require a ticket body",
            file=sys.stderr,
        )
        return 2

    paths = [Path(p) for p in args.files]
    if not paths:
        return 0

    exit_code, findings = run(
        paths, local_only=args.local, ticket_bodies_path=args.ticket_bodies
    )
    if findings:
        mode = "local" if args.local else "hosted"
        print(
            f"Acceptance-criterion binding gate ({mode}): {len(findings)} finding(s)",
            file=sys.stderr,
        )
        for finding in findings:
            print(finding.render(), file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
