# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for ModelDogfoodScorecard and the dogfood regression detector."""

import pytest
from pydantic import ValidationError

from onex_change_control.enums.enum_dogfood_status import (
    EnumDogfoodStatus,
    EnumRegressionSeverity,
)
from onex_change_control.models.model_dogfood_scorecard import (
    ModelDelegationHealth,
    ModelDogfoodScorecard,
    ModelEndpointHealth,
    ModelGoldenChainHealth,
    ModelInfrastructureHealth,
    ModelReadinessDimension,
)
from onex_change_control.validation.dogfood_regression import detect_regressions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_scorecard(**kwargs) -> ModelDogfoodScorecard:  # type: ignore[no-untyped-def]
    """Return a minimal valid scorecard, overridable via kwargs."""
    defaults: dict[str, object] = {
        "captured_at": "2026-04-10T14:30:00Z",
        "run_id": "test-run-001",
        "overall_status": EnumDogfoodStatus.PASS,
    }
    defaults.update(kwargs)
    return ModelDogfoodScorecard(**defaults)


def _chain(
    name: str, row_count: int, status: EnumDogfoodStatus
) -> ModelGoldenChainHealth:
    return ModelGoldenChainHealth(
        chain_name=name,
        topic=f"onex.evt.{name}.v1",
        table=name,
        row_count=row_count,
        status=status,
    )


def _endpoint(
    path: str,
    *,
    has_data: bool,
    status: EnumDogfoodStatus,
) -> ModelEndpointHealth:
    return ModelEndpointHealth(
        path=path,
        http_code=200,
        has_data=has_data,
        response_schema_valid=True,
        status=status,
    )


def _delegation(
    coverage_pct: float, status: EnumDogfoodStatus
) -> ModelDelegationHealth:
    return ModelDelegationHealth(
        classifier_coverage_pct=coverage_pct,
        model_health=status,
        status=status,
    )


# ---------------------------------------------------------------------------
# ModelReadinessDimension
# ---------------------------------------------------------------------------


class TestModelReadinessDimension:
    def test_valid(self) -> None:
        dim = ModelReadinessDimension(
            name="golden_chain",
            status=EnumDogfoodStatus.PASS,
            evidence="All chains healthy",
        )
        assert dim.name == "golden_chain"
        assert dim.status == EnumDogfoodStatus.PASS

    def test_evidence_defaults_empty(self) -> None:
        dim = ModelReadinessDimension(name="endpoints", status=EnumDogfoodStatus.WARN)
        assert dim.evidence == ""

    def test_frozen(self) -> None:
        dim = ModelReadinessDimension(name="x", status=EnumDogfoodStatus.PASS)
        with pytest.raises(ValidationError):
            dim.name = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ModelGoldenChainHealth
# ---------------------------------------------------------------------------


