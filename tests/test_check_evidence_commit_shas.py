# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the Evidence-Commit SHA existence gate (OMN-15111).

Found live 2026-07-25: the ``Evidence-Commit: <sha>`` citation embedded in
receipt ``check_value`` text has been in use fleet-wide since OMN-14494 but
was never validated by anything — no CI job, no OCC receipt, no pre-commit
hook. These tests pin the fail-closed contract this gate must enforce.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from scripts.validation.check_evidence_commit_shas import check_file, main

if TYPE_CHECKING:
    from collections.abc import Callable

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


_GARBLED_SHA = "72b52d1d758597147bc0763e7918b67c42504ccd"
_SQUASH_SHA = "0c5012bfa6b44e64e856977d740ab90070a93777"


def _write_base(item_dir: Path, sha: str = _GARBLED_SHA) -> Path:
    """Write a merged base receipt citing ``sha``, and return its path."""
    item_dir.mkdir(parents=True, exist_ok=True)
    receipt = item_dir / "command.yaml"
    receipt.write_text(
        f"check_value: >-\n  grep -Fq 'Evidence-Commit: {sha}' body.txt\n"
    )
    return receipt


def _write_supersede(
    item_dir: Path,
    *,
    supersedes: str = "drift/dod_receipts/OMN-14402/dod-x/command.yaml",
    tombstone: bool = False,
    replacement_sha: str | None = _SQUASH_SHA,
    reason_sha: str | None = _GARBLED_SHA,
) -> Path:
    """Write a sibling supersession record and return its path."""
    record = item_dir / "command.supersede.6448.yaml"
    named = f" the garbled value {reason_sha} is retired" if reason_sha else ""
    lines = [
        "schema_version: '1.0.0'",
        "ticket_id: OMN-14402",
        "evidence_item_id: dod-x",
        "check_type: command",
        f"supersedes: {supersedes}",
        f"reason: rebind a garbled commit identifier;{named}",
        "superseder: test",
        "created_at: '2026-08-14T07:10:00Z'",
        f"tombstone: {'true' if tombstone else 'false'}",
    ]
    if replacement_sha is not None:
        lines += [
            "replacement:",
            "  commit_sha: " + replacement_sha,
            "  probe_command: >-",
            f"    body='Evidence-Commit: {replacement_sha}'",
        ]
    record.write_text("\n".join(lines) + "\n")
    return record


