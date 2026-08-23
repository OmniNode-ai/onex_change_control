# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the validate_yaml.py CLI script.

Tests cover:
- Valid file validation
- Invalid file detection
- Schema type detection (path-based and content-based)
- Error message formatting
- CLI argument handling
"""

import subprocess
import sys
from pathlib import Path

import pytest

# Use the CLI entrypoint instead of direct script path
CLI_ENTRYPOINT = "validate-yaml"

# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent.parent / "drift" / "day_close"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# Exit codes
EXIT_SUCCESS = 0
EXIT_VALIDATION_FAILURE = 1
EXIT_USAGE_ERROR = 2

# Minimum files required for multi-file tests
MIN_FILES_FOR_MULTI_TEST = 2


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the validate-yaml CLI with given arguments.

    Args:
        *args: CLI arguments

    Returns:
        CompletedProcess with stdout, stderr, and returncode

    """
    # Use the module path directly since it's installed as a package
    return subprocess.run(
        [sys.executable, "-m", "onex_change_control.scripts.validate_yaml", *args],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )


class TestCliHelp:
    """Tests for CLI help and version flags."""

    def test_help_flag_shows_usage(self) -> None:
        """Test that --help shows usage information."""
        result = run_cli("--help")
        assert result.returncode == 0
        assert "Usage:" in result.stdout
        assert (
            "validate-yaml" in result.stdout or "Validate YAML files" in result.stdout
        )

    def test_h_flag_shows_usage(self) -> None:
        """Test that -h shows usage information."""
        result = run_cli("-h")
        assert result.returncode == 0
        assert "Usage:" in result.stdout

    def test_version_flag(self) -> None:
        """Test that --version shows version."""
        result = run_cli("--version")
        assert result.returncode == 0
        assert "validate_yaml.py v" in result.stdout

    def test_no_args_shows_usage_and_error_code(self) -> None:
        """Test that no arguments shows usage and exits with error code."""
        result = run_cli()
        assert result.returncode == EXIT_USAGE_ERROR
        assert "Usage:" in result.stdout


class TestValidFileValidation:
    """Tests for validating valid YAML files."""

    def test_valid_day_close_file(self) -> None:
        """Test validation of a valid day_close file."""
        # Find any day_close file in drift/day_close/
        day_close_files = list(FIXTURES_DIR.glob("*.yaml"))
        if not day_close_files:
            pytest.skip("No day_close files found")

        result = run_cli(str(day_close_files[0]))
        assert result.returncode == 0
        assert "[OK]" in result.stdout
        assert "day_close" in result.stdout

    def test_multiple_valid_files(self) -> None:
        """Test validation of multiple valid files."""
        day_close_files = list(FIXTURES_DIR.glob("*.yaml"))
        if len(day_close_files) < MIN_FILES_FOR_MULTI_TEST:
            pytest.skip("Need at least 2 day_close files")

        result = run_cli(str(day_close_files[0]), str(day_close_files[1]))
        assert result.returncode == 0
        assert "All 2 file(s) valid" in result.stdout


class TestInvalidFileDetection:
    """Tests for detecting invalid YAML files."""

    def test_file_not_found(self, tmp_path: Path) -> None:
        """Test error when file does not exist."""
        nonexistent = tmp_path / "nonexistent.yaml"
        result = run_cli(str(nonexistent))
        assert result.returncode == 1
        assert "File not found" in result.stderr

    def test_empty_file(self, tmp_path: Path) -> None:
        """Test error for empty YAML file."""
        empty_file = tmp_path / "empty_day_close.yaml"
        empty_file.write_text("")

        result = run_cli(str(empty_file))
        assert result.returncode == 1
        assert "Empty file" in result.stderr

    def test_invalid_yaml_syntax(self, tmp_path: Path) -> None:
        """Test error for invalid YAML syntax."""
        invalid_yaml = tmp_path / "invalid_day_close.yaml"
        invalid_yaml.write_text("{ invalid yaml [")

        result = run_cli(str(invalid_yaml))
        assert result.returncode == 1
        assert "YAML parse error" in result.stderr

    def test_validation_error_shows_path_and_reason(self, tmp_path: Path) -> None:
        """Test that validation errors show field path and reason."""
        invalid_file = tmp_path / "invalid_day_close.yaml"
        # Uses correct ModelDayClose schema structure but with invalid date
        invalid_file.write_text(
            """
schema_version: "1.0.0"
date: "not-a-valid-date"
process_changes_today: []
plan: []
actual_by_repo: []
drift_detected: []
invariants_checked:
  reducers_pure: "pass"
  orchestrators_no_io: "pass"
  effects_do_io_only: "pass"
  real_infra_proof_progressing: "unknown"
corrections_for_tomorrow: []
risks: []
""",
        )

        result = run_cli(str(invalid_file))
        assert result.returncode == 1
        assert "date:" in result.stderr  # Field path
        assert "Invalid date format" in result.stderr  # Reason


