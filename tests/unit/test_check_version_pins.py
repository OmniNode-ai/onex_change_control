# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for standards/check_version_pins.py."""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

# The script lives outside the installed package tree, so we import it
# by manipulating sys.path.
_STANDARDS_DIR = Path(__file__).resolve().parents[2] / "standards"
sys.path.insert(0, str(_STANDARDS_DIR))

import check_version_pins  # noqa: E402

# ---------------------------------------------------------------------------
# extract_pins_from_pyproject
# ---------------------------------------------------------------------------


class TestExtractPinsFromPyproject:
    """Tests for extract_pins_from_pyproject()."""

    def test_exact_pin(self, tmp_path: Path) -> None:
        pp = tmp_path / "pyproject.toml"
        pp.write_text('"omnibase-core==0.27.1"\n')
        result = check_version_pins.extract_pins_from_pyproject(pp)
        assert result == {"omnibase-core": ("==", "0.27.1")}

    def test_gte_pin(self, tmp_path: Path) -> None:
        pp = tmp_path / "pyproject.toml"
        pp.write_text('"omnibase-core>=0.27.1"\n')
        result = check_version_pins.extract_pins_from_pyproject(pp)
        assert result == {"omnibase-core": (">=", "0.27.1")}

    def test_compatible_pin(self, tmp_path: Path) -> None:
        pp = tmp_path / "pyproject.toml"
        pp.write_text('"omnibase-core~=0.27.1"\n')
        result = check_version_pins.extract_pins_from_pyproject(pp)
        assert result == {"omnibase-core": ("~=", "0.27.1")}

    def test_ignores_non_onex_packages(self, tmp_path: Path) -> None:
        pp = tmp_path / "pyproject.toml"
        pp.write_text('"requests==2.31.0"\n"omnibase-spi==0.17.0"\n')
        result = check_version_pins.extract_pins_from_pyproject(pp)
        assert "requests" not in result
        assert "omnibase-spi" in result

    def test_multiple_pins(self, tmp_path: Path) -> None:
        pp = tmp_path / "pyproject.toml"
        pp.write_text('"omnibase-core>=0.27.1"\n"omnibase-spi==0.17.0"\n')
        result = check_version_pins.extract_pins_from_pyproject(pp)
        assert result == {
            "omnibase-core": (">=", "0.27.1"),
            "omnibase-spi": ("==", "0.17.0"),
        }


# ---------------------------------------------------------------------------
# check_pins
# ---------------------------------------------------------------------------


class TestCheckPins:
    """Tests for check_pins()."""

    def test_exact_pin_at_expected(self) -> None:
        actual = {"omnibase-core": ("==", "0.27.1")}
        expected = {"omnibase-core": "0.27.1"}
        errors = check_version_pins.check_pins(actual, expected, "test")
        assert errors == []

    def test_exact_pin_above_expected(self) -> None:
        actual = {"omnibase-core": ("==", "0.28.0")}
        expected = {"omnibase-core": "0.27.1"}
        errors = check_version_pins.check_pins(actual, expected, "test")
        assert errors == []

    def test_exact_pin_below_expected(self) -> None:
        actual = {"omnibase-core": ("==", "0.26.0")}
        expected = {"omnibase-core": "0.27.1"}
        errors = check_version_pins.check_pins(actual, expected, "test")
        assert len(errors) == 1
        assert "omnibase-core" in errors[0]
        assert "0.26.0" in errors[0]

    def test_gte_pin_at_expected(self) -> None:
        actual = {"omnibase-core": (">=", "0.27.1")}
        expected = {"omnibase-core": "0.27.1"}
        errors = check_version_pins.check_pins(actual, expected, "test")
        assert errors == []

    def test_gte_pin_above_expected(self) -> None:
        actual = {"omnibase-core": (">=", "0.28.0")}
        expected = {"omnibase-core": "0.27.1"}
        errors = check_version_pins.check_pins(actual, expected, "test")
        assert errors == []

    def test_gte_pin_below_expected(self) -> None:
        actual = {"omnibase-core": (">=", "0.25.0")}
        expected = {"omnibase-core": "0.27.1"}
        errors = check_version_pins.check_pins(actual, expected, "test")
        assert len(errors) == 1
        assert ">=0.25.0" in errors[0]

    def test_compatible_pin_at_expected(self) -> None:
        actual = {"omnibase-core": ("~=", "0.27.1")}
        expected = {"omnibase-core": "0.27.1"}
        errors = check_version_pins.check_pins(actual, expected, "test")
        assert errors == []

    def test_compatible_pin_above_expected(self) -> None:
        actual = {"omnibase-core": ("~=", "0.28.0")}
        expected = {"omnibase-core": "0.27.1"}
        errors = check_version_pins.check_pins(actual, expected, "test")
        assert errors == []

    def test_package_not_found(self) -> None:
        actual: dict[str, tuple[str, str]] = {}
        expected = {"omnibase-core": "0.27.1"}
        errors = check_version_pins.check_pins(actual, expected, "test")
        assert len(errors) == 1
        assert "not found" in errors[0]

    def test_empty_expected_pins(self) -> None:
        actual = {"omnibase-core": ("==", "0.27.1")}
        expected: dict[str, str] = {}
        errors = check_version_pins.check_pins(actual, expected, "test")
        assert errors == []


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------


