"""Tests for the check_schema_purity.py script.

These tests verify that the purity and naming convention enforcement works correctly.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

from scripts.check_schema_purity import check_file


def run_purity_check(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the check_schema_purity.py script with given arguments.

    Args:
        *args: Additional command-line arguments

    Returns:
        CompletedProcess with captured stdout and stderr

    """
    cmd = [sys.executable, "scripts/check_schema_purity.py", *args]
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )


class TestPurityCheckIntegration:
    """Integration tests for the purity check script."""

    def test_existing_schema_files_pass(self) -> None:
        """Test that existing schema files pass all checks."""
        result = run_purity_check()
        assert result.returncode == 0
        assert "passed purity and naming checks" in result.stdout

    def test_reports_file_count(self) -> None:
        """Test that the script reports how many files it checked."""
        result = run_purity_check()
        assert result.returncode == 0
        assert "Checking" in result.stdout
        assert "schema files" in result.stdout


class TestPurityViolationDetection:
    """Tests for detecting purity violations."""

    def test_detects_forbidden_os_import(self, tmp_path: Path) -> None:
        """Test that 'import os' is detected as a violation."""
        # Create a temporary file with forbidden import in models directory
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        test_file = models_dir / "model_test.py"
        test_file.write_text("import os\n")

        violations = check_file(test_file)
        assert len(violations) >= 1
        assert any(v.category == "forbidden_import" for v in violations)
        assert any("os" in v.message for v in violations)

    def test_detects_forbidden_time_import(self, tmp_path: Path) -> None:
        """Test that 'import time' is detected as a violation."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        test_file = models_dir / "model_test.py"
        test_file.write_text("import time\n")

        violations = check_file(test_file)
        assert len(violations) >= 1
        assert any("time" in v.message for v in violations)

    def test_detects_forbidden_requests_import(self, tmp_path: Path) -> None:
        """Test that 'import requests' is detected as a violation."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        test_file = models_dir / "model_test.py"
        test_file.write_text("import requests\n")

        violations = check_file(test_file)
        assert len(violations) >= 1
        assert any("requests" in v.message for v in violations)

    def test_detects_datetime_now_call(self, tmp_path: Path) -> None:
        """Test that datetime.now() is detected as a forbidden call."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        test_file = models_dir / "model_test.py"
        test_file.write_text(
            textwrap.dedent("""
            from datetime import datetime
            x = datetime.now()
            """)
        )

        violations = check_file(test_file)
        assert len(violations) >= 1
        assert any("now" in v.message.lower() for v in violations)

    def test_allows_datetime_fromisoformat(self, tmp_path: Path) -> None:
        """Test that datetime.fromisoformat() is allowed (pure parsing)."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        test_file = models_dir / "model_test.py"
        test_file.write_text(
            textwrap.dedent("""
            from datetime import date
            x = date.fromisoformat("2025-01-01")
            """)
        )

        violations = check_file(test_file)
        # Should have no forbidden call violations
        forbidden_calls = [v for v in violations if v.category == "forbidden_call"]
        assert len(forbidden_calls) == 0

    def test_allows_pydantic_import(self, tmp_path: Path) -> None:
        """Test that pydantic imports are allowed."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        test_file = models_dir / "model_test.py"
        test_file.write_text("from pydantic import BaseModel\n")

        violations = check_file(test_file)
        assert len(violations) == 0

    def test_allows_re_import(self, tmp_path: Path) -> None:
        """Test that re module is allowed (pure regex)."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        test_file = models_dir / "model_test.py"
        test_file.write_text("import re\n")

        violations = check_file(test_file)
        assert len(violations) == 0


class TestNamingConventions:
    """Tests for naming convention enforcement."""

    def test_detects_wrong_model_file_prefix(self, tmp_path: Path) -> None:
        """Test that model files without 'model_' prefix are flagged."""
        # Create a file in a "models" directory with wrong name
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        test_file = models_dir / "wrong_name.py"
        test_file.write_text("class WrongModel: pass\n")

        violations = check_file(test_file)
        assert len(violations) >= 1
        assert any(v.category == "naming_file" for v in violations)
        assert any("model_" in v.message for v in violations)

    def test_detects_wrong_model_class_prefix(self, tmp_path: Path) -> None:
        """Test that model classes without 'Model' prefix are flagged."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        test_file = models_dir / "model_test.py"
        test_file.write_text("class WrongClassName: pass\n")

        violations = check_file(test_file)
        assert len(violations) >= 1
        assert any(v.category == "naming_class" for v in violations)
        assert any("Model" in v.message for v in violations)

    def test_detects_wrong_enum_file_prefix(self, tmp_path: Path) -> None:
        """Test that enum files without 'enum_' prefix are flagged."""
        enums_dir = tmp_path / "enums"
        enums_dir.mkdir()
        test_file = enums_dir / "wrong_name.py"
        test_file.write_text("from enum import Enum\nclass WrongEnum(Enum): pass\n")

        violations = check_file(test_file)
        assert len(violations) >= 1
        assert any(v.category == "naming_file" for v in violations)

    def test_detects_wrong_enum_class_prefix(self, tmp_path: Path) -> None:
        """Test that enum classes without 'Enum' prefix are flagged."""
        enums_dir = tmp_path / "enums"
        enums_dir.mkdir()
        test_file = enums_dir / "enum_test.py"
        test_file.write_text(
            "from enum import Enum\nclass WrongClassName(Enum): pass\n"
        )

        violations = check_file(test_file)
        assert len(violations) >= 1
        assert any(v.category == "naming_class" for v in violations)

    def test_allows_correct_model_naming(self, tmp_path: Path) -> None:
        """Test that correctly named model files/classes pass."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        test_file = models_dir / "model_correct.py"
        test_file.write_text("class ModelCorrect: pass\n")

        violations = check_file(test_file)
        assert len(violations) == 0

    def test_allows_correct_enum_naming(self, tmp_path: Path) -> None:
        """Test that correctly named enum files/classes pass."""
        enums_dir = tmp_path / "enums"
        enums_dir.mkdir()
        test_file = enums_dir / "enum_correct.py"
        test_file.write_text("from enum import Enum\nclass EnumCorrect(Enum): pass\n")

        violations = check_file(test_file)
        assert len(violations) == 0

    def test_skips_init_files(self, tmp_path: Path) -> None:
        """Test that __init__.py files are skipped."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        test_file = models_dir / "__init__.py"
        test_file.write_text("# init file\n")

        violations = check_file(test_file)
        assert len(violations) == 0

    def test_allows_private_classes(self, tmp_path: Path) -> None:
        """Test that private classes (starting with _) are allowed."""
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        test_file = models_dir / "model_test.py"
        test_file.write_text("class _PrivateHelper: pass\nclass ModelPublic: pass\n")

        violations = check_file(test_file)
        # Should not flag the private class
        assert all("_PrivateHelper" not in v.message for v in violations)