class TestModelGoldenChainHealth:
    def test_valid(self) -> None:
        chain = _chain("context_audit", 1200, EnumDogfoodStatus.PASS)
        assert chain.row_count == 1200

    def test_row_count_cannot_be_negative(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            ModelGoldenChainHealth(
                chain_name="x",
                topic="t",
                table="t",
                row_count=-1,
                status=EnumDogfoodStatus.FAIL,
            )


# ---------------------------------------------------------------------------
# ModelEndpointHealth
# ---------------------------------------------------------------------------


class TestModelEndpointHealth:
    def test_valid(self) -> None:
        ep = _endpoint("/api/health", has_data=True, status=EnumDogfoodStatus.PASS)
        assert ep.http_code == 200
        assert ep.has_data is True

    def test_invalid_http_code_too_low(self) -> None:
        with pytest.raises(ValidationError):
            ModelEndpointHealth(
                path="/x",
                http_code=99,
                has_data=False,
                response_schema_valid=False,
                status=EnumDogfoodStatus.FAIL,
            )

    def test_invalid_http_code_too_high(self) -> None:
        with pytest.raises(ValidationError):
            ModelEndpointHealth(
                path="/x",
                http_code=600,
                has_data=False,
                response_schema_valid=False,
                status=EnumDogfoodStatus.FAIL,
            )


# ---------------------------------------------------------------------------
# ModelDelegationHealth
# ---------------------------------------------------------------------------


class TestModelDelegationHealth:
    def test_valid(self) -> None:
        d = _delegation(92.5, EnumDogfoodStatus.PASS)
        assert d.classifier_coverage_pct == 92.5

    def test_coverage_cannot_exceed_100(self) -> None:
        with pytest.raises(ValidationError):
            ModelDelegationHealth(
                classifier_coverage_pct=100.1,
                model_health=EnumDogfoodStatus.PASS,
                status=EnumDogfoodStatus.PASS,
            )

    def test_coverage_cannot_be_negative(self) -> None:
        with pytest.raises(ValidationError):
            ModelDelegationHealth(
                classifier_coverage_pct=-1.0,
                model_health=EnumDogfoodStatus.PASS,
                status=EnumDogfoodStatus.PASS,
            )


# ---------------------------------------------------------------------------
# ModelInfrastructureHealth
# ---------------------------------------------------------------------------


class TestModelInfrastructureHealth:
    def test_valid_all_pass(self) -> None:
        infra = ModelInfrastructureHealth(
            kafka=EnumDogfoodStatus.PASS,
            postgres=EnumDogfoodStatus.PASS,
            docker=EnumDogfoodStatus.PASS,
            consumer_groups=EnumDogfoodStatus.PASS,
            status=EnumDogfoodStatus.PASS,
        )
        assert infra.status == EnumDogfoodStatus.PASS

    def test_degraded_component(self) -> None:
        infra = ModelInfrastructureHealth(
            kafka=EnumDogfoodStatus.FAIL,
            postgres=EnumDogfoodStatus.PASS,
            docker=EnumDogfoodStatus.PASS,
            consumer_groups=EnumDogfoodStatus.WARN,
            status=EnumDogfoodStatus.FAIL,
        )
        assert infra.kafka == EnumDogfoodStatus.FAIL


# ---------------------------------------------------------------------------
# ModelDogfoodScorecard
# ---------------------------------------------------------------------------


class TestModelDogfoodScorecard:
    def test_minimal_valid(self) -> None:
        sc = _minimal_scorecard()
        assert sc.schema_version == "1.0.0"
        assert sc.overall_status == EnumDogfoodStatus.PASS
        assert sc.regressions == []

    def test_full_scorecard(self) -> None:
        sc = ModelDogfoodScorecard(
            captured_at="2026-04-10T14:30:00Z",
            run_id="full-run-001",
            readiness_dimensions=[
                ModelReadinessDimension(
                    name="golden_chain", status=EnumDogfoodStatus.PASS
                ),
            ],
            golden_chains=[_chain("context_audit", 1200, EnumDogfoodStatus.PASS)],
            endpoints=[
                _endpoint("/api/health", has_data=True, status=EnumDogfoodStatus.PASS)
            ],
            delegation=_delegation(95.0, EnumDogfoodStatus.PASS),
            infrastructure=ModelInfrastructureHealth(
                kafka=EnumDogfoodStatus.PASS,
                postgres=EnumDogfoodStatus.PASS,
                docker=EnumDogfoodStatus.PASS,
                consumer_groups=EnumDogfoodStatus.PASS,
                status=EnumDogfoodStatus.PASS,
            ),
            overall_status=EnumDogfoodStatus.PASS,
        )
        assert len(sc.golden_chains) == 1
        assert len(sc.endpoints) == 1
        assert sc.delegation is not None

    def test_frozen(self) -> None:
        sc = _minimal_scorecard()
        with pytest.raises(ValidationError):
            sc.run_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Regression Detector
# ---------------------------------------------------------------------------


class TestDogfoodRegressionDetector:
    def test_no_regressions_when_identical(self) -> None:
        sc = _minimal_scorecard(
            golden_chains=[_chain("c1", 500, EnumDogfoodStatus.PASS)],
        )
        result = detect_regressions(sc, sc)
        assert result == []

    def test_critical_chain_zero_rows(self) -> None:
        prev = _minimal_scorecard(
            golden_chains=[_chain("c1", 500, EnumDogfoodStatus.PASS)],
        )
        curr = _minimal_scorecard(
            golden_chains=[_chain("c1", 0, EnumDogfoodStatus.FAIL)],
        )
        regressions = detect_regressions(curr, prev)
        assert len(regressions) == 1
        r = regressions[0]
        assert r.severity == EnumRegressionSeverity.CRITICAL
        assert r.dimension == "golden_chains"
        assert r.current_value == "0"

    def test_warn_row_count_drop_over_20pct(self) -> None:
        prev = _minimal_scorecard(
            golden_chains=[_chain("c1", 1000, EnumDogfoodStatus.PASS)],
        )
        curr = _minimal_scorecard(
            golden_chains=[_chain("c1", 750, EnumDogfoodStatus.WARN)],
        )
        regressions = detect_regressions(curr, prev)
        assert len(regressions) == 1
        assert regressions[0].severity == EnumRegressionSeverity.WARN

    def test_no_warn_for_small_row_count_drop(self) -> None:
        prev = _minimal_scorecard(
            golden_chains=[_chain("c1", 1000, EnumDogfoodStatus.PASS)],
        )
        curr = _minimal_scorecard(
            golden_chains=[_chain("c1", 850, EnumDogfoodStatus.PASS)],
        )
        regressions = detect_regressions(curr, prev)
        # 15% drop — below threshold, no regression
        assert regressions == []

    def test_warn_endpoint_data_loss(self) -> None:
        prev = _minimal_scorecard(
            endpoints=[
                _endpoint("/api/health", has_data=True, status=EnumDogfoodStatus.PASS)
            ],
        )
        curr = _minimal_scorecard(
            endpoints=[
                _endpoint("/api/health", has_data=False, status=EnumDogfoodStatus.WARN)
            ],
        )
        regressions = detect_regressions(curr, prev)
        assert len(regressions) == 1
        r = regressions[0]
        assert r.severity == EnumRegressionSeverity.WARN
        assert r.dimension == "endpoints"
        assert "/api/health" in r.field_path

    def test_no_regression_endpoint_still_no_data(self) -> None:
        """Endpoint that never had data going to no-data is not a regression."""
        prev = _minimal_scorecard(
            endpoints=[
                _endpoint("/api/health", has_data=False, status=EnumDogfoodStatus.WARN)
            ],
        )
        curr = _minimal_scorecard(
            endpoints=[
                _endpoint("/api/health", has_data=False, status=EnumDogfoodStatus.WARN)
            ],
        )
        regressions = detect_regressions(curr, prev)
        assert regressions == []

    def test_warn_delegation_coverage_drop(self) -> None:
        prev = _minimal_scorecard(delegation=_delegation(95.0, EnumDogfoodStatus.PASS))
        curr = _minimal_scorecard(delegation=_delegation(80.0, EnumDogfoodStatus.WARN))
        regressions = detect_regressions(curr, prev)
        assert len(regressions) == 1
        r = regressions[0]
        assert r.severity == EnumRegressionSeverity.WARN
        assert r.dimension == "delegation"
        assert "80.0" in r.current_value

    def test_no_regression_delegation_coverage_same(self) -> None:
        prev = _minimal_scorecard(delegation=_delegation(95.0, EnumDogfoodStatus.PASS))
        curr = _minimal_scorecard(delegation=_delegation(95.0, EnumDogfoodStatus.PASS))
        regressions = detect_regressions(curr, prev)
        assert regressions == []

    def test_no_regression_delegation_none(self) -> None:
        """No regression when either scorecard has no delegation data."""
        prev = _minimal_scorecard()
        curr = _minimal_scorecard()
        regressions = detect_regressions(curr, prev)
        assert regressions == []

    def test_new_chain_not_in_previous_ignored(self) -> None:
        """Chains not present in the previous run do not generate regressions."""
        prev = _minimal_scorecard(
            golden_chains=[_chain("c1", 500, EnumDogfoodStatus.PASS)],
        )
        curr = _minimal_scorecard(
            golden_chains=[
                _chain("c1", 500, EnumDogfoodStatus.PASS),
                _chain("c2_new", 0, EnumDogfoodStatus.UNKNOWN),
            ],
        )
        regressions = detect_regressions(curr, prev)
        # c2_new has no prior baseline — not a regression
        assert regressions == []

    def test_multiple_regressions(self) -> None:
        prev = _minimal_scorecard(
            golden_chains=[_chain("c1", 500, EnumDogfoodStatus.PASS)],
            endpoints=[
                _endpoint("/api/data", has_data=True, status=EnumDogfoodStatus.PASS)
            ],
            delegation=_delegation(95.0, EnumDogfoodStatus.PASS),
        )
        curr = _minimal_scorecard(
            golden_chains=[_chain("c1", 0, EnumDogfoodStatus.FAIL)],
            endpoints=[
                _endpoint("/api/data", has_data=False, status=EnumDogfoodStatus.WARN)
            ],
            delegation=_delegation(70.0, EnumDogfoodStatus.WARN),
            overall_status=EnumDogfoodStatus.FAIL,
        )
        regressions = detect_regressions(curr, prev)
        assert len(regressions) == 3
        severities = {r.severity for r in regressions}
        assert EnumRegressionSeverity.CRITICAL in severities
        assert EnumRegressionSeverity.WARN in severities
