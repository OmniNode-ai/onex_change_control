# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for check_dod_compliance.py CI script.

Tests cover:
1. Exemption loading (cutoff date, explicit exemptions, expiry handling)
2. Contract existence check logic
3. Evidence receipt parsing (clean vs failed)
4. Summary table formatting
5. Exit code behavior (0 on all pass, 1 on any fail)
6. Missing LINEAR_API_KEY -- reports degraded, does NOT exit clean-pass
7. Partial repo clones -- receipts missing
8. Empty batch window -- 0 tickets, not error
9. Receipt exists but has failures -- check 3 fails even though 1-2 pass
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts dir to path so we can import the module
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import check_dod_compliance  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_contracts(tmp_path: Path) -> Path:
    """Create a temporary contracts directory structure."""
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    return contracts


@pytest.fixture
def tmp_exemptions(tmp_path: Path) -> Path:
    """Create a temporary exemptions file."""
    import yaml

    exemptions_path = tmp_path / "dod_sweep_exemptions.yaml"
    data = {
        "cutoff_date": "2026-03-01",
        "exemptions": [
            {
                "ticket_id": "OMN-1000",
                "reason": "Legacy ticket",
            },
            {
                "ticket_id": "OMN-1001",
                "reason": "Expired exemption",
                "expires_on": "2025-01-01",
            },
        ],
    }
    exemptions_path.write_text(yaml.dump(data))
    return exemptions_path


# ---------------------------------------------------------------------------
# Tests: Exemption loading
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExemptionLoading:
    """Tests for exemption loading and filtering."""

    def test_load_cutoff_date(self, tmp_exemptions: Path) -> None:
        """Cutoff date is loaded correctly."""
        cutoff, _exempt_ids = check_dod_compliance.load_exemptions(tmp_exemptions)
        assert cutoff == "2026-03-01"

    def test_load_explicit_exemptions(self, tmp_exemptions: Path) -> None:
        """Explicit exemptions are loaded (non-expired)."""
        _, exempt_ids = check_dod_compliance.load_exemptions(tmp_exemptions)
        assert "OMN-1000" in exempt_ids

    def test_expired_exemptions_skipped(self, tmp_exemptions: Path) -> None:
        """Expired exemptions are not included."""
        _, exempt_ids = check_dod_compliance.load_exemptions(tmp_exemptions)
        assert "OMN-1001" not in exempt_ids

    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        """Missing exemptions file returns None cutoff and empty set."""
        cutoff, exempt_ids = check_dod_compliance.load_exemptions(
            tmp_path / "nonexistent.yaml"
        )
        assert cutoff is None
        assert len(exempt_ids) == 0

    def test_is_exempt_by_cutoff(self) -> None:
        """Ticket completed before cutoff is exempt."""
        reason = check_dod_compliance.is_exempt(
            "OMN-2000", "2026-02-15T00:00:00Z", "2026-03-01", set()
        )
        assert reason is not None
        assert "cutoff" in reason.lower()

    def test_is_exempt_by_explicit_id(self) -> None:
        """Ticket in exempt set is exempt."""
        reason = check_dod_compliance.is_exempt(
            "OMN-1000", "2026-03-15T00:00:00Z", "2026-03-01", {"OMN-1000"}
        )
        assert reason is not None

    def test_is_not_exempt(self) -> None:
        """Ticket after cutoff and not in exempt set is not exempt."""
        reason = check_dod_compliance.is_exempt(
            "OMN-2000", "2026-03-15T00:00:00Z", "2026-03-01", set()
        )
        assert reason is None