class TestSupersessionCorrectionPath:
    """OMN-16007: the append-only correction path this gate must honor.

    The OCC Append-Only Gate forbids editing a merged receipt, so a garbled
    Evidence-Commit in one is correctable ONLY by a net-new sibling
    supersession record. These pin that the exemption is real but narrow.
    """

    def test_supersede_record_retires_the_garbled_sha(self, tmp_path: Path) -> None:
        item = tmp_path / "drift/dod_receipts/OMN-14402/dod-x"
        base = _write_base(item)
        _write_supersede(item)
        # Neither SHA resolves in this fake resolver; only the supersession
        # record excuses the base receipt's citation.
        assert check_file(base, commit_exists=_resolver(set())) == []

    def test_replacement_still_citing_the_sha_does_not_excuse_it(
        self, tmp_path: Path
    ) -> None:
        item = tmp_path / "drift/dod_receipts/OMN-14402/dod-x"
        base = _write_base(item)
        # The "correction" re-binds the same garbled value — it corrected
        # nothing and must not launder it.
        _write_supersede(item, replacement_sha=_GARBLED_SHA)
        violations = check_file(base, commit_exists=_resolver(set()))
        assert len(violations) == 1
        assert _GARBLED_SHA in violations[0]

    def test_reason_must_name_the_sha_it_retires(self, tmp_path: Path) -> None:
        """Absence from the replacement alone is not a correction.

        Without this, a record correcting SHA A would silently excuse an
        unrelated garbled SHA B in the same receipt, purely because B is also
        missing from the replacement — a fail-open hole.
        """
        item = tmp_path / "drift/dod_receipts/OMN-14402/dod-x"
        base = _write_base(item)
        _write_supersede(item, reason_sha=None)
        violations = check_file(base, commit_exists=_resolver(set()))
        assert len(violations) == 1
        assert _GARBLED_SHA in violations[0]

    def test_tombstone_record_does_not_excuse(self, tmp_path: Path) -> None:
        item = tmp_path / "drift/dod_receipts/OMN-14402/dod-x"
        base = _write_base(item)
        _write_supersede(item, tombstone=True, replacement_sha=None)
        assert len(check_file(base, commit_exists=_resolver(set()))) == 1

    def test_record_for_a_different_item_does_not_excuse(self, tmp_path: Path) -> None:
        item = tmp_path / "drift/dod_receipts/OMN-14402/dod-x"
        base = _write_base(item)
        _write_supersede(
            item,
            supersedes="drift/dod_receipts/OMN-14402/dod-other/command.yaml",
        )
        assert len(check_file(base, commit_exists=_resolver(set()))) == 1

    def test_malformed_record_fails_closed(self, tmp_path: Path) -> None:
        item = tmp_path / "drift/dod_receipts/OMN-14402/dod-x"
        base = _write_base(item)
        (item / "command.supersede.6448.yaml").write_text("{[not: valid yaml\n")
        assert len(check_file(base, commit_exists=_resolver(set()))) == 1

    def test_supersede_record_gets_no_exemption_itself(self, tmp_path: Path) -> None:
        """Net-new records are held strictly — the corpus stays shrink-only."""
        item = tmp_path / "drift/dod_receipts/OMN-14402/dod-x"
        item.mkdir(parents=True)
        record = _write_supersede(item, replacement_sha=_FABRICATED_SHA)
        violations = check_file(record, commit_exists=_resolver({_REAL_SHA}))
        assert len(violations) == 1
        assert _FABRICATED_SHA in violations[0]

    def test_exemption_is_keyed_to_the_exact_sha(self, tmp_path: Path) -> None:
        """A different bad SHA in the same base file still fails."""
        item = tmp_path / "drift/dod_receipts/OMN-14402/dod-x"
        item.mkdir(parents=True)
        base = item / "command.yaml"
        base.write_text(
            "check_value: >-\n"
            f"  grep -Fq 'Evidence-Commit: {_GARBLED_SHA}' a.txt && "
            f"grep -Fq 'Evidence-Commit: {_FABRICATED_SHA}' b.txt\n"
        )
        _write_supersede(item)
        violations = check_file(base, commit_exists=_resolver(set()))
        assert len(violations) == 1
        assert _FABRICATED_SHA in violations[0]
        assert _GARBLED_SHA not in violations[0]

    def test_violation_message_names_the_append_only_correction_path(
        self, tmp_path: Path
    ) -> None:
        receipt = tmp_path / "command.yaml"
        receipt.write_text(
            "check_value: >-\n"
            f"  grep -Fq 'Evidence-Commit: {_FABRICATED_SHA}' body.txt\n"
        )
        violations = check_file(receipt, commit_exists=_resolver(set()))
        assert "command.supersede.<NNNN>.yaml" in violations[0]
        assert "never by editing this file" in violations[0]


class TestLiveCorpusOmn14402:
    """The real receipt this mechanism was built for stays resolved."""

    def test_omn14402_receipt_and_its_supersession_are_clean(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        item = (
            repo_root
            / "drift/dod_receipts/OMN-14402"
            / "dod-omn14402-occ-deploy-gate-self-eligibility"
        )
        base = item / "command.yaml"
        record = item / "command.supersede.6448.yaml"
        assert base.is_file()
        assert record.is_file(), (
            "the append-only correction record is missing; the base receipt's "
            "garbled Evidence-Commit would be unexcused"
        )
        # Base still carries the garbled SHA verbatim — it is immutable.
        assert _GARBLED_SHA in base.read_text()
        # Real resolver: the squash commit exists, the garbled one does not.
        assert check_file(base) == []
        assert check_file(record) == []
