# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for EnumIntegrationSurface."""

import pytest

from onex_change_control.enums.enum_integration_surface import EnumIntegrationSurface


@pytest.mark.unit
class TestEnumIntegrationSurface:
    """Tests for EnumIntegrationSurface enum."""

    def test_all_values_defined(self) -> None:
        """All expected values are present."""
        assert EnumIntegrationSurface.KAFKA is not None
        assert EnumIntegrationSurface.DB is not None
        assert EnumIntegrationSurface.CI is not None
        assert EnumIntegrationSurface.PLUGIN is not None
        assert EnumIntegrationSurface.GITHUB_CI is not None
        assert EnumIntegrationSurface.SCRIPT is not None

    def test_value_count(self) -> None:
        """Exactly six members defined."""
        assert len(EnumIntegrationSurface) == 6

    def test_str_returns_value(self) -> None:
        """__str__ returns the string value."""
        assert str(EnumIntegrationSurface.KAFKA) == "kafka"
        assert str(EnumIntegrationSurface.DB) == "db"
        assert str(EnumIntegrationSurface.CI) == "ci"
        assert str(EnumIntegrationSurface.PLUGIN) == "plugin"
        assert str(EnumIntegrationSurface.GITHUB_CI) == "github_ci"
        assert str(EnumIntegrationSurface.SCRIPT) == "script"

    def test_is_str_subclass(self) -> None:
        """EnumIntegrationSurface members are str instances."""
        for member in EnumIntegrationSurface:
            assert isinstance(member, str)

    def test_roundtrip_from_value(self) -> None:
        """Can construct members from their string values."""
        assert EnumIntegrationSurface("kafka") is EnumIntegrationSurface.KAFKA
        assert EnumIntegrationSurface("db") is EnumIntegrationSurface.DB
        assert EnumIntegrationSurface("ci") is EnumIntegrationSurface.CI
        assert EnumIntegrationSurface("plugin") is EnumIntegrationSurface.PLUGIN
        assert EnumIntegrationSurface("github_ci") is EnumIntegrationSurface.GITHUB_CI
        assert EnumIntegrationSurface("script") is EnumIntegrationSurface.SCRIPT

    def test_importable_from_enums_package(self) -> None:
        """EnumIntegrationSurface is accessible via the enums package __init__."""
        from onex_change_control.enums import EnumIntegrationSurface as ImportedEnum

        assert ImportedEnum is EnumIntegrationSurface
