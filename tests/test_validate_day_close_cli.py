# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for validate-day-close CLI entry point."""

from __future__ import annotations

import pytest
import yaml

from onex_change_control.scripts.validate_day_close import main


@pytest.mark.unit
class TestValidateDayCloseCLI:
    """Tests for the validate-day-close CLI."""

    def test_valid_yaml_returns_zero(self, tmp_path: object) -> None:
        """Valid day-close YAML should return exit code 0."""
        from pathlib import Path

        tmp = Path(str(tmp_path))
        valid_data = {
            "schema_version": "1.0.0",
            "date": "2026-03-19",
            "process_changes_today": [],
            "plan": [],
            "actual_by_repo": [],
            "drift_detected": [],
            "invariants_checked": {
                "reducers_pure": "pass",
                "orchestrators_no_io": "pass",
                "effects_do_io_only": "unknown",
                "real_infra_proof_progressing": "unknown",
                "integration_sweep": "unknown",
            },
            "corrections_for_tomorrow": [],
            "risks": [],
        }
        yaml_file = tmp / "test.yaml"
        yaml_file.write_text(yaml.dump(valid_data), encoding="utf-8")

        result = main([str(yaml_file)])
        assert result == 0

    def test_invalid_yaml_returns_one(self, tmp_path: object) -> None:
        """Invalid day-close YAML should return exit code 1."""
        from pathlib import Path

        tmp = Path(str(tmp_path))
        invalid_data = {"schema_version": "not-semver", "date": "bad-date"}
        yaml_file = tmp / "test.yaml"
        yaml_file.write_text(yaml.dump(invalid_data), encoding="utf-8")

        result = main([str(yaml_file)])
        assert result == 1

    def test_missing_file_returns_one(self) -> None:
        """Missing file should return exit code 1."""
        result = main(["/nonexistent/path/test.yaml"])
        assert result == 1

    def test_quiet_mode_suppresses_output(
        self, tmp_path: object, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Quiet mode should suppress OK messages."""
        from pathlib import Path

        tmp = Path(str(tmp_path))
        valid_data = {
            "schema_version": "1.0.0",
            "date": "2026-03-19",
            "invariants_checked": {
                "reducers_pure": "pass",
                "orchestrators_no_io": "pass",
                "effects_do_io_only": "unknown",
                "real_infra_proof_progressing": "unknown",
                "integration_sweep": "unknown",
            },
        }
        yaml_file = tmp / "test.yaml"
        yaml_file.write_text(yaml.dump(valid_data), encoding="utf-8")

        result = main(["--quiet", str(yaml_file)])
        assert result == 0
        captured = capsys.readouterr()
        assert "OK" not in captured.out
