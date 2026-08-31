# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for the false-Done checker."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from onex_change_control.scripts import check_pr_touches_ticket_files as checker

if TYPE_CHECKING:
    from pathlib import Path


_OMN_10175_DESCRIPTION = """Substrate stabilization Task 12 / Wave 8.

Ordering constraint: this must land after the gated Wave 3/Wave 6 decisions
and Task 8 runtime observability proof, because it cross-links every owning
ADR and final seam resolution.

Goal:

Create a master interface map that names every cross-plan interface and
assigns each to one owning plan, preventing future substrate seams.

Files:

* Create `docs/integration/2026-04-27-substrate-integration-map.md`.
* Create `scripts/ci/check_integration_map_freshness.py` as an advisory check.

Required map columns:

* Interface name
* Producer plan
* Consumer plan(s)
* Topic / model / file path
* Owning ADR
* Status (`proposed`, `merged`, `live`)

Required content:

* For each of the 10 seams, list the interface(s) touched and the resolution.
* Cross-link every ADR landed in Wave 3.
* Add a `How to add a new interface` section: must land an ADR, update this
  map in the same PR, declare topics in contract YAML, and name a producer
  and consumer plan.

Advisory CI check:

* Fails if a new `docs/plans/2026-04-2x-*.md` introduces a topic name not
  present in the integration map.
* Mark advisory first; promote to required after one week of green runs.
"""

_PR_122_FILES = {
    "files": [
        {"path": "docs/diagnosis-2026-04-27-merge-sweep-shim-still-broken.md"},
        {
            "path": (
                "docs/plans/"
                "2026-04-27-emit-daemon-omnimarket-cutover-and-runtime-"
                "standardization.md"
            )
        },
        {"path": "docs/plans/2026-04-27-skills-to-market-orchestrators-plan.md"},
        {
            "path": (
                "docs/plans/2026-04-27-substrate-stabilization-and-integration-fixes.md"
            )
        },
        {"path": "docs/tracking/2026-04-27-codex-merge-controller-ledger.md"},
        {"path": "docs/tracking/2026-04-27-plan-integration-gap-analysis.md"},
    ]
}


def _write_text(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.unit
def test_explicit_required_paths_preferred_and_matching_diff_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    description = _write_text(
        tmp_path,
        "ticket.md",
        """required_paths:
  - docs/integration/2026-04-27-substrate-integration-map.md
  - scripts/ci/check_integration_map_freshness.py

Context:
  docs/ignore/this.md should not matter.
""",
    )
    pr_files = _write_text(
        tmp_path,
        "pr_files.json",
        json.dumps(
            {
                "files": [
                    {
                        "path": (
                            "docs/integration/2026-04-27-substrate-integration-map.md"
                        )
                    }
                ]
            }
        ),
    )

    rc = checker.main(
        [
            "--ticket",
            "OMN-9999",
            "--ticket-description-file",
            str(description),
            "--pr-files-file",
            str(pr_files),
        ]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["required_paths_source"] == "metadata"
    assert report["matched_paths"] == [
        "docs/integration/2026-04-27-substrate-integration-map.md"
    ]


@pytest.mark.unit
def test_explicit_required_paths_non_matching_diff_fails_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    description = _write_text(
        tmp_path,
        "ticket.md",
        """required_paths:
- docs/integration/2026-04-27-substrate-integration-map.md
- scripts/ci/check_integration_map_freshness.py
""",
    )
    pr_files = _write_text(
        tmp_path,
        "pr_files.json",
        json.dumps({"files": [{"path": "docs/plans/2026-04-28-plan.md"}]}),
    )

    rc = checker.main(
        [
            "--ticket",
            "OMN-9999",
            "--ticket-description-file",
            str(description),
            "--pr-files-file",
            str(pr_files),
        ]
    )
    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert report["required_paths"] == [
        "docs/integration/2026-04-27-substrate-integration-map.md",
        "scripts/ci/check_integration_map_freshness.py",
    ]
    assert report["missing_paths"] == report["required_paths"]


@pytest.mark.unit
def test_context_only_paths_are_treated_as_inapplicable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    description = _write_text(
        tmp_path,
        "ticket.md",
        """Context:
This references docs/integration/2026-04-27-substrate-integration-map.md,
but only as background.

Background:
scripts/ci/check_integration_map_freshness.py is mentioned here too.
""",
    )

    rc = checker.main(
        [
            "--ticket",
            "OMN-9998",
            "--ticket-description-file",
            str(description),
            "--pr-file",
            "docs/plans/2026-04-28-plan.md",
        ]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "inapplicable"
    assert report["required_paths"] == []


@pytest.mark.unit
def test_no_required_paths_is_inapplicable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    description = _write_text(
        tmp_path,
        "ticket.md",
        """Goal:
Do a design review and summarize findings.
""",
    )

    rc = checker.main(
        [
            "--ticket",
            "OMN-9997",
            "--ticket-description-file",
            str(description),
            "--pr-file",
            "docs/plans/2026-04-28-plan.md",
        ]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "inapplicable"


@pytest.mark.unit
def test_omn_10175_regression_replay_fails_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    description = _write_text(tmp_path, "omn_10175.md", _OMN_10175_DESCRIPTION)
    pr_files = _write_text(tmp_path, "pr_122.json", json.dumps(_PR_122_FILES))

    rc = checker.main(
        [
            "--ticket",
            "OMN-10175",
            "--ticket-description-file",
            str(description),
            "--pr-files-file",
            str(pr_files),
        ]
    )
    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert report["ticket_id"] == "OMN-10175"
    assert report["required_paths_source"] == "sections"
    assert report["matched_paths"] == []
    assert report["required_paths"] == [
        "docs/integration/2026-04-27-substrate-integration-map.md",
        "scripts/ci/check_integration_map_freshness.py",
    ]
    assert len(report["pr_files"]) == 6
