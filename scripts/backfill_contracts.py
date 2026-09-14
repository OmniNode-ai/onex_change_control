# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Backfill skeleton contracts for Linear tickets missing YAML files.

Usage
-----
    python scripts/backfill_contracts.py [--range OMN-START:OMN-END] [--dry-run]
                                         [--contracts-dir PATH]

The script iterates every ticket ID in the given range, checks whether a
corresponding ``contracts/OMN-XXXX.yaml`` already exists, and — when it does
not — fetches the ticket from the Linear API and generates a skeleton YAML via
``generate_skeleton_contract()``.

Rules
-----
- Idempotent: existing files are NEVER overwritten (skipped with a log line).
- Linear 404 → write a minimal tombstone YAML (summary: BACKFILL_NOT_FOUND).
- ``LINEAR_API_KEY`` absent → immediate nonzero exit with a clear message.
- ``--dry-run`` → print intended actions, write nothing.

Exit codes
----------
0   All tickets processed (or dry-run completed).
1   ``LINEAR_API_KEY`` missing or one or more tickets raised an unexpected error.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap: allow the omniclaude contract generator to be imported even
# when the package is not on the venv path (cross-repo usage).
# ---------------------------------------------------------------------------
_OMNI_HOME = os.environ.get("OMNI_HOME")
if _OMNI_HOME:
    _GENERATOR_DIR = (
        Path(_OMNI_HOME)
        / "omniclaude"
        / "plugins"
        / "onex"
        / "skills"
        / "_lib"
        / "contract_generator"
    )
    if _GENERATOR_DIR.is_dir() and str(_GENERATOR_DIR) not in sys.path:
        sys.path.insert(0, str(_GENERATOR_DIR))


# ---------------------------------------------------------------------------
# Custom exceptions (satisfies EM / TRY003 rules)
# ---------------------------------------------------------------------------


class _GeneratorNotFoundError(ImportError):
    """Raised when generate_skeleton_contract cannot be located."""


class _LinearAPIError(RuntimeError):
    """Raised on unexpected Linear API HTTP or transport errors."""


class _RangeParseError(ValueError):
    """Raised when the --range argument cannot be parsed."""


# ---------------------------------------------------------------------------
# Generator loader
# ---------------------------------------------------------------------------


def _load_generate_skeleton_contract() -> Any:
    """Import generate_skeleton_contract, searching common locations."""
    # Try direct import first (covers venv installs and path-augmented cases).
    try:
        from generate_contract import (  # type: ignore[import-not-found]
            generate_skeleton_contract,
        )
    except ImportError:
        pass
    else:
        return generate_skeleton_contract

    # Fallback: resolve relative to OMNI_HOME if set.
    if _OMNI_HOME:
        generator_path = (
            Path(_OMNI_HOME)
            / "omniclaude"
            / "plugins"
            / "onex"
            / "skills"
            / "_lib"
            / "contract_generator"
            / "generate_contract.py"
        )
        if generator_path.is_file():
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "generate_contract", generator_path
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod.generate_skeleton_contract

    msg = (
        "generate_skeleton_contract not found. "
        "Set OMNI_HOME or ensure omniclaude is on sys.path."
    )
    raise _GeneratorNotFoundError(msg)


# ---------------------------------------------------------------------------
# Tombstone YAML for tickets not found in Linear
# ---------------------------------------------------------------------------
_TOMBSTONE_YAML = """\
schema_version: '1.0.0'
ticket_id: '{ticket_id}'
summary: BACKFILL_NOT_FOUND
is_seam_ticket: false
interface_change: false
interfaces_touched: []
evidence_requirements: []
emergency_bypass:
  enabled: false
  justification: ''
  follow_up_ticket_id: ''
"""


# ---------------------------------------------------------------------------
# Linear client
# ---------------------------------------------------------------------------


