# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Model Dogfood Scorecard.

Typed Pydantic model hierarchy for platform dogfood health scoring.
Captures readiness dimensions, golden chain health, endpoint health,
delegation health, and infrastructure health in a single timestamped artifact.
"""

from pydantic import BaseModel, ConfigDict, Field

from onex_change_control.enums.enum_dogfood_status import (
    EnumDogfoodStatus,
    EnumRegressionSeverity,
)

# Security constraints
_MAX_STRING_LENGTH = 10000
_MAX_LIST_ITEMS = 500


class ModelReadinessDimension(BaseModel):
    """Per-dimension readiness verdict with supporting evidence.

    Each dimension maps to a named check in the platform_readiness skill.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(
        ...,
        description="Dimension name (e.g., 'golden_chain', 'endpoint_health')",
        max_length=_MAX_STRING_LENGTH,
    )
    status: EnumDogfoodStatus = Field(
        ...,
        description="PASS/WARN/FAIL/UNKNOWN verdict for this dimension",
    )
    evidence: str = Field(
        default="",
        description="Supporting evidence or failure reason",
        max_length=_MAX_STRING_LENGTH,
    )


class ModelGoldenChainHealth(BaseModel):
    """Health record for a single named golden chain.

    A golden chain is defined by a Kafka topic, a downstream DB table,
    and an expected row count. If a previously-healthy chain drops to
    0 rows, that constitutes a CRITICAL regression.
    """

    model_config = ConfigDict(frozen=True)

    chain_name: str = Field(
        ...,
        description="Human-readable chain identifier (e.g., 'context_audit')",
        max_length=_MAX_STRING_LENGTH,
    )
    topic: str = Field(
        ...,
        description="Kafka topic name",
        max_length=_MAX_STRING_LENGTH,
    )
    table: str = Field(
        ...,
        description="Downstream DB table name",
        max_length=_MAX_STRING_LENGTH,
    )
    row_count: int = Field(
        ...,
        description="Current row count in the downstream table",
        ge=0,
    )
    status: EnumDogfoodStatus = Field(
        ...,
        description="Chain health status",
    )


class ModelEndpointHealth(BaseModel):
    """Health record for a single HTTP endpoint probe.

    If an endpoint previously returned data but now returns empty,
    that constitutes a WARN regression.
    """

    model_config = ConfigDict(frozen=True)

    path: str = Field(
        ...,
        description="Endpoint path (e.g., '/api/health')",
        max_length=_MAX_STRING_LENGTH,
    )
    http_code: int = Field(
        ...,
        description="HTTP response status code",
        ge=100,
        le=599,
    )
    has_data: bool = Field(
        ...,
        description="Whether the response body contained meaningful data",
    )
    response_schema_valid: bool = Field(
        ...,
        description="Whether the response body matched the expected schema",
    )
    status: EnumDogfoodStatus = Field(
        ...,
        description="Endpoint health status",
    )


class ModelDelegationHealth(BaseModel):
    """Health of the delegation classifier and per-task-type routing.

    If classifier coverage drops between runs, that constitutes a WARN regression.
    """

    model_config = ConfigDict(frozen=True)

    task_type_coverage: dict[str, EnumDogfoodStatus] = Field(
        default_factory=dict,
        description="Per-task-type classifier coverage status",
    )
    classifier_coverage_pct: float = Field(
        ...,
        description="Overall classifier coverage as a percentage (0.0-100.0)",
        ge=0.0,
        le=100.0,
    )
    model_health: EnumDogfoodStatus = Field(
        ...,
        description="Health of the underlying inference model",
    )
    status: EnumDogfoodStatus = Field(
        ...,
        description="Overall delegation health status",
    )


