# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the SPDX header check and fix module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from onex_change_control.cosmetic.checks.spdx import run_check

if TYPE_CHECKING:
    from pathlib import Path

CORRECT_HEADER = """\
# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""

SPEC = {
    "spdx": {
        "copyright_text": "2025 OmniNode.ai Inc.",
        "license_identifier": "MIT",
        "file_patterns": ["*.py"],
        "exclude_patterns": ["**/migrations/**"],
    },
}


@pytest.mark.unit
class TestSPDXCheck:
    """Check-mode tests."""

    def test_correct_header_passes(self, tmp_path: Path) -> None:
        """File with the correct canonical header produces no violations."""
        f = tmp_path / "good.py"
        f.write_text(CORRECT_HEADER + "x = 1\n")
        violations = run_check(tmp_path, SPEC)
        assert violations == []

    def test_missing_header_detected(self, tmp_path: Path) -> None:
        """File without any SPDX header is flagged."""
        f = tmp_path / "bad.py"
        f.write_text("x = 1\n")
        violations = run_check(tmp_path, SPEC)
        assert len(violations) >= 1
        assert violations[0].check == "spdx"
        assert "bad.py" in violations[0].path

    def test_wrong_copyright_detected(self, tmp_path: Path) -> None:
        """File with a non-canonical copyright holder is flagged."""
        f = tmp_path / "wrong.py"
        f.write_text(
            "# SPDX-FileCopyrightText: 2024 Wrong Corp\n"
            "# SPDX-License-Identifier: MIT\n"
            "\nx = 1\n"
        )
        violations = run_check(tmp_path, SPEC)
        assert len(violations) >= 1
        assert any("2025 OmniNode.ai Inc." in v.message for v in violations)

    def test_spdx_skip_marker_honored(self, tmp_path: Path) -> None:
        """File containing 'spdx-skip' is not checked."""
        f = tmp_path / "skipped.py"
        f.write_text("# spdx-skip\nx = 1\n")
        violations = run_check(tmp_path, SPEC)
        assert violations == []

    def test_exclude_patterns_work(self, tmp_path: Path) -> None:
        """Files inside excluded directories are not checked."""
        mig = tmp_path / "migrations"
        mig.mkdir()
        f = mig / "0001.py"
        f.write_text("x = 1\n")
        violations = run_check(tmp_path, SPEC)
        assert violations == []


@pytest.mark.unit
class TestSPDXFix:
    """Fix-mode tests."""

    def test_fix_stamps_header(self, tmp_path: Path) -> None:
        """Fix mode adds the canonical SPDX header to a bare file."""
        f = tmp_path / "bare.py"
        f.write_text("x = 1\n")
        violations = run_check(tmp_path, SPEC, fix=True)
        assert len(violations) >= 1
        content = f.read_text()
        assert content.startswith("# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.\n")
        assert "# SPDX-License-Identifier: MIT\n" in content
        assert "x = 1\n" in content

    def test_fix_preserves_shebang(self, tmp_path: Path) -> None:
        """Fix mode keeps the shebang line first, header second."""
        f = tmp_path / "script.py"
        f.write_text("#!/usr/bin/env python3\nx = 1\n")
        run_check(tmp_path, SPEC, fix=True)
        content = f.read_text()
        lines = content.splitlines()
        assert lines[0] == "#!/usr/bin/env python3"
        assert lines[1] == "# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc."
        assert lines[2] == "# SPDX-License-Identifier: MIT"

    def test_fix_strips_legacy_duplicates(self, tmp_path: Path) -> None:
        """Fix mode removes old SPDX and legacy Copyright lines."""
        f = tmp_path / "legacy.py"
        f.write_text(
            "# SPDX-FileCopyrightText: 2024 Wrong Corp\n"
            "# SPDX-License-Identifier: Apache-2.0\n"
            "# Copyright (c) 2025 OmniNode Team\n"
            "\nx = 1\n"
        )
        run_check(tmp_path, SPEC, fix=True)
        content = f.read_text()
        assert "Wrong Corp" not in content
        assert "Apache-2.0" not in content
        assert "Copyright (c)" not in content
        assert "# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc." in content
        assert "# SPDX-License-Identifier: MIT" in content

    def test_fix_is_idempotent(self, tmp_path: Path) -> None:
        """Running fix twice produces identical output."""
        f = tmp_path / "idem.py"
        f.write_text("x = 1\n")
        run_check(tmp_path, SPEC, fix=True)
        content_after_first = f.read_text()
        run_check(tmp_path, SPEC, fix=True)
        content_after_second = f.read_text()
        assert content_after_first == content_after_second

    def test_spdx_skip_not_fixed(self, tmp_path: Path) -> None:
        """Files with spdx-skip marker are not modified by fix mode."""
        f = tmp_path / "skipped.py"
        original = "# spdx-skip\nx = 1\n"
        f.write_text(original)
        run_check(tmp_path, SPEC, fix=True)
        assert f.read_text() == original