class TestMain:
    """Integration tests for main() via monkeypatch."""

    def _setup_matrix(self, root: Path, matrix_content: str) -> None:
        standards_dir = root / "standards"
        standards_dir.mkdir(parents=True, exist_ok=True)
        (standards_dir / "version-matrix.yaml").write_text(matrix_content)

    def test_repo_not_in_matrix_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Repo not in matrix should exit 0 with INFO message."""
        self._setup_matrix(
            tmp_path,
            dedent("""\
                schema_version: 1
                repos:
                  some_other_repo:
                    expected_pins: {}
            """),
        )
        # Need a dummy pyproject.toml so it doesn't blow up
        # (but we shouldn't even get there)
        monkeypatch.setattr(
            sys,
            "argv",
            ["prog", "--repo", "nonexistent_repo", "--root", str(tmp_path)],
        )
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        rc = check_version_pins.main()

        assert rc == 0
        assert "INFO" in captured.getvalue()
        assert "not managed" in captured.getvalue()

    def test_empty_expected_pins_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Repo with empty expected_pins should pass with special message."""
        self._setup_matrix(
            tmp_path,
            dedent("""\
                schema_version: 1
                repos:
                  omnimemory:
                    pyproject_path: pyproject.toml
                    expected_pins: {}
            """),
        )
        repo_dir = tmp_path / "omnimemory"
        repo_dir.mkdir()
        (repo_dir / "pyproject.toml").write_text(
            '[project]\nname = "omninode-memory"\n'
        )

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "prog",
                "--repo",
                "omnimemory",
                "--root",
                str(tmp_path),
                "--repo-path",
                str(repo_dir),
            ],
        )
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        rc = check_version_pins.main()

        assert rc == 0
        assert "no expected pins defined" in captured.getvalue()

    def test_all_pins_match(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All pins at expected version should pass."""
        self._setup_matrix(
            tmp_path,
            dedent("""\
                schema_version: 1
                repos:
                  myrepo:
                    pyproject_path: pyproject.toml
                    expected_pins:
                      omnibase-core: "0.27.1"
            """),
        )
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()
        (repo_dir / "pyproject.toml").write_text('"omnibase-core==0.27.1"\n')

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "prog",
                "--repo",
                "myrepo",
                "--root",
                str(tmp_path),
                "--repo-path",
                str(repo_dir),
            ],
        )
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        rc = check_version_pins.main()

        assert rc == 0
        assert "PASS" in captured.getvalue()

    def test_gte_pin_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A >= pin with floor at expected version should pass."""
        self._setup_matrix(
            tmp_path,
            dedent("""\
                schema_version: 1
                repos:
                  myrepo:
                    pyproject_path: pyproject.toml
                    expected_pins:
                      omnibase-core: "0.27.1"
            """),
        )
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()
        (repo_dir / "pyproject.toml").write_text('"omnibase-core>=0.27.1"\n')

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "prog",
                "--repo",
                "myrepo",
                "--root",
                str(tmp_path),
                "--repo-path",
                str(repo_dir),
            ],
        )
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        rc = check_version_pins.main()

        assert rc == 0
        assert "PASS" in captured.getvalue()

    def test_pin_drift_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pin below expected version should fail."""
        self._setup_matrix(
            tmp_path,
            dedent("""\
                schema_version: 1
                repos:
                  myrepo:
                    pyproject_path: pyproject.toml
                    expected_pins:
                      omnibase-core: "0.27.1"
            """),
        )
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()
        (repo_dir / "pyproject.toml").write_text('"omnibase-core==0.25.0"\n')

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "prog",
                "--repo",
                "myrepo",
                "--root",
                str(tmp_path),
                "--repo-path",
                str(repo_dir),
            ],
        )
        captured = StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        rc = check_version_pins.main()

        assert rc == 1
        assert "FAIL" in captured.getvalue()
        assert "pin drift" in captured.getvalue()
