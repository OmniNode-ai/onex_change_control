# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""check_dod_evidence_present.py -- Pre-commit guard requiring a non-empty
``dod_evidence`` block on every newly-authored ``contracts/OMN-*.yaml`` file.

Background
----------
``ModelTicketContract.dod_evidence`` defaults to ``[]``, so a contract that
omits the block still passes pydantic validation and ``validate-yaml``. A
``plan_to_tickets`` run on 2026-04-27 truncated 20 contracts (OMN-9829..9850)
without the block, and the missing evidence only surfaced later as
``dod_verify`` SKIPPED status. This pre-commit hook closes that gap by
rejecting commits that introduce an OMN ticket contract without populated
``dod_evidence``.

Usage
-----
    python3 scripts/check_dod_evidence_present.py contracts/OMN-1234.yaml [...]

Behavior
--------
* For each path matching ``contracts/OMN-<digits>.yaml``: load the YAML,
  require ``dod_evidence`` to exist and be a non-empty list.
* Non-OMN paths (e.g. drift artifacts, templates) are skipped silently so
  ``pre-commit``'s ``files:`` filter remains the source of truth for which
  files are eligible.
* Exits 0 on pass, 1 on any failure with a clear remediation message.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

# ``contracts/OMN-<digits>.yaml`` -- matches the pre-commit ``files:`` regex.
_OMN_CONTRACT_RE = re.compile(r"(?:^|/)contracts/OMN-\d+\.yaml$")


def _is_omn_contract(path: Path) -> bool:
    """True iff ``path`` looks like an OMN ticket contract.

    Uses the path string (not just the basename) so we don't accidentally
    match ``OMN-1234.yaml`` artifacts that live outside ``contracts/``.
    """
    return _OMN_CONTRACT_RE.search(path.as_posix()) is not None


def check_contract(path: Path) -> str | None:
    """Return an error message if the contract is missing/empty ``dod_evidence``,
    or ``None`` if the contract passes.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"{path}: unable to read file ({exc})"

    try:
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return f"{path}: YAML parse error ({exc})"

    if not isinstance(data, dict):
        return (
            f"{path}: top-level YAML document must be a mapping, "
            f"got {type(data).__name__}"
        )

    if "dod_evidence" not in data:
        return (
            f"{path}: missing or empty dod_evidence block. "
            "Generate with scripts/migrate_dod_contracts.py or author manually."
        )

    evidence = data["dod_evidence"]
    if not isinstance(evidence, list) or len(evidence) == 0:
        return (
            f"{path}: missing or empty dod_evidence block. "
            "Generate with scripts/migrate_dod_contracts.py or author manually."
        )

    return None


def main(argv: list[str]) -> int:
    if len(argv) <= 1:
        # No files passed -- pre-commit invokes us with zero files when no
        # matching paths are staged, and that's a clean pass.
        return 0

    failures: list[str] = []
    for arg in argv[1:]:
        path = Path(arg)
        if not _is_omn_contract(path):
            continue
        msg = check_contract(path)
        if msg is not None:
            failures.append(msg)

    if failures:
        print(
            "FAIL: ticket contracts missing required dod_evidence block:",
            file=sys.stderr,
        )
        for msg in failures:
            print(f"  {msg}", file=sys.stderr)
        print(
            "\nFix: every contracts/OMN-*.yaml must declare a non-empty "
            "dod_evidence list mapping each Linear DoD bullet to an "
            "executable check. See templates/ticket_contract.template.yaml.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
