# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for scripts/ci/evidence_only_predicate.py (OMN-16285).

RED/GREEN corpus derived from a live audit of 5 recently merged OCC
companion PRs (#6685, #6735, #6676, #6757, #6755, 2026-08-20) plus the
negative-control shapes the exact-allowlist hard constraint exists to reject
(``scripts/``, ``.github/``, a file merely nested under ``contracts/``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from scripts.ci.evidence_only_predicate import (
    is_evidence_only_diff,
    is_evidence_path,
    main,
    parse_changed_files,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# is_evidence_path -- exact allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        # Real companion file lists (2026-08-20 audit).
        "contracts/OMN-16204.yaml",
        "drift/dod_receipts/OMN-16204/dod-OmniNode-ai-omnibase_infra-pr-2789-ci/command.yaml",
        "drift/dod_receipts/OMN-16204/occ-self-bind-pr-6685/command.yaml",
        "drift/dod_receipts/OMN-16249/dod-omn16249-consumer-widened-key-set/command.supersede.2110.yaml",
        # contracts/v1/ shape (OMN-15669) -- one recognized nesting level.
        "contracts/v1/OMN-15669.yaml",
        # .yml is accepted alongside .yaml.
        "contracts/OMN-1.yml",
        "drift/dod_receipts/OMN-1/item/run.yml",
    ],
)
def test_evidence_paths_match(path: str) -> None:
    assert is_evidence_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # The exact hard-constraint negative controls (OMN-16285 ticket body).
        "scripts/ci/ci_summary_gate.py",
        ".github/workflows/ci.yml",
        # A contract-adjacent file that is NOT itself contract YAML.
        "contracts/README.md",
        # Nested one level deeper than the recognized v1/ shape.
        "contracts/v1/nested/OMN-1.yaml",
        "contracts/v2/OMN-1.yaml",
        # Evidence-looking but outside the exact tree.
        "allowlists/skip_token_approvals.yaml",
        "grants/prod_promotion_grants.yaml",
        "drift/other/OMN-1.yaml",
        # Substring trap: contains "contracts/" but is not anchored there.
        "src/contracts/OMN-1.yaml",
        "not-drift/dod_receipts/OMN-1/item/run.yaml",
    ],
)
def test_non_evidence_paths_do_not_match(path: str) -> None:
    assert is_evidence_path(path) is False


# ---------------------------------------------------------------------------
# is_evidence_only_diff -- whole-diff predicate
# ---------------------------------------------------------------------------


def test_pure_evidence_diff_is_evidence_only() -> None:
    # PR #6755's real file list.
    files = [
        "contracts/OMN-16249.yaml",
        "drift/dod_receipts/OMN-16249/dod-OmniNode-ai-omnimarket-pr-2110/command.yaml",
        "drift/dod_receipts/OMN-16249/occ-self-bind-pr-6755/command.yaml",
    ]
    assert is_evidence_only_diff(files) is True


def test_mixed_diff_is_not_evidence_only() -> None:
    files = ["contracts/OMN-1.yaml", "scripts/ci/ci_summary_gate.py"]
    assert is_evidence_only_diff(files) is False


def test_pure_code_diff_is_not_evidence_only() -> None:
    files = [".github/workflows/ci.yml", "scripts/ci/ci_summary_gate.py"]
    assert is_evidence_only_diff(files) is False


def test_empty_diff_fails_closed_not_evidence_only() -> None:
    """An unresolved/empty diff must never be treated as safe to narrow."""

    assert is_evidence_only_diff([]) is False


def test_single_non_evidence_file_flips_an_otherwise_evidence_diff() -> None:
    """One file outside the allowlist makes the WHOLE diff non-evidence-only,
    even when every other file is squarely inside contracts/ or
    drift/dod_receipts/ -- this is the falsification control for "exact
    allowlist, not substring heuristic"."""

    files = [f"contracts/OMN-{i}.yaml" for i in range(20)]
    assert is_evidence_only_diff(files) is True
    files.append("allowlists/skip_token_approvals.yaml")
    assert is_evidence_only_diff(files) is False


# ---------------------------------------------------------------------------
# parse_changed_files
# ---------------------------------------------------------------------------


def test_parse_changed_files_drops_blank_lines() -> None:
    raw = "contracts/OMN-1.yaml\n\n  \ndrift/dod_receipts/OMN-1/item/run.yaml\n"
    assert parse_changed_files(raw) == [
        "contracts/OMN-1.yaml",
        "drift/dod_receipts/OMN-1/item/run.yaml",
    ]


def test_parse_changed_files_empty_string_is_empty_list() -> None:
    assert parse_changed_files("") == []


# ---------------------------------------------------------------------------
# main() -- GITHUB_OUTPUT wiring
# ---------------------------------------------------------------------------


def test_main_writes_evidence_only_true_to_github_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_file = tmp_path / "github_output.txt"
    monkeypatch.setenv("CHANGED_FILES", "contracts/OMN-1.yaml\n")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    assert main() == 0
    assert output_file.read_text(encoding="utf-8") == "evidence_only=true\n"


def test_main_writes_evidence_only_false_for_mixed_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_file = tmp_path / "github_output.txt"
    monkeypatch.setenv(
        "CHANGED_FILES", "contracts/OMN-1.yaml\nscripts/ci/ci_summary_gate.py\n"
    )
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    assert main() == 0
    assert output_file.read_text(encoding="utf-8") == "evidence_only=false\n"


def test_main_missing_changed_files_env_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_file = tmp_path / "github_output.txt"
    monkeypatch.delenv("CHANGED_FILES", raising=False)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    assert main() == 0
    assert output_file.read_text(encoding="utf-8") == "evidence_only=false\n"
