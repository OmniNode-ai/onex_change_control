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
        assert EnumIntegrationSurface.CONTAINER_HEALTH is not None
        assert EnumIntegrationSurface.RUNTIME_HEALTH is not None

    def test_value_count(self) -> None:
        """Exactly eight members defined."""
        assert len(EnumIntegrationSurface) == 8

    def test_container_health_exists(self) -> None:
        """CONTAINER_HEALTH member has correct value."""
        assert EnumIntegrationSurface.CONTAINER_HEALTH.value == "container_health"

    def test_runtime_health_exists(self) -> None:
        """RUNTIME_HEALTH member has correct value."""
        assert EnumIntegrationSurface.RUNTIME_HEALTH.value == "runtime_health"

    def test_str_returns_value(self) -> None:
        """__str__ returns the string value."""
        assert str(EnumIntegrationSurface.KAFKA) == "kafka"
        assert str(EnumIntegrationSurface.DB) == "db"
        assert str(EnumIntegrationSurface.CI) == "ci"
        assert str(EnumIntegrationSurface.PLUGIN) == "plugin"
        assert str(EnumIntegrationSurface.GITHUB_CI) == "github_ci"
        assert str(EnumIntegrationSurface.SCRIPT) == "script"
        assert str(EnumIntegrationSurface.CONTAINER_HEALTH) == "container_health"
        assert str(EnumIntegrationSurface.RUNTIME_HEALTH) == "runtime_health"

    def test_is_str_subclass(self) -> None:
        """EnumIntegrationSurface members are str instances."""
        assert isinstance(EnumIntegrationSurface.KAFKA, str)
        assert isinstance(EnumIntegrationSurface.DB, str)
        assert isinstance(EnumIntegrationSurface.CI, str)
        assert isinstance(EnumIntegrationSurface.PLUGIN, str)
        assert isinstance(EnumIntegrationSurface.GITHUB_CI, str)
        assert isinstance(EnumIntegrationSurface.SCRIPT, str)
        assert isinstance(EnumIntegrationSurface.CONTAINER_HEALTH, str)
        assert isinstance(EnumIntegrationSurface.RUNTIME_HEALTH, str)

    def test_roundtrip_from_value(self) -> None:
        """Can construct members from their string values."""
        assert EnumIntegrationSurface("kafka") is EnumIntegrationSurface.KAFKA
        assert EnumIntegrationSurface("db") is EnumIntegrationSurface.DB
        assert EnumIntegrationSurface("ci") is EnumIntegrationSurface.CI
        assert EnumIntegrationSurface("plugin") is EnumIntegrationSurface.PLUGIN
        assert EnumIntegrationSurface("github_ci") is EnumIntegrationSurface.GITHUB_CI
        assert EnumIntegrationSurface("script") is EnumIntegrationSurface.SCRIPT
        assert (
            EnumIntegrationSurface("container_health")
            is EnumIntegrationSurface.CONTAINER_HEALTH
        )
        assert (
            EnumIntegrationSurface("runtime_health")
            is EnumIntegrationSurface.RUNTIME_HEALTH
        )

    def test_importable_from_enums_package(self) -> None:
        """EnumIntegrationSurface is accessible via the enums package __init__."""
        from onex_change_control.enums import EnumIntegrationSurface as ImportedEnum

        assert ImportedEnum is EnumIntegrationSurface
