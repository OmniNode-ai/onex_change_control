# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the cosmetic-lint CLI skeleton."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from onex_change_control.cosmetic.cli import Violation, main

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
class TestViolation:
    """Verify the Violation dataclass and format() method."""

    def test_format_fixable(self) -> None:
        """Fixable violation includes [fixable] tag."""
        v = Violation(
            check="spdx",
            path="src/foo.py",
            line=1,
            message="Missing SPDX header",
            fixable=True,
        )
        assert v.format() == "src/foo.py:1: [spdx] Missing SPDX header [fixable]"

    def test_format_not_fixable(self) -> None:
        """Non-fixable violation omits [fixable] tag."""
        v = Violation(
            check="github",
            path=".github/PULL_REQUEST_TEMPLATE.md",
            line=0,
            message="Missing PR template",
            fixable=False,
        )
        assert (
            v.format()
            == ".github/PULL_REQUEST_TEMPLATE.md:0: [github] Missing PR template"
        )

    def test_frozen(self) -> None:
        """Violation is immutable (frozen dataclass)."""
        v = Violation(check="spdx", path="a.py", line=1, message="msg", fixable=True)
        with pytest.raises(AttributeError):
            v.check = "other"  # type: ignore[misc]


@pytest.mark.unit
class TestCLI:
    """Verify the CLI skeleton dispatches correctly."""

    def test_check_no_violations_exits_zero(self, tmp_path: Path) -> None:
        """Check on clean directory exits 0."""
        with pytest.raises(SystemExit) as exc_info:
            main(["check", str(tmp_path)])
        assert exc_info.value.code == 0

    def test_fix_no_violations_exits_zero(self, tmp_path: Path) -> None:
        """Fix on clean directory exits 0."""
        with pytest.raises(SystemExit) as exc_info:
            main(["fix", str(tmp_path)])
        assert exc_info.value.code == 0

    def test_select_flag_accepted(self, tmp_path: Path) -> None:
        """Single --select value is accepted."""
        with pytest.raises(SystemExit) as exc_info:
            main(["check", "--select", "spdx", str(tmp_path)])
        assert exc_info.value.code == 0

    def test_multiple_select_flags(self, tmp_path: Path) -> None:
        """Comma-separated --select values are accepted."""
        with pytest.raises(SystemExit) as exc_info:
            main(["check", "--select", "spdx,pyproject", str(tmp_path)])
        assert exc_info.value.code == 0

    def test_unknown_select_exits_with_error(self, tmp_path: Path) -> None:
        """Unknown check name in --select causes non-zero exit."""
        with pytest.raises(SystemExit) as exc_info:
            main(["check", "--select", "nonexistent", str(tmp_path)])
        assert exc_info.value.code is not None
        assert exc_info.value.code != 0

    def test_spec_flag_accepted(self, tmp_path: Path) -> None:
        """Custom --spec path is accepted."""
        spec_file = tmp_path / "custom.yaml"
        spec_file.write_text(
            "spdx: {}\npyproject: {}\nprecommit: {}\nreadme: {}\ngithub: {}\n"
        )
        with pytest.raises(SystemExit) as exc_info:
            main(["check", "--spec", str(spec_file), str(tmp_path)])
        assert exc_info.value.code == 0

    def test_verbose_flag_accepted(self, tmp_path: Path) -> None:
        """Verbose -v flag is accepted."""
        with pytest.raises(SystemExit) as exc_info:
            main(["-v", "check", str(tmp_path)])
        assert exc_info.value.code == 0

    def test_no_command_exits_zero(self) -> None:
        """No subcommand prints help and exits 0."""
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 0

    def test_check_detects_spdx_violation(self, tmp_path: Path) -> None:
        """Check detects SPDX violations and exits 1."""
        (tmp_path / "bad.py").write_text("x = 1\n")
        with pytest.raises(SystemExit) as exc_info:
            main(["check", str(tmp_path)])
        assert exc_info.value.code == 1