class ModelInfrastructureHealth(BaseModel):
    """Infrastructure component health snapshot.

    Captures the health of Kafka (Redpanda), PostgreSQL, Docker,
    and consumer groups at scorecard capture time.
    """

    model_config = ConfigDict(frozen=True)

    kafka: EnumDogfoodStatus = Field(
        ...,
        description="Kafka (Redpanda) broker health",
    )
    postgres: EnumDogfoodStatus = Field(
        ...,
        description="PostgreSQL availability",
    )
    docker: EnumDogfoodStatus = Field(
        ...,
        description="Docker daemon and required container health",
    )
    consumer_groups: EnumDogfoodStatus = Field(
        ...,
        description="Kafka consumer group lag status",
    )
    status: EnumDogfoodStatus = Field(
        ...,
        description="Aggregate infrastructure health status",
    )


class ModelDogfoodRegression(BaseModel):
    """A single detected regression between two scorecard runs.

    Produced by the regression detector when comparing the current
    scorecard against a previous run.
    """

    model_config = ConfigDict(frozen=True)

    dimension: str = Field(
        ...,
        description="Which scorecard dimension the regression occurred in",
        max_length=_MAX_STRING_LENGTH,
    )
    field_path: str = Field(
        ...,
        description=(
            "Dotted path to the regressed field (e.g., 'golden_chains[0].row_count')"
        ),
        max_length=_MAX_STRING_LENGTH,
    )
    severity: EnumRegressionSeverity = Field(
        ...,
        description="CRITICAL or WARN",
    )
    previous_value: str = Field(
        ...,
        description="String representation of the previous value",
        max_length=_MAX_STRING_LENGTH,
    )
    current_value: str = Field(
        ...,
        description="String representation of the current value",
        max_length=_MAX_STRING_LENGTH,
    )
    description: str = Field(
        ...,
        description="Human-readable explanation of the regression",
        max_length=_MAX_STRING_LENGTH,
    )


class ModelDogfoodScorecard(BaseModel):
    """Platform dogfood scorecard — a single timestamped health snapshot.

    Captures all platform health dimensions in one structured artifact.
    Stored in .onex_state/dogfood/ as timestamped YAML files. The
    regression detector compares the current scorecard against the last
    N scorecards to surface regressions.

    Schema design:
    - frozen=True ensures scorecards are immutable once captured
    - All sub-models are also frozen
    - regressions is populated by the regression detector, not the probe
    """

    model_config = ConfigDict(frozen=True)

    # string-version-ok: wire type serialized to YAML/JSON at scorecard boundary
    schema_version: str = Field(
        default="1.0.0",
        description="Scorecard schema version (SemVer)",
        max_length=20,
    )
    captured_at: str = Field(
        ...,
        description=(
            "ISO 8601 timestamp when this scorecard was captured"
            " (e.g., '2026-04-10T14:30:00Z')"
        ),
        max_length=30,
    )
    run_id: str = Field(
        ...,
        description=(
            "Unique identifier for this scorecard run (e.g., UUID or timestamp slug)"
        ),
        max_length=_MAX_STRING_LENGTH,
    )
    readiness_dimensions: list[ModelReadinessDimension] = Field(
        default_factory=list,
        description="Per-dimension readiness verdicts",
        max_length=_MAX_LIST_ITEMS,
    )
    golden_chains: list[ModelGoldenChainHealth] = Field(
        default_factory=list,
        description="Golden chain health records",
        max_length=_MAX_LIST_ITEMS,
    )
    endpoints: list[ModelEndpointHealth] = Field(
        default_factory=list,
        description="Endpoint health probes",
        max_length=_MAX_LIST_ITEMS,
    )
    delegation: ModelDelegationHealth | None = Field(
        default=None,
        description="Delegation classifier and routing health",
    )
    infrastructure: ModelInfrastructureHealth | None = Field(
        default=None,
        description="Infrastructure component health",
    )
    regressions: list[ModelDogfoodRegression] = Field(
        default_factory=list,
        description="Regressions detected by comparing against prior runs",
        max_length=_MAX_LIST_ITEMS,
    )
    overall_status: EnumDogfoodStatus = Field(
        ...,
        description="Aggregate health status across all dimensions",
    )
