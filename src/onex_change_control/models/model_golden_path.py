"""Golden Path Model.

Pydantic schema model for golden path event chain test declarations.
A golden path test verifies that a specific input event produces the expected
output event, enabling automated contract verification of node pipelines.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# Security constraints to prevent DoS attacks
_MAX_STRING_LENGTH = 10000  # Max length for string fields
_MAX_LIST_ITEMS = 1000  # Max items in lists


class ModelGoldenPathAssertion(BaseModel, frozen=True):
    """A single assertion on an output event field.

    Specifies a field path, comparison operator, and expected value.
    Assertions are evaluated against the output event produced by running
    the golden path test.
    """

    field: str = Field(
        ...,
        description=(
            "Dot-separated field path on the output event "
            "(e.g., 'status' or 'data.result')"
        ),
        max_length=_MAX_STRING_LENGTH,
    )
    op: Literal["eq", "neq", "gte", "lte", "in", "contains"] = Field(
        ...,
        description="Comparison operator: eq | neq | gte | lte | in | contains",
    )
    value: Any = Field(
        ...,
        description="Expected value to compare against",
    )


class ModelGoldenPathInput(BaseModel, frozen=True):
    """Input specification for a golden path test.

    Describes the Kafka topic and fixture file to use as the input event.
    """

    topic: str = Field(
        ...,
        description="Kafka topic to publish the input event to",
        max_length=_MAX_STRING_LENGTH,
    )
    fixture: str = Field(
        ...,
        description="Path to the JSON fixture file relative to the repo root",
        max_length=_MAX_STRING_LENGTH,
    )
    input_correlation_id_field: str = Field(
        default="correlation_id",
        description="Field name in the fixture that holds the correlation ID",
        max_length=_MAX_STRING_LENGTH,
    )


class ModelGoldenPathOutput(BaseModel, frozen=True):
    """Output specification for a golden path test.

    Describes the Kafka topic to listen on for the output event, with optional
    schema validation and field-level assertions.
    """

    topic: str = Field(
        ...,
        description="Kafka topic to consume the output event from",
        max_length=_MAX_STRING_LENGTH,
    )
    output_correlation_id_field: str = Field(
        default="correlation_id",
        description="Field name in the output event that holds the correlation ID",
        max_length=_MAX_STRING_LENGTH,
    )
    schema_name: str | None = Field(
        default=None,
        description=(
            "Optional Pydantic model class name for output validation. "
            "When present and importable, the runner validates the output event "
            "against this schema. When not importable, "
            "schema_validation_status is set to 'skipped'."
        ),
        max_length=_MAX_STRING_LENGTH,
    )
    assertions: list[ModelGoldenPathAssertion] = Field(
        default_factory=list,
        description="Field-level assertions to evaluate against the output event",
        max_length=_MAX_LIST_ITEMS,
    )


class ModelGoldenPath(BaseModel, frozen=True):
    """Golden path event chain test declaration.

    Declares a full input-to-output contract test for a node pipeline. The golden
    path runner publishes the input fixture to the input topic, waits for a matching
    output event on the output topic, and evaluates all assertions.

    The timeout_ms field lives here (not in input or output) as the single source
    of truth for the test timeout. The infra field controls whether real Kafka or
    a mock is used.
    """

    input: ModelGoldenPathInput = Field(
        ...,
        description="Input event specification",
    )
    output: ModelGoldenPathOutput = Field(
        ...,
        description="Output event specification",
    )
    timeout_ms: int = Field(
        default=30000,
        description="Timeout in milliseconds for the full input-to-output round trip",
        ge=1,
    )
    infra: Literal["real", "mock"] = Field(
        default="real",
        description=(
            "Infrastructure mode: 'real' uses live Kafka, "
            "'mock' uses an in-process stub"
        ),
    )
    test_file: str | None = Field(
        default=None,
        description=(
            "Optional path to the pytest golden path file relative to the repo root"
        ),
        max_length=_MAX_STRING_LENGTH,
    )
