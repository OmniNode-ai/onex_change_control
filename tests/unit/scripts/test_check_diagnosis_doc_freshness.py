# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for the diagnosis/audit freshness checker."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from onex_change_control.scripts import check_diagnosis_doc_freshness as checker

if TYPE_CHECKING:
    from pathlib import Path


def _write_doc(tmp_path: Path, name: str, body: str) -> Path:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(exist_ok=True)
    path = docs_dir / name
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.unit
def test_fresh_live_state_doc_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _write_doc(
        tmp_path,
        "diagnosis-fresh.md",
        """---
verified_live_at: 2026-04-28T10:00:00Z
evidence_kind: live_state
verification_status: verified
verified_by: ci_static
---

fresh
""",
    )

    rc = checker.main([str(doc), "--now", "2026-04-28T12:00:00Z"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["stale_docs"] == []


@pytest.mark.unit
def test_missing_required_field_fails_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _write_doc(
        tmp_path,
        "audit-missing-field.md",
        """---
verified_live_at: 2026-04-28T10:00:00Z
evidence_kind: live_state
verification_status: verified
---

missing verified_by
""",
    )

    rc = checker.main([str(doc), "--now", "2026-04-28T12:00:00Z"])
    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert report["stale_docs"][0]["reason"] == "missing_required_field:verified_by"


@pytest.mark.unit
def test_stale_live_state_doc_fails_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _write_doc(
        tmp_path,
        "diagnosis-stale.md",
        """---
verified_live_at: 2026-04-27T09:00:00Z
evidence_kind: live_state
verification_status: verified
verified_by: local_macos_operator
---

stale
""",
    )

    rc = checker.main([str(doc), "--now", "2026-04-28T12:00:00Z"])
    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stale_docs"][0]["reason"] == "stale_live_state"
    assert report["stale_docs"][0]["verified_live_at"] == "2026-04-27T09:00:00+00:00"


@pytest.mark.unit
def test_historical_record_older_than_24_hours_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _write_doc(
        tmp_path,
        "audit-historical.md",
        """---
verified_live_at: 2026-04-20T09:00:00Z
evidence_kind: historical_record
verification_status: verified
verified_by: ci_static
---

historical
""",
    )

    rc = checker.main([str(doc), "--now", "2026-04-28T12:00:00Z"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["stale_docs"] == []


@pytest.mark.unit
def test_bulk_stamped_live_state_warns_in_soft_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _write_doc(
        tmp_path,
        "diagnosis-bulk-stamped.md",
        """---
verified_live_at: 2026-04-28T11:00:00Z
evidence_kind: live_state
verification_status: bulk_stamped_unverified
verified_by: ci_static
---

bulk stamped
""",
    )

    rc = checker.main([str(doc), "--now", "2026-04-28T12:00:00Z", "--soft"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "warning"
    assert report["stale_docs"][0]["reason"] == "bulk_stamped_unverified_live_state"