# ---------------------------------------------------------------------------
# Tests: Artifact checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestArtifactChecks:
    """Tests for the 3 artifact checks."""

    def test_contract_exists_pass(self, tmp_contracts: Path) -> None:
        """Check 1 passes when contract YAML exists."""
        (tmp_contracts / "OMN-2000.yaml").write_text("ticket_id: OMN-2000")
        status, _ = check_dod_compliance.check_contract_exists(
            "OMN-2000", tmp_contracts
        )
        assert status == "PASS"

    def test_contract_exists_fail(self, tmp_contracts: Path) -> None:
        """Check 1 fails when contract YAML is missing."""
        status, _ = check_dod_compliance.check_contract_exists(
            "OMN-9999", tmp_contracts
        )
        assert status == "FAIL"

    def test_receipt_exists_pass(self, tmp_contracts: Path) -> None:
        """Check 2 passes when receipt exists."""
        repo_root = tmp_contracts.parent
        evidence = repo_root / ".evidence" / "OMN-2000"
        evidence.mkdir(parents=True)
        (evidence / "dod_report.json").write_text('{"result": {"failed": 0}}')
        status, _ = check_dod_compliance.check_receipt_exists("OMN-2000", tmp_contracts)
        assert status == "PASS"

    def test_receipt_exists_fail(self, tmp_contracts: Path) -> None:
        """Check 2 fails when receipt is missing."""
        status, _ = check_dod_compliance.check_receipt_exists("OMN-9999", tmp_contracts)
        assert status == "FAIL"

    def test_receipt_clean_pass(self, tmp_contracts: Path) -> None:
        """Check 3 passes when receipt has 0 failures."""
        repo_root = tmp_contracts.parent
        evidence = repo_root / ".evidence" / "OMN-2000"
        evidence.mkdir(parents=True)
        (evidence / "dod_report.json").write_text(
            json.dumps({"result": {"failed": 0, "passed": 6}})
        )
        status, _ = check_dod_compliance.check_receipt_clean("OMN-2000", tmp_contracts)
        assert status == "PASS"

    def test_receipt_clean_fail_with_failures(self, tmp_contracts: Path) -> None:
        """Check 3 fails when receipt has failures."""
        repo_root = tmp_contracts.parent
        evidence = repo_root / ".evidence" / "OMN-2000"
        evidence.mkdir(parents=True)
        (evidence / "dod_report.json").write_text(
            json.dumps({"result": {"failed": 2, "passed": 4}})
        )
        status, detail = check_dod_compliance.check_receipt_clean(
            "OMN-2000", tmp_contracts
        )
        assert status == "FAIL"
        assert "2" in detail

    def test_receipt_clean_fail_no_receipt(self, tmp_contracts: Path) -> None:
        """Check 3 fails when receipt does not exist."""
        status, _ = check_dod_compliance.check_receipt_clean("OMN-9999", tmp_contracts)
        assert status == "FAIL"

    def test_receipt_clean_unknown_missing_field(self, tmp_contracts: Path) -> None:
        """Check 3 returns UNKNOWN when result.failed field missing."""
        repo_root = tmp_contracts.parent
        evidence = repo_root / ".evidence" / "OMN-2000"
        evidence.mkdir(parents=True)
        (evidence / "dod_report.json").write_text(json.dumps({"some_other": "data"}))
        status, _ = check_dod_compliance.check_receipt_clean("OMN-2000", tmp_contracts)
        assert status == "UNKNOWN"


# ---------------------------------------------------------------------------
# Tests: Summary formatting
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSummaryFormatting:
    """Tests for the markdown summary table."""

    def test_summary_includes_counts(self) -> None:
        """Summary includes passed/failed/exempt counts."""
        results: list[dict[str, object]] = [
            {"ticket_id": "OMN-1", "title": "T1", "status": "PASS", "details": "ok"},
            {"ticket_id": "OMN-2", "title": "T2", "status": "FAIL", "details": "bad"},
        ]
        summary = check_dod_compliance.format_summary_table(results, 7)
        assert "Passed: 1" in summary
        assert "Failed: 1" in summary

    def test_summary_degraded_warning(self) -> None:
        """Degraded mode includes warning in summary."""
        summary = check_dod_compliance.format_summary_table([], 7, degraded=True)
        assert "DEGRADED" in summary

    def test_summary_scope_honesty(self) -> None:
        """Summary includes scope disclaimer."""
        summary = check_dod_compliance.format_summary_table([], 7)
        assert "NOT checked" in summary


# ---------------------------------------------------------------------------
# Tests: Main function
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMain:
    """Tests for the main() entry point."""

    def test_missing_api_key_degraded(self) -> None:
        """Missing LINEAR_API_KEY produces degraded output, exit 0."""
        with (
            patch.dict("os.environ", {"LINEAR_API_KEY": ""}, clear=False),
            patch(
                "sys.argv",
                [
                    "check_dod_compliance.py",
                    "--contracts-dir",
                    "/nonexistent",
                    "--exemptions",
                    "/nonexistent.yaml",
                ],
            ),
        ):
            exit_code = check_dod_compliance.main()
        assert exit_code == 0

    def test_empty_batch_window(self) -> None:
        """No tickets in lookback produces exit 0, not error."""
        with (
            patch.dict("os.environ", {"LINEAR_API_KEY": "test-key"}, clear=False),
            patch("check_dod_compliance.fetch_completed_tickets", return_value=[]),
            patch(
                "sys.argv",
                [
                    "check_dod_compliance.py",
                    "--contracts-dir",
                    "/nonexistent",
                    "--exemptions",
                    "/nonexistent.yaml",
                    "--since-days",
                    "7",
                ],
            ),
        ):
            exit_code = check_dod_compliance.main()
        assert exit_code == 0

    def test_fail_exit_code(self, tmp_contracts: Path) -> None:
        """Exit code 1 when a ticket fails artifact checks."""
        tickets = [
            {"id": "OMN-3000", "title": "Test", "completed_at": "2026-03-22T00:00:00Z"}
        ]
        exemptions_path = tmp_contracts.parent / "exemptions.yaml"
        exemptions_path.write_text("")
        with (
            patch.dict("os.environ", {"LINEAR_API_KEY": "test-key"}, clear=False),
            patch(
                "check_dod_compliance.fetch_completed_tickets",
                return_value=tickets,
            ),
            patch(
                "sys.argv",
                [
                    "check_dod_compliance.py",
                    "--contracts-dir",
                    str(tmp_contracts),
                    "--exemptions",
                    str(exemptions_path),
                    "--since-days",
                    "7",
                ],
            ),
        ):
            exit_code = check_dod_compliance.main()
        assert exit_code == 1
