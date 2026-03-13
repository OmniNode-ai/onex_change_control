# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Verify the embedded spec file loads and has expected structure.

These tests are expected to fail with ModuleNotFoundError until the config
loader module (onex_change_control.cosmetic.config) is implemented in Task 2.
They are marked xfail to document the TDD red phase.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest


def _load_spec() -> dict[str, Any]:
    """Import and call load_spec dynamically to avoid mypy import-not-found.

    Uses importlib so the import happens at runtime (not module load time),
    allowing mypy to pass while the config module does not yet exist.
    """
    mod = importlib.import_module("onex_change_control.cosmetic.config")
    return mod.load_spec()  # type: ignore[no-any-return]


@pytest.mark.unit
class TestSpecFile:
    """Tests for the canonical cosmetic spec file."""

    @pytest.mark.xfail(
        raises=ModuleNotFoundError, reason="config module not yet implemented"
    )
    def test_spec_loads(self) -> None:
        """Verify spec loads and contains all required top-level sections."""
        spec = _load_spec()
        assert "spdx" in spec
        assert "pyproject" in spec
        assert "precommit" in spec
        assert "readme" in spec
        assert "github" in spec

    @pytest.mark.xfail(
        raises=ModuleNotFoundError, reason="config module not yet implemented"
    )
    def test_spdx_section(self) -> None:
        """Verify SPDX section has canonical copyright and license values."""
        spec = _load_spec()
        assert spec["spdx"]["copyright_text"] == "2025 OmniNode.ai Inc."
        assert spec["spdx"]["license_identifier"] == "MIT"

    @pytest.mark.xfail(
        raises=ModuleNotFoundError, reason="config module not yet implemented"
    )
    def test_pyproject_author(self) -> None:
        """Verify pyproject section has canonical author name and email."""
        spec = _load_spec()
        assert spec["pyproject"]["author"]["name"] == "OmniNode.ai"
        assert spec["pyproject"]["author"]["email"] == "jonah@omninode.ai"