class TestSchemaTypeDetection:
    """Tests for schema type detection logic."""

    def test_path_based_detection_day_close(self, tmp_path: Path) -> None:
        """Test that 'day_close' in path triggers day_close schema."""
        # Create a minimal valid day_close file
        day_close_dir = tmp_path / "day_close"
        day_close_dir.mkdir()
        test_file = day_close_dir / "2025-01-01.yaml"
        test_file.write_text(
            """
schema_version: "1.0.0"
date: "2025-01-01"
plan_summary: "Test day"
process_changes_today: []
plan: []
actual_by_repo: []
drift_detected: []
invariants_checked:
  reducers_pure: "pass"
  orchestrators_no_io: "pass"
  effects_do_io_only: "pass"
  real_infra_proof_progressing: "unknown"
corrections_for_tomorrow: []
risks: []
""",
        )

        result = run_cli(str(test_file))
        assert result.returncode == 0
        assert "day_close" in result.stdout

    def test_path_based_detection_contract(self, tmp_path: Path) -> None:
        """Test that 'contract' in path triggers ticket_contract schema."""
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        test_file = contracts_dir / "OMN-123.yaml"
        test_file.write_text(
            """
schema_version: "1.0.0"
ticket_id: "OMN-123"
title: "Test ticket"
summary: "Test ticket"
is_seam_ticket: false
interface_change: false
interfaces_touched: []
evidence_requirements: []
emergency_bypass:
  enabled: false
  justification: ""
  follow_up_ticket_id: ""
""",
        )

        result = run_cli(str(test_file))
        assert result.returncode == 0
        assert "ticket_contract" in result.stdout

    def test_content_based_detection_unknown_path(self, tmp_path: Path) -> None:
        """Test content-based detection when path is ambiguous."""
        test_file = tmp_path / "random_file.yaml"
        test_file.write_text(
            """
schema_version: "1.0.0"
ticket_id: "OMN-456"
title: "Content-detected ticket"
summary: "Content-detected ticket"
is_seam_ticket: false
interface_change: false
interfaces_touched: []
evidence_requirements: []
emergency_bypass:
  enabled: false
  justification: ""
  follow_up_ticket_id: ""
""",
        )

        result = run_cli(str(test_file))
        assert result.returncode == 0
        assert "ticket_contract" in result.stdout

    def test_undetectable_schema_type(self, tmp_path: Path) -> None:
        """Test error when schema type cannot be determined."""
        test_file = tmp_path / "ambiguous.yaml"
        test_file.write_text(
            """
foo: bar
baz: 123
""",
        )

        result = run_cli(str(test_file))
        assert result.returncode == 1
        assert "Cannot determine schema type" in result.stderr

    def test_contracts_v1_namespace_is_not_legacy_ticket_contract(
        self, tmp_path: Path
    ) -> None:
        """OMN-16161: contracts/v1/*.yaml is the OMN-15669 occ-contract/v1 shape,
        not the legacy ModelTicketContract shape. A file that would fail
        ModelTicketContract (non-SemVer schema_version, extra fields like
        `interface`/`cases`) must still validate cleanly through this CLI when
        it lives under a `contracts/v1/` directory, because that namespace has
        its own dedicated gate (check-contract-shape-v1 / OMN-15669) and must
        not be fed to the legacy validator.
        """
        v1_dir = tmp_path / "contracts" / "v1"
        v1_dir.mkdir(parents=True)
        test_file = v1_dir / "OMN-15669.yaml"
        test_file.write_text(
            """
schema_version: occ-contract/v1
ticket_id: OMN-15669
title: Canonical contract shape v1
interface:
  inputs: []
  outputs: []
dependencies: []
cases: []
exclusions: []
""",
        )

        result = run_cli(str(test_file))
        assert result.returncode == 0, result.stderr
        assert "ticket_contract" not in result.stdout

    def test_top_level_contracts_dir_still_ticket_contract(
        self, tmp_path: Path
    ) -> None:
        """Sibling to the v1-namespace test: a top-level contracts/*.yaml file
        (no /v1/ path segment) must still classify and validate as the legacy
        ticket_contract shape — the v1-namespace carve-out must not swallow
        the normal case.
        """
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        test_file = contracts_dir / "OMN-999.yaml"
        test_file.write_text(
            """
schema_version: "1.0.0"
ticket_id: "OMN-999"
title: "Test ticket"
summary: "Test ticket"
is_seam_ticket: false
interface_change: false
interfaces_touched: []
evidence_requirements: []
emergency_bypass:
  enabled: false
  justification: ""
  follow_up_ticket_id: ""
""",
        )

        result = run_cli(str(test_file))
        assert result.returncode == 0, result.stderr
        assert "ticket_contract" in result.stdout