class _LinearClient:
    """Thin HTTP wrapper around the Linear GraphQL API."""

    _GRAPHQL_URL = "https://api.linear.app/graphql"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def get_issue(self, ticket_id: str) -> dict[str, Any] | None:
        """Fetch a Linear issue by identifier.

        Returns the issue dict on success, or None when the issue does not
        exist (404-equivalent: the API returns an empty ``nodes`` list).

        Raises
        ------
        _LinearAPIError
            On unexpected HTTP or API errors.
        """
        import json
        import urllib.error
        import urllib.request

        identifier = ticket_id  # e.g. "OMN-150"
        query = """
        query($identifier: String!) {
            issueSearch(query: $identifier, first: 1) {
                nodes {
                    id
                    identifier
                    title
                    description
                    state { name }
                    team { key }
                }
            }
        }
        """
        payload = {"query": query, "variables": {"identifier": identifier}}
        body = json.dumps(payload).encode()

        req = urllib.request.Request(  # noqa: S310
            self._GRAPHQL_URL,
            data=body,
            headers={
                "Authorization": self._api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            msg = f"Linear API HTTP error {exc.code} for {ticket_id}"
            raise _LinearAPIError(msg) from exc
        except OSError as exc:
            msg = f"Linear API request failed for {ticket_id}: {exc}"
            raise _LinearAPIError(msg) from exc

        nodes: list[dict[str, Any]] = (
            data.get("data", {}).get("issueSearch", {}).get("nodes", [])
        )
        if not nodes:
            return None  # ticket not found

        # issueSearch is a fuzzy text search — verify the returned identifier
        # is an exact match to prevent OMN-10 matching OMN-100 etc.
        issue = next(
            (n for n in nodes if n.get("identifier") == ticket_id),
            None,
        )
        if issue is None:
            return None  # fuzzy match returned unrelated tickets; treat as not found

        return {
            "id": issue.get("identifier", ticket_id),
            "identifier": issue.get("identifier", ticket_id),
            "title": issue.get("title", ""),
            "description": issue.get("description") or "",
            "state": issue.get("state", {}),
            "team": issue.get("team", {}),
        }


def _build_linear_client(api_key: str) -> _LinearClient:
    """Construct a Linear client.

    Exists as a top-level function so tests can patch it via
    ``patch.object(backfill, '_build_linear_client', ...)``.
    """
    return _LinearClient(api_key)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _parse_range(range_arg: str) -> range:
    """Parse ``OMN-123:OMN-200`` into a ``range(123, 201)``."""
    try:
        start_str, end_str = range_arg.split(":")
        start = int(start_str.split("-")[1])
        end = int(end_str.split("-")[1])
    except (ValueError, IndexError) as exc:
        msg = (
            f"Invalid range format {range_arg!r}. "
            "Expected 'OMN-START:OMN-END' (e.g. 'OMN-123:OMN-9892')."
        )
        raise _RangeParseError(msg) from exc
    return range(start, end + 1)


def generate_for_ticket(
    *,
    ticket_id: str,
    contracts_dir: Path,
    linear_client: Any,
    dry_run: bool = False,
) -> str:
    """Process a single ticket ID.

    Returns one of:
    - ``"skipped"``    — file already exists, no action taken.
    - ``"generated"``  — new contract written (or would be in dry-run).
    - ``"tombstoned"`` — ticket not found in Linear; tombstone written.

    Raises
    ------
    _LinearAPIError
        On unexpected Linear API errors.
    """
    output_path = contracts_dir / f"{ticket_id}.yaml"

    # Idempotency guard — never overwrite existing files.
    if output_path.exists():
        return "skipped"

    # Fetch from Linear.
    issue = linear_client.get_issue(ticket_id)

    if issue is None:
        # Ticket does not exist in Linear → tombstone.
        content = _TOMBSTONE_YAML.format(ticket_id=ticket_id)
        if dry_run:
            print(f"[DRY-RUN] Would tombstone {output_path}")
        else:
            output_path.write_text(content, encoding="utf-8")
            print(f"Tombstoned {ticket_id} → {output_path.name}")
        return "tombstoned"

    # Ticket found → generate skeleton contract.
    generate_skeleton_contract = _load_generate_skeleton_contract()
    summary = issue.get("title") or ticket_id
    yaml_content = generate_skeleton_contract(
        ticket_id=ticket_id,
        summary=summary,
        is_seam_ticket=False,
    )

    if dry_run:
        print(f"[DRY-RUN] Would write {output_path}:")
        for line in yaml_content.splitlines():
            print(f"  {line}")
    else:
        output_path.write_text(yaml_content, encoding="utf-8")
        print(f"Generated {ticket_id} → {output_path.name}")

    return "generated"


# ---------------------------------------------------------------------------
# CLI helpers (split out to keep main() complexity below C901 threshold)
# ---------------------------------------------------------------------------


def _resolve_contracts_dir(contracts_dir_arg: Path | None) -> Path:
    """Return the contracts directory, defaulting to <repo-root>/contracts/."""
    if contracts_dir_arg is not None:
        return contracts_dir_arg
    # scripts/ → repo root → contracts/
    return Path(__file__).resolve().parent.parent / "contracts"


def _print_summary(
    *,
    dry_run: bool,
    generated: int,
    skipped: int,
    tombstoned: int,
    errors: int,
) -> None:
    mode = "[DRY-RUN] " if dry_run else ""
    print(
        f"\n{mode}Report: {generated} generated, {skipped} skipped (exists), "
        f"{tombstoned} tombstoned (not found), {errors} errors"
    )


def _run_sweep(
    *,
    id_range: range,
    contracts_dir: Path,
    linear_client: Any,
    dry_run: bool,
) -> tuple[int, int, int, int]:
    """Execute the sweep and return (generated, skipped, tombstoned, errors)."""
    generated = skipped = tombstoned = errors = 0
    for num in id_range:
        ticket_id = f"OMN-{num}"
        try:
            result = generate_for_ticket(
                ticket_id=ticket_id,
                contracts_dir=contracts_dir,
                linear_client=linear_client,
                dry_run=dry_run,
            )
        except _LinearAPIError as exc:
            print(f"ERROR processing {ticket_id}: {exc}", file=sys.stderr)
            errors += 1
            continue

        if result == "generated":
            generated += 1
        elif result == "skipped":
            skipped += 1
        elif result == "tombstoned":
            tombstoned += 1

    return generated, skipped, tombstoned, errors


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """CLI entry point.

    Parameters
    ----------
    argv:
        Argument vector (including ``argv[0]`` program name).  When *None*,
        ``sys.argv`` is used.  Tests pass a list to avoid touching the process
        argument vector.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="backfill_contracts.py",
        description="Backfill skeleton contracts for missing Linear tickets.",
    )
    parser.add_argument(
        "--range",
        dest="range_arg",
        default="OMN-123:OMN-9892",
        metavar="OMN-START:OMN-END",
        help="Ticket ID range to sweep (inclusive). Default: OMN-123:OMN-9892",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print intended actions without writing files.",
    )
    parser.add_argument(
        "--contracts-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to the contracts directory. "
            "Defaults to <repo-root>/contracts/ relative to this script."
        ),
    )

    # Parse — skip argv[0] (program name) when argv is provided by tests.
    args = parser.parse_args(argv[1:] if argv is not None else None)

    # Fail-fast: LINEAR_API_KEY required.
    api_key = os.environ.get("LINEAR_API_KEY", "")
    if not api_key:
        print(
            "ERROR: LINEAR_API_KEY is required for backfill but is not set.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Resolve contracts directory.
    contracts_dir = _resolve_contracts_dir(args.contracts_dir)
    if not contracts_dir.is_dir():
        print(
            f"ERROR: contracts directory does not exist: {contracts_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Parse range.
    try:
        id_range = _parse_range(args.range_arg)
    except _RangeParseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # Build Linear client.
    linear_client = _build_linear_client(api_key)

    # Sweep.
    generated, skipped, tombstoned, errors = _run_sweep(
        id_range=id_range,
        contracts_dir=contracts_dir,
        linear_client=linear_client,
        dry_run=args.dry_run,
    )

    _print_summary(
        dry_run=args.dry_run,
        generated=generated,
        skipped=skipped,
        tombstoned=tombstoned,
        errors=errors,
    )

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
