# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
# ruff: noqa: S607
"""Tests for scripts/sync_version_matrix.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# The script lives outside the installed package tree, so we import it
# by manipulating sys.path.
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import sync_version_matrix  # noqa: E402


def _init_git_repo(repo: Path) -> None:
    """Initialize a git repo with user config for CI compatibility."""
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )


@pytest.mark.unit
class TestGetLatestTag:
    """Tests for get_latest_tag()."""

    def test_returns_latest_semver_tag(self, tmp_path: Path) -> None:
        """A repo with semver tags should return the latest one."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "tag", "v1.0.0"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "second"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "tag", "v1.2.3"],
            check=True,
            capture_output=True,
        )

        result = sync_version_matrix.get_latest_tag(repo)
        assert result == "1.2.3"

    def test_returns_none_for_no_tags(self, tmp_path: Path) -> None:
        """A repo with no tags should return None."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
        )

        result = sync_version_matrix.get_latest_tag(repo)
        assert result is None

    def test_returns_none_for_nonexistent_dir(self, tmp_path: Path) -> None:
        """A nonexistent directory should return None."""
        result = sync_version_matrix.get_latest_tag(tmp_path / "nonexistent")
        assert result is None

    def test_ignores_non_semver_tags(self, tmp_path: Path) -> None:
        """Tags that aren't semver should be skipped."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "tag", "release-candidate-1"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "tag", "v2.0.0"],
            check=True,
            capture_output=True,
        )

        result = sync_version_matrix.get_latest_tag(repo)
        assert result == "2.0.0"


@pytest.mark.unit
class TestMain:
    """Tests for main() entry point."""

    def _write_matrix(self, root: Path, matrix: dict[str, object]) -> Path:
        standards = root / "standards"
        standards.mkdir(parents=True, exist_ok=True)
        path = standards / "version-matrix.yaml"
        with path.open("w") as f:
            yaml.dump(matrix, f, default_flow_style=False)
        return path

    def _make_repo_with_tag(self, root: Path, name: str, tag: str) -> None:
        repo = root / name
        repo.mkdir()
        _init_git_repo(repo)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "tag", tag],
            check=True,
            capture_output=True,
        )

    def test_up_to_date_matrix_returns_zero(self, tmp_path: Path) -> None:
        """When all versions match git tags, exit 0."""
        self._make_repo_with_tag(tmp_path, "omnibase_core", "v1.0.0")
        matrix_root = tmp_path / "change_control"
        matrix_root.mkdir()
        matrix_path = self._write_matrix(
            matrix_root,
            {
                "schema_version": 1,
                "packages": {
                    "omnibase-core": {"version": "1.0.0", "pypi_name": "omnibase-core"},
                },
                "repos": {},
            },
        )

        with patch(
            "sys.argv",
            ["prog", "--root", str(tmp_path), "--matrix", str(matrix_path)],
        ):
            rc = sync_version_matrix.main()

        assert rc == 0

    def test_stale_matrix_returns_one_in_dry_run(self, tmp_path: Path) -> None:
        """When versions are stale and no --write, exit 1."""
        self._make_repo_with_tag(tmp_path, "omnibase_core", "v2.0.0")
        matrix_root = tmp_path / "change_control"
        matrix_root.mkdir()
        matrix_path = self._write_matrix(
            matrix_root,
            {
                "schema_version": 1,
                "packages": {
                    "omnibase-core": {"version": "1.0.0", "pypi_name": "omnibase-core"},
                },
                "repos": {},
            },
        )

        with patch(
            "sys.argv",
            ["prog", "--root", str(tmp_path), "--matrix", str(matrix_path)],
        ):
            rc = sync_version_matrix.main()

        assert rc == 1

    def test_write_mode_updates_file(self, tmp_path: Path) -> None:
        """With --write, the matrix file should be updated."""
        self._make_repo_with_tag(tmp_path, "omnibase_core", "v2.0.0")
        matrix_root = tmp_path / "change_control"
        matrix_root.mkdir()
        matrix_path = self._write_matrix(
            matrix_root,
            {
                "schema_version": 1,
                "packages": {
                    "omnibase-core": {"version": "1.0.0", "pypi_name": "omnibase-core"},
                },
                "repos": {
                    "omniclaude": {
                        "expected_pins": {"omnibase-core": "1.0.0"},
                    },
                },
            },
        )

        with patch(
            "sys.argv",
            [
                "prog",
                "--root",
                str(tmp_path),
                "--matrix",
                str(matrix_path),
                "--write",
            ],
        ):
            rc = sync_version_matrix.main()

        assert rc == 0

        # Verify the file was updated
        with matrix_path.open() as f:
            updated = yaml.safe_load(f)
        assert updated["packages"]["omnibase-core"]["version"] == "2.0.0"
        assert (
            updated["repos"]["omniclaude"]["expected_pins"]["omnibase-core"] == "2.0.0"
        )

    def test_missing_root_returns_two(self, tmp_path: Path) -> None:
        """Non-existent root should exit 2."""
        with patch(
            "sys.argv",
            [
                "prog",
                "--root",
                str(tmp_path / "nonexistent"),
                "--matrix",
                str(tmp_path / "m.yaml"),
            ],
        ):
            rc = sync_version_matrix.main()

        assert rc == 2

    def test_missing_matrix_returns_two(self, tmp_path: Path) -> None:
        """Non-existent matrix file should exit 2."""
        with patch(
            "sys.argv",
            [
                "prog",
                "--root",
                str(tmp_path),
                "--matrix",
                str(tmp_path / "nonexistent.yaml"),
            ],
        ):
            rc = sync_version_matrix.main()

        assert rc == 2
