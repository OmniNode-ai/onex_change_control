# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the evidence-import guardrail (OMN-16429).

Pins the two refusal shapes the `evidence-import.yml` workflow_dispatch path
must reject before it will push/PR an imported branch: a workflow-file edit,
and a mutation/deletion of a pre-existing contract/receipt file. Also proves
the allow-path (net-new files, including net-new files under the guarded
prefixes) is not over-broadly rejected, and that the CLI wrapper's exit code
matches the pure-function verdict via a real git repo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.validation.check_evidence_import_allowed import (
    ChangedFile,
    evaluate_import,
    parse_name_status,
)


class TestEvaluateImportRefusals:
    def test_refuses_workflow_file_edit(self) -> None:
        changed = [ChangedFile(status="M", path=".github/workflows/ci.yml")]
        violations = evaluate_import(changed)
        assert len(violations) == 1
        assert "workflow file" in violations[0]
        assert ".github/workflows/ci.yml" in violations[0]

    def test_refuses_new_workflow_file_too(self) -> None:
        """Adding a brand-new workflow file is refused, not just editing one —
        a net-new workflow is just as much a privilege-escalation vector as
        modifying an existing one."""
        changed = [ChangedFile(status="A", path=".github/workflows/backdoor.yml")]
        violations = evaluate_import(changed)
        assert len(violations) == 1
        assert "workflow file" in violations[0]

    @pytest.mark.parametrize("prefix", ["contracts/", "drift/dod_receipts/"])
    @pytest.mark.parametrize("status", ["M", "D", "R", "C", "T"])
    def test_refuses_mutation_of_preexisting_receipt(
        self, prefix: str, status: str
    ) -> None:
        changed = [
            ChangedFile(status=status, path=f"{prefix}OMN-99999/some-receipt.yaml")
        ]
        violations = evaluate_import(changed)
        assert len(violations) == 1
        assert "append-only" in violations[0]

    @pytest.mark.parametrize("prefix", ["contracts/", "drift/dod_receipts/"])
    def test_allows_net_new_receipt_file(self, prefix: str) -> None:
        """A brand-new (status A) receipt/contract file is exactly what a
        legitimate evidence import looks like — must not be refused."""
        changed = [ChangedFile(status="A", path=f"{prefix}OMN-16429/new-receipt.yaml")]
        assert evaluate_import(changed) == []

    def test_allows_unrelated_new_files(self) -> None:
        changed = [
            ChangedFile(status="A", path="docs/tracking/2026-08-23-daniyal-proof.md"),
            ChangedFile(status="A", path="evidence/OMN-16429/log.txt"),
        ]
        assert evaluate_import(changed) == []

    def test_collects_multiple_violations(self) -> None:
        changed = [
            ChangedFile(status="M", path=".github/workflows/ci.yml"),
            ChangedFile(status="D", path="contracts/OMN-1.yaml"),
            ChangedFile(status="A", path="evidence/OMN-16429/fine.txt"),
        ]
        violations = evaluate_import(changed)
        assert len(violations) == 2

    def test_empty_diff_is_not_a_violation_itself(self) -> None:
        """evaluate_import is pure and doesn't special-case emptiness — the
        CLI layer (main) is what refuses a zero-file import as an operator
        error, tested separately below."""
        assert evaluate_import([]) == []


class TestParseNameStatus:
    def test_parses_simple_statuses(self) -> None:
        raw = (
            "A\tevidence/OMN-1/new.yaml\n"
            "M\tcontracts/OMN-2.yaml\n"
            "D\tdrift/dod_receipts/OMN-3/old.yaml\n"
        )
        parsed = parse_name_status(raw)
        assert parsed == [
            ChangedFile(status="A", path="evidence/OMN-1/new.yaml"),
            ChangedFile(status="M", path="contracts/OMN-2.yaml"),
            ChangedFile(status="D", path="drift/dod_receipts/OMN-3/old.yaml"),
        ]

    def test_parses_rename_status_using_destination_path(self) -> None:
        raw = "R100\tcontracts/OMN-old.yaml\tcontracts/OMN-new.yaml\n"
        parsed = parse_name_status(raw)
        assert parsed == [ChangedFile(status="R", path="contracts/OMN-new.yaml")]

    def test_ignores_blank_lines(self) -> None:
        raw = "\nA\tevidence/OMN-1/new.yaml\n\n"
        assert parse_name_status(raw) == [
            ChangedFile(status="A", path="evidence/OMN-1/new.yaml")
        ]

    def test_empty_input_yields_empty_list(self) -> None:
        assert parse_name_status("") == []


@pytest.mark.unit
class TestMainCliAgainstRealGitRepo:
    """Exercises `main()` end-to-end against a real hermetic git repo — no
    mocked subprocess — so the CLI wrapper's git invocation and exit-code
    mapping are proven, not just the pure `evaluate_import` function."""

    @staticmethod
    def _init_repo(tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("base\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
        subprocess.run(["git", "branch", "base"], cwd=repo, check=True)
        return repo

    def test_exit_zero_on_clean_net_new_import(self, tmp_path: Path) -> None:
        repo = self._init_repo(tmp_path)
        (repo / "evidence").mkdir()
        (repo / "evidence" / "OMN-16429.txt").write_text("proof\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "import evidence"], cwd=repo, check=True
        )

        result = subprocess.run(
            [
                "python3",
                str(
                    Path(__file__).resolve().parents[1]
                    / "scripts"
                    / "validation"
                    / "check_evidence_import_allowed.py"
                ),
                "--base-ref",
                "base",
                "--head-ref",
                "HEAD",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_exit_one_on_workflow_file_import(self, tmp_path: Path) -> None:
        repo = self._init_repo(tmp_path)
        (repo / ".github").mkdir()
        (repo / ".github" / "workflows").mkdir(parents=True)
        (repo / ".github" / "workflows" / "sneaky.yml").write_text("on: push\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "import"], cwd=repo, check=True)

        result = subprocess.run(
            [
                "python3",
                str(
                    Path(__file__).resolve().parents[1]
                    / "scripts"
                    / "validation"
                    / "check_evidence_import_allowed.py"
                ),
                "--base-ref",
                "base",
                "--head-ref",
                "HEAD",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "workflow file" in result.stderr

    def test_exit_one_on_preexisting_receipt_mutation(self, tmp_path: Path) -> None:
        repo = self._init_repo(tmp_path)
        (repo / "contracts").mkdir()
        (repo / "contracts" / "OMN-1.yaml").write_text("v: 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "seed receipt"], cwd=repo, check=True
        )
        subprocess.run(["git", "branch", "-f", "base"], cwd=repo, check=True)

        (repo / "contracts" / "OMN-1.yaml").write_text("v: 2\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "mutate receipt"], cwd=repo, check=True
        )

        result = subprocess.run(
            [
                "python3",
                str(
                    Path(__file__).resolve().parents[1]
                    / "scripts"
                    / "validation"
                    / "check_evidence_import_allowed.py"
                ),
                "--base-ref",
                "base",
                "--head-ref",
                "HEAD",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "append-only" in result.stderr

    def test_exit_two_on_empty_diff(self, tmp_path: Path) -> None:
        repo = self._init_repo(tmp_path)
        result = subprocess.run(
            [
                "python3",
                str(
                    Path(__file__).resolve().parents[1]
                    / "scripts"
                    / "validation"
                    / "check_evidence_import_allowed.py"
                ),
                "--base-ref",
                "base",
                "--head-ref",
                "HEAD",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert "zero changed files" in result.stderr
