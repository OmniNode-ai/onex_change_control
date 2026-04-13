# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for scripts/ci/run_contract_compliance_check.py."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    import pytest

# Add scripts/ci to path so we can import directly
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "ci"))

from run_contract_compliance_check import (  # type: ignore[import-not-found]
    _RESULT_BLOCK,
    _RESULT_PASS,
    _check_command,
    _check_file_exists,
    _check_grep,
    _check_test_exists,
    _extract_ticket_id,
    _find_contracts_dir,
    run_compliance_check,
)

# ---------------------------------------------------------------------------
# _extract_ticket_id
# ---------------------------------------------------------------------------


def test_extract_ticket_id_from_title() -> None:
    pr_json = (
        '{"title": "fix: something [OMN-1234]", "headRefName": "main", "body": ""}'
    )
    with patch("run_contract_compliance_check._run", return_value=(0, pr_json, "")):
        result = _extract_ticket_id(42, "OmniNode-ai/omnimarket")
    assert result == "OMN-1234"


def test_extract_ticket_id_from_branch() -> None:
    pr_json = (
        '{"title": "no ticket", "headRefName": "jonah/omn-5678-my-fix", "body": ""}'
    )
    with patch("run_contract_compliance_check._run", return_value=(0, pr_json, "")):
        result = _extract_ticket_id(42, "OmniNode-ai/omnimarket")
    assert result == "OMN-5678"


def test_extract_ticket_id_none_when_missing() -> None:
    pr_json = '{"title": "chore: housekeeping", "headRefName": "fix-stuff", "body": ""}'
    with patch("run_contract_compliance_check._run", return_value=(0, pr_json, "")):
        result = _extract_ticket_id(42, "OmniNode-ai/omnimarket")
    assert result is None


def test_extract_ticket_id_gh_failure() -> None:
    with patch(
        "run_contract_compliance_check._run", return_value=(1, "", "auth error")
    ):
        result = _extract_ticket_id(42, "OmniNode-ai/omnimarket")
    assert result is None


# ---------------------------------------------------------------------------
# _find_contracts_dir
# ---------------------------------------------------------------------------


