# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the Evidence-Commit SHA existence gate (OMN-15111).

Found live 2026-07-25: the ``Evidence-Commit: <sha>`` citation embedded in
receipt ``check_value`` text has been in use fleet-wide since OMN-14494 but
was never validated by anything — no CI job, no OCC receipt, no pre-commit
hook. These tests pin the fail-closed contract this gate must enforce.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scripts.validation.check_evidence_commit_shas import check_file, main

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_REAL_SHA = "53e14f927a6ef80910fabeeabe58576b4cb21087"
_FABRICATED_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _resolver(known: set[str]) -> Callable[[str], bool]:
    def _check(sha: str) -> bool:
        return sha in known

    return _check


class TestCheckFile:
    def test_no_evidence_commit_citation_is_clean(self, tmp_path: Path) -> None:
        receipt = tmp_path / "command.yaml"
        receipt.write_text("check_value: gh pr view 1 --json state\n")
        assert check_file(receipt, commit_exists=_resolver(set())) == []

    def test_real_sha_passes(self, tmp_path: Path) -> None:
        receipt = tmp_path / "command.yaml"
        receipt.write_text(
            f"check_value: >-\n  grep -Fq 'Evidence-Commit: {_REAL_SHA}' body.txt\n"
        )
        violations = check_file(receipt, commit_exists=_resolver({_REAL_SHA}))
        assert violations == []

    def test_fabricated_sha_fails(self, tmp_path: Path) -> None:
        # Seeded violation: a well-formed 40-char hex string that is not a
        # real commit (commit_exists resolver knows nothing about it).
        receipt = tmp_path / "command.yaml"
        receipt.write_text(
            "check_value: >-\n"
            f"  grep -Fq 'Evidence-Commit: {_FABRICATED_SHA}' body.txt\n"
        )
        violations = check_file(receipt, commit_exists=_resolver({_REAL_SHA}))
        assert len(violations) == 1
        assert _FABRICATED_SHA in violations[0]
        assert "does not resolve to a real commit" in violations[0]

    def test_multiple_citations_deduplicated_and_each_checked(
        self, tmp_path: Path
    ) -> None:
        receipt = tmp_path / "command.yaml"
        receipt.write_text(
            "check_value: >-\n"
            f"  grep -Fq 'Evidence-Commit: {_REAL_SHA}' a.txt && "
            f"grep -Fq 'Evidence-Commit: {_FABRICATED_SHA}' b.txt && "
            f"grep -Fq 'Evidence-Commit: {_REAL_SHA}' c.txt\n"
        )
        violations = check_file(receipt, commit_exists=_resolver({_REAL_SHA}))
        assert len(violations) == 1
        assert _FABRICATED_SHA in violations[0]


class TestMain:
    def test_clean_receipt_exits_zero(self, tmp_path: Path) -> None:
        receipt = tmp_path / "command.yaml"
        receipt.write_text("check_value: gh pr view 1 --json state\n")
        assert main([str(receipt)]) == 0

    def test_missing_file_is_skipped_not_a_violation(self, tmp_path: Path) -> None:
        missing = tmp_path / "deleted.yaml"
        assert main([str(missing)]) == 0

    def test_no_files_exits_zero(self) -> None:
        assert main([]) == 0
