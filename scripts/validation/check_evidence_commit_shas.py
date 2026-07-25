# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Evidence-Commit SHA existence gate (OMN-15111).

Found live 2026-07-25: the informal ``Evidence-Commit: <sha>`` trailer
convention — cite the onex_change_control commit a product PR's
``Evidence-Source`` line resolves to — has been embedded fleet-wide into
receipt ``check_value`` / ``probe_command`` / ``actual_output`` text since
OMN-14494, but nothing has ever validated that the cited SHA is a real
commit. It is pure decorative text that looks authoritative but is checked
by no gate.

This gate rejects any staged/modified ``drift/dod_receipts/**/*.yaml`` file
that embeds an ``Evidence-Commit: <sha>`` citation whose SHA does not
resolve to a real commit object in **this repository's local git history**.
It is purely offline (``git cat-file -e <sha>^{commit}``, no network calls),
so it runs at pre-commit speed — the authoring-time backstop for the
humans/tooling who type these SHAs into receipt text.

The cross-repo, network-dependent check — does the cited SHA also match the
*current* binding of the product PR's ``Evidence-Source`` line? — is a
separate concern that needs GitHub API access and lives in the CI-side
companion (``omnibase_core``'s ``receipt-gate.yml`` step, backed by
``validator_evidence_commit_binding_cli``). This gate only proves the SHA is
not fabricated/truncated; it does not (and, offline, cannot) prove it is
bound to any specific product PR.

Exit codes: 0 = every cited Evidence-Commit SHA resolves locally; 1 = violations found.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# Matches "Evidence-Commit: <sha>" wherever it appears in the file's raw
# text (it is typically embedded inside a YAML string value — a
# check_value/probe_command/actual_output field — not a top-level YAML key),
# capturing a well-formed 7-40 char hex SHA immediately after the colon.
_EVIDENCE_COMMIT_RE = re.compile(r"Evidence-Commit:\s*([0-9a-fA-F]{7,40})\b")


def _commit_exists_locally(sha: str) -> bool:
    """True iff ``sha`` is a real commit reachable in the local git object store."""
    result = subprocess.run(  # noqa: S603
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def check_file(
    path: Path, *, commit_exists: Callable[[str], bool] = _commit_exists_locally
) -> list[str]:
    """Return violations for each unresolvable Evidence-Commit SHA in ``path``."""
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    shas = _EVIDENCE_COMMIT_RE.findall(text)
    for sha in sorted(set(shas)):
        if not commit_exists(sha):
            violations.append(
                f"{path}: Evidence-Commit '{sha}' does not resolve to a real "
                "commit in this repository's local git history (fabricated, "
                "truncated, or the local clone is missing the commit — run "
                "`git fetch` and retry before assuming it is fabricated)."
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evidence-Commit SHA existence gate: every 'Evidence-Commit: <sha>' "
            "citation embedded in a staged receipt YAML must resolve to a real "
            "commit in this repo's local git history (OMN-15111)."
        )
    )
    parser.add_argument(
        "files", nargs="*", help="Receipt YAML paths (from pre-commit)."
    )
    args = parser.parse_args(argv)

    all_violations: list[str] = []
    for file_arg in args.files:
        path = Path(file_arg)
        if not path.is_file():
            continue  # deleted/renamed paths are not this gate's concern
        all_violations.extend(check_file(path))

    if all_violations:
        print(
            f"Evidence-Commit SHA existence gate: {len(all_violations)} violation(s):\n"
        )
        for violation in all_violations:
            print(f"  {violation}")
        print(
            "\nFix the receipt (cite the real onex_change_control commit that "
            "the bound Evidence-Source actually resolves to); never bypass the "
            "gate."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
