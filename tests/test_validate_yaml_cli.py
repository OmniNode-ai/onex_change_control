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

# Path to the CLI script
CLI_SCRIPT = Path(__file__).parent.parent / "scripts" / "validate_yaml.py"

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
    """Run the validate_yaml.py CLI with given arguments.

    Args:
        *args: CLI arguments

    Returns:
        CompletedProcess with stdout, stderr, and returncode

    """
    return subprocess.run(  # noqa: S603
        [sys.executable, str(CLI_SCRIPT), *args],
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
        assert "validate_yaml.py" in result.stdout

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
"""
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
"""
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
summary: "Test ticket"
is_seam_ticket: false
interface_change: false
interfaces_touched: []
evidence_requirements: []
emergency_bypass:
  enabled: false
  justification: ""
  follow_up_ticket_id: ""
"""
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
summary: "Content-detected ticket"
is_seam_ticket: false
interface_change: false
interfaces_touched: []
evidence_requirements: []
emergency_bypass:
  enabled: false
  justification: ""
  follow_up_ticket_id: ""
"""
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
"""
        )

        result = run_cli(str(test_file))
        assert result.returncode == 1
        assert "Cannot determine schema type" in result.stderr
