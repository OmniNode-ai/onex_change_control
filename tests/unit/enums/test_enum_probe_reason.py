# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for EnumProbeReason."""

import pytest

from onex_change_control.enums.enum_probe_reason import EnumProbeReason


@pytest.mark.unit
class TestEnumProbeReason:
    """Tests for EnumProbeReason enum."""

    def test_all_values_defined(self) -> None:
        """All expected values are present."""
        assert EnumProbeReason.NO_CONTRACT is not None
        assert EnumProbeReason.PROBE_UNAVAILABLE is not None
        assert EnumProbeReason.INCONCLUSIVE is not None
        assert EnumProbeReason.NOT_APPLICABLE is not None

    def test_value_count(self) -> None:
        """Exactly four members defined."""
        assert len(EnumProbeReason) == 4

    def test_str_returns_value(self) -> None:
        """__str__ returns the string value, not 'EnumProbeReason.X'."""
        assert str(EnumProbeReason.NO_CONTRACT) == "no_contract"
        assert str(EnumProbeReason.PROBE_UNAVAILABLE) == "probe_unavailable"
        assert str(EnumProbeReason.INCONCLUSIVE) == "inconclusive"
        assert str(EnumProbeReason.NOT_APPLICABLE) == "not_applicable"

    def test_is_str_subclass(self) -> None:
        """EnumProbeReason members are str instances."""
        for member in EnumProbeReason:
            assert isinstance(member, str)

    def test_roundtrip_from_value(self) -> None:
        """Can construct members from their string values."""
        assert EnumProbeReason("no_contract") is EnumProbeReason.NO_CONTRACT
        assert EnumProbeReason("probe_unavailable") is EnumProbeReason.PROBE_UNAVAILABLE
        assert EnumProbeReason("inconclusive") is EnumProbeReason.INCONCLUSIVE
        assert EnumProbeReason("not_applicable") is EnumProbeReason.NOT_APPLICABLE

    def test_importable_from_enums_package(self) -> None:
        """EnumProbeReason is accessible via the enums package __init__."""
        from onex_change_control.enums import EnumProbeReason as ImportedEnum

        assert ImportedEnum is EnumProbeReason