def test_find_contracts_dir_explicit(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    result = _find_contracts_dir(str(contracts), Path(__file__))
    assert result == contracts.resolve()


def test_find_contracts_dir_local_fallback(tmp_path: Path) -> None:
    # Explicit path resolves correctly regardless of CWD
    script = tmp_path / "scripts" / "ci" / "run_contract_compliance_check.py"
    script.parent.mkdir(parents=True)
    script.touch()
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    result = _find_contracts_dir(str(contracts), script)
    assert result == contracts.resolve()


# ---------------------------------------------------------------------------
# Check runners
# ---------------------------------------------------------------------------


def test_check_test_exists_pass(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").touch()
    result, _ = _check_test_exists("tests/test_*.py", tmp_path)
    assert result == _RESULT_PASS


def test_check_test_exists_block(tmp_path: Path) -> None:
    result, _ = _check_test_exists("tests/test_nonexistent_*.py", tmp_path)
    assert result == _RESULT_BLOCK


def test_check_file_exists_pass(tmp_path: Path) -> None:
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "output.json").touch()
    result, _ = _check_file_exists("dist/output.json", tmp_path)
    assert result == _RESULT_PASS


def test_check_file_exists_block(tmp_path: Path) -> None:
    result, _ = _check_file_exists("dist/missing.json", tmp_path)
    assert result == _RESULT_BLOCK


def test_check_grep_pass(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "handler.py").write_text("def my_handler(): pass\n")
    result, _ = _check_grep({"pattern": "my_handler", "path": "src"}, tmp_path)
    assert result == _RESULT_PASS


def test_check_grep_block(tmp_path: Path) -> None:
    result, _ = _check_grep(
        {"pattern": "definitely_not_here_xyz", "path": "."}, tmp_path
    )
    assert result == _RESULT_BLOCK


def test_check_grep_bad_value(tmp_path: Path) -> None:
    result, detail = _check_grep("not-a-dict", tmp_path)
    assert result == _RESULT_BLOCK
    assert "dict" in detail


def test_check_command_pass(tmp_path: Path) -> None:
    result, _ = _check_command("exit 0", tmp_path)
    assert result == _RESULT_PASS


def test_check_command_block(tmp_path: Path) -> None:
    result, _ = _check_command("exit 1", tmp_path)
    assert result == _RESULT_BLOCK


def test_check_command_placeholder_substitution(tmp_path: Path) -> None:
    """Placeholders {pr} and {repo} must be substituted before execution."""
    # echo the args; grep for the expected values in the output
    result, _detail = _check_command(
        "echo pr={pr} repo={repo}",
        tmp_path,
        pr_number=586,
        repo="OmniNode-ai/omnidash",
    )
    assert result == _RESULT_PASS


def test_check_command_precommit_missing_warns(tmp_path: Path) -> None:
    """When pre-commit is absent, demote to WARN rather than BLOCK."""
    from run_contract_compliance_check import _RESULT_WARN

    with patch(
        "run_contract_compliance_check._run",
        side_effect=[
            (1, "", "not found"),  # which pre-commit → not installed
        ],
    ):
        result, detail = _check_command("pre-commit run --all-files", tmp_path)
    assert result == _RESULT_WARN
    assert "not installed" in detail


# ---------------------------------------------------------------------------
# Emergency bypass
# ---------------------------------------------------------------------------


def test_emergency_bypass_skips_all_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMERGENCY_BYPASS", "jonah-prod-incident")
    from run_contract_compliance_check import main

    with patch("sys.argv", ["prog", "--pr", "1", "--repo", "OmniNode-ai/omnimarket"]):
        rc = main()
    assert rc == 0


# ---------------------------------------------------------------------------
# run_compliance_check integration
# ---------------------------------------------------------------------------


def test_no_ticket_id_returns_pass(tmp_path: Path) -> None:
    with patch("run_contract_compliance_check._extract_ticket_id", return_value=None):
        rc = run_compliance_check(
            1, "OmniNode-ai/omnimarket", tmp_path / "contracts", tmp_path
        )
    assert rc == 0


def test_no_contract_file_returns_pass_with_warn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    with patch(
        "run_contract_compliance_check._extract_ticket_id", return_value="OMN-9999"
    ):
        rc = run_compliance_check(1, "OmniNode-ai/omnimarket", contracts, tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARN" in out


def test_contract_with_no_dod_evidence_returns_pass(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    contract_yaml = textwrap.dedent("""
        schema_version: "1.0.0"
        ticket_id: "OMN-1001"
        summary: "Test ticket"
        is_seam_ticket: false
        interface_change: false
        interfaces_touched: []
        evidence_requirements: []
        emergency_bypass:
          enabled: false
          justification: ""
          follow_up_ticket_id: ""
    """)
    (contracts / "OMN-1001.yaml").write_text(contract_yaml)
    with patch(
        "run_contract_compliance_check._extract_ticket_id", return_value="OMN-1001"
    ):
        rc = run_compliance_check(1, "OmniNode-ai/omnimarket", contracts, tmp_path)
    assert rc == 0


def test_contract_with_passing_file_exists_check(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_omn1002.py").touch()
    contract_yaml = textwrap.dedent("""
        schema_version: "1.0.0"
        ticket_id: "OMN-1002"
        summary: "Test ticket with dod"
        is_seam_ticket: false
        interface_change: false
        interfaces_touched: []
        evidence_requirements: []
        emergency_bypass:
          enabled: false
          justification: ""
          follow_up_ticket_id: ""
        dod_evidence:
          - id: dod-001
            description: "Test file must exist"
            checks:
              - check_type: file_exists
                check_value: "tests/test_omn1002.py"
            status: pending
    """)
    (contracts / "OMN-1002.yaml").write_text(contract_yaml)
    with patch(
        "run_contract_compliance_check._extract_ticket_id", return_value="OMN-1002"
    ):
        rc = run_compliance_check(1, "OmniNode-ai/omnimarket", contracts, tmp_path)
    assert rc == 0


def test_contract_with_failing_check_returns_block(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    contract_yaml = textwrap.dedent("""
        schema_version: "1.0.0"
        ticket_id: "OMN-1003"
        summary: "Test ticket with failing dod"
        is_seam_ticket: false
        interface_change: false
        interfaces_touched: []
        evidence_requirements: []
        emergency_bypass:
          enabled: false
          justification: ""
          follow_up_ticket_id: ""
        dod_evidence:
          - id: dod-001
            description: "Missing file must exist"
            checks:
              - check_type: file_exists
                check_value: "nonexistent/path/missing.py"
            status: pending
    """)
    (contracts / "OMN-1003.yaml").write_text(contract_yaml)
    with patch(
        "run_contract_compliance_check._extract_ticket_id", return_value="OMN-1003"
    ):
        rc = run_compliance_check(1, "OmniNode-ai/omnimarket", contracts, tmp_path)
    assert rc == 1
