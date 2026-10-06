# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Verify the embedded spec file loads and has expected structure."""

from __future__ import annotations

import pytest

from onex_change_control.cosmetic.config import load_spec


@pytest.mark.unit
class TestSpecFile:
    """Tests for the canonical cosmetic spec file."""

    def test_spec_loads(self) -> None:
        """Verify spec loads and contains all required top-level sections."""
        spec = load_spec()
        assert "spdx" in spec
        assert "pyproject" in spec
        assert "precommit" in spec
        assert "readme" in spec
        assert "github" in spec

    def test_spdx_section(self) -> None:
        """Verify SPDX section has canonical copyright and license values."""
        spec = load_spec()
        assert spec["spdx"]["copyright_text"] == "2025 OmniNode.ai Inc."
        assert spec["spdx"]["license_identifier"] == "MIT"

    def test_pyproject_author(self) -> None:
        """Verify pyproject section has canonical author name and email."""
        spec = load_spec()
        assert spec["pyproject"]["author"]["name"] == "OmniNode.ai"
        assert spec["pyproject"]["author"]["email"] == "jonah@omninode.ai"