class TestMalformedContractRegression:
    """Regression tests for OMN-8808: malformed DoD evidence YAML must be blocked.

    Motivated by OMN-8606 where a malformed contract shipped to main undetected.
    """

    def test_malformed_contract_exits_nonzero(self, tmp_path: Path) -> None:
        """Synthetic malformed contract must cause validate-yaml to exit non-zero."""
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        bad_contract = contracts_dir / "OMN-9999.yaml"
        # Missing required fields: summary, is_seam_ticket, interface_change, etc.
        bad_contract.write_text(
            """
ticket_id: OMN-9999
schema_version: "1.0"
dod_evidence:
  - check_type: INVALID_ENUM_VALUE_THAT_DOES_NOT_EXIST
""",
        )

        result = run_cli(str(bad_contract))
        assert result.returncode != 0, (
            "validate-yaml must exit non-zero for malformed contract (OMN-8808 gate)"
        )

    def test_malformed_contract_reports_validation_error(self, tmp_path: Path) -> None:
        """Malformed contract must produce a ValidationError message on stderr."""
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        bad_contract = contracts_dir / "OMN-9998.yaml"
        bad_contract.write_text(
            """
ticket_id: OMN-9998
schema_version: "1.0"
required_field_missing: true
""",
        )

        result = run_cli(str(bad_contract))
        assert result.returncode != 0
        assert result.stderr, "stderr must contain validation error details"


class TestV1ContractSkip:
    """Regression tests for OMN-15669: `contracts/v1/` files must not be validated
    against the generic ModelTicketContract wrapper.

    ModelTicketContract is extra="forbid" and enforces schema_version as strict
    SemVer, so a genuinely conformant contract-shape-v1 document (interface/
    dependencies/cases/exclusions blocks, schema_version="occ-contract/v1") is
    unrepresentable there by design — see contract_shape_v1.py's V1_DIR comment.
    Motivated by the live OMN-15669.yaml corpus-validation failure surfaced on
    omnibase_spi#270: a fully conformant v1 contract (confirmed green under
    `check-contract-shape-v1`) was rejected corpus-wide by `validate-yaml`.
    """

    def test_v1_contract_is_skipped_not_rejected(self, tmp_path: Path) -> None:
        """A conformant contract-shape-v1 document under contracts/v1/ is skipped,
        not validated (and rejected) against ModelTicketContract.
        """
        v1_dir = tmp_path / "contracts" / "v1"
        v1_dir.mkdir(parents=True)
        v1_contract = v1_dir / "OMN-99999.yaml"
        # Fields (`interface`, `dependencies`, `cases`, `exclusions`,
        # schema_version="occ-contract/v1") that ModelTicketContract forbids.
        v1_contract.write_text(
            """
schema_version: occ-contract/v1
ticket_id: OMN-99999
title: Synthetic v1 contract for the skip-routing regression test
interface:
  inputs: []
  outputs: []
cases:
  - id: noop
    class: unit
    behavior: placeholder
""",
        )

        result = run_cli(str(v1_contract))
        assert result.returncode == 0, (
            "validate-yaml must not fail a contracts/v1/ file against "
            f"ModelTicketContract. stderr: {result.stderr}"
        )
        assert "skipped" in result.stdout
        assert "extra_forbidden" not in result.stderr
        assert "invalid SemVer" not in result.stderr

    def test_non_v1_contract_still_validated(self, tmp_path: Path) -> None:
        """A top-level contracts/ file (not under v1/) is still validated
        normally against ModelTicketContract — the skip is v1-scoped only.
        """
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        bad_contract = contracts_dir / "OMN-99998.yaml"
        bad_contract.write_text(
            """
ticket_id: OMN-99998
schema_version: "1.0"
required_field_missing: true
""",
        )

        result = run_cli(str(bad_contract))
        assert result.returncode != 0, (
            "the v1 skip must not swallow validation for non-v1 contracts"
        )
        assert "skipped" not in result.stdout
