# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for DB boundary exception model and registry."""

import pytest
from pydantic import ValidationError

from onex_change_control.enums.enum_db_boundary import (
    EnumDbBoundaryExceptionStatus,
    EnumDbBoundaryReasonCategory,
)
from onex_change_control.models.model_db_boundary_exception import (
    ModelDbBoundaryException,
    ModelDbBoundaryExceptionsRegistry,
)


@pytest.mark.unit
class TestModelDbBoundaryException:
    """Tests for ModelDbBoundaryException."""

    def test_valid_exception_creation(self) -> None:
        """Test creating a valid exception with all fields."""
        exc = ModelDbBoundaryException(
            repo="omnimemory",
            file="src/omnimemory/fixtures/seed.py",
            usage="Reads omnibase_infra session table for test seeding",
            reason_category=EnumDbBoundaryReasonCategory.TEST_ONLY,
            justification="Test fixtures need session data to seed state",
            owner="jonah",
            approved_by="jonah",
            review_by="2026-06",
            status=EnumDbBoundaryExceptionStatus.APPROVED,
        )
        assert exc.repo == "omnimemory"
        assert exc.reason_category == EnumDbBoundaryReasonCategory.TEST_ONLY
        assert exc.status == EnumDbBoundaryExceptionStatus.APPROVED

    def test_missing_required_field_raises(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            ModelDbBoundaryException(  # type: ignore[call-arg]
                repo="omnimemory",
                # missing file, usage, reason_category, etc.
            )

    def test_invalid_reason_category_raises(self) -> None:
        """Test that invalid reason_category raises ValidationError."""
        with pytest.raises(ValidationError):
            ModelDbBoundaryException(
                repo="omnimemory",
                file="src/omnimemory/fixtures/seed.py",
                usage="test",
                reason_category="INVALID_CATEGORY",
                justification="test",
                owner="jonah",
                approved_by="jonah",
                review_by="2026-06",
            )

    def test_review_by_validates_yyyy_mm_format(self) -> None:
        """Test that review_by validates YYYY-MM format."""
        # Valid format
        exc = ModelDbBoundaryException(
            repo="omnimemory",
            file="src/omnimemory/fixtures/seed.py",
            usage="test",
            reason_category=EnumDbBoundaryReasonCategory.TEST_ONLY,
            justification="test",
            owner="jonah",
            approved_by="jonah",
            review_by="2026-12",
        )
        assert exc.review_by == "2026-12"

    def test_review_by_rejects_invalid_format(self) -> None:
        """Test that review_by rejects invalid formats."""
        with pytest.raises(ValidationError, match="review_by"):
            ModelDbBoundaryException(
                repo="omnimemory",
                file="src/omnimemory/fixtures/seed.py",
                usage="test",
                reason_category=EnumDbBoundaryReasonCategory.TEST_ONLY,
                justification="test",
                owner="jonah",
                approved_by="jonah",
                review_by="2026-13",  # invalid month
            )

        with pytest.raises(ValidationError, match="review_by"):
            ModelDbBoundaryException(
                repo="omnimemory",
                file="src/omnimemory/fixtures/seed.py",
                usage="test",
                reason_category=EnumDbBoundaryReasonCategory.TEST_ONLY,
                justification="test",
                owner="jonah",
                approved_by="jonah",
                review_by="not-a-date",
            )

    def test_frozen_model_rejects_mutation(self) -> None:
        """Test that frozen model rejects attribute mutation."""
        exc = ModelDbBoundaryException(
            repo="omnimemory",
            file="src/omnimemory/fixtures/seed.py",
            usage="test",
            reason_category=EnumDbBoundaryReasonCategory.TEST_ONLY,
            justification="test",
            owner="jonah",
            approved_by="jonah",
            review_by="2026-06",
        )
        with pytest.raises(ValidationError):
            exc.repo = "omniintelligence"  # type: ignore[misc]


@pytest.mark.unit
class TestModelDbBoundaryExceptionsRegistry:
    """Tests for ModelDbBoundaryExceptionsRegistry."""

    def test_empty_registry_creation(self) -> None:
        """Test creating an empty registry."""
        registry = ModelDbBoundaryExceptionsRegistry(exceptions=[])
        assert registry.exceptions == []

    def test_registry_with_exceptions(self) -> None:
        """Test creating a registry with exceptions."""
        exc = ModelDbBoundaryException(
            repo="omnimemory",
            file="src/omnimemory/fixtures/seed.py",
            usage="test",
            reason_category=EnumDbBoundaryReasonCategory.TEST_ONLY,
            justification="test",
            owner="jonah",
            approved_by="jonah",
            review_by="2026-06",
        )
        registry = ModelDbBoundaryExceptionsRegistry(exceptions=[exc])
        assert len(registry.exceptions) == 1
        assert registry.exceptions[0].repo == "omnimemory"

    def test_yaml_round_trip_via_model_validate(self) -> None:
        """Test YAML round-trip loading via model_validate."""
        raw_data = {
            "exceptions": [
                {
                    "repo": "omnimemory",
                    "file": "src/omnimemory/fixtures/seed.py",
                    "usage": "Reads session table for test seeding",
                    "reason_category": "TEST_ONLY",
                    "justification": "Test fixtures need session data",
                    "owner": "jonah",
                    "approved_by": "jonah",
                    "review_by": "2026-06",
                    "status": "APPROVED",
                },
            ],
        }
        registry = ModelDbBoundaryExceptionsRegistry.model_validate(raw_data)
        assert len(registry.exceptions) == 1
        assert (
            registry.exceptions[0].reason_category
            == EnumDbBoundaryReasonCategory.TEST_ONLY
        )
