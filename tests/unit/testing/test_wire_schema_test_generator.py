# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for wire schema test generator (OMN-7371)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from onex_change_control.models.model_wire_schema_contract import (
    load_wire_schema_contract,
)
from onex_change_control.testing.wire_schema_test_generator import (
    generate_all_test_cases,
    generate_test_cases_for_contract,
    pytest_params_from_contracts,
)


def _find_omnibase_infra() -> Path | None:
    """Locate omnibase_infra via env var or well-known candidate paths."""
    env_root = os.environ.get("OMNI_HOME")
    candidates = []
    if env_root:
        candidates.append(Path(env_root) / "omnibase_infra")
    candidates.extend(
        [
            Path("/Volumes/PRO-G40/Code/omni_home/omnibase_infra"),
            Path("/Users/jonah/Code/omni_home/omnibase_infra"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _make_contract(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "topic": "onex.evt.test.event.v1",
        "schema_version": "1.0.0",
        "producer": {
            "repo": "test_repo",
            "file": "src/test_repo/producer.py",
            "function": "emit",
        },
        "consumer": {
            "repo": "consumer_repo",
            "file": "src/consumer_repo/consumer.py",
            "model": "ModelTestEvent",
        },
        "required_fields": [
            {"name": "id", "type": "uuid"},
            {"name": "name", "type": "string"},
        ],
        "optional_fields": [
            {"name": "metadata", "type": "object", "nullable": True},
        ],
    }
    base.update(overrides)
    return base


class TestGenerateTestCasesForContract:
    """Test case generation for a single contract."""

    def test_generates_baseline_checks(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        cases = generate_test_cases_for_contract("test.yaml", contract)
        check_names = [c.check_name for c in cases]
        assert "contract_valid" in check_names
        assert "no_duplicate_fields" in check_names

    def test_contract_valid_always_passes(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        cases = generate_test_cases_for_contract("test.yaml", contract)
        valid_case = next(c for c in cases if c.check_name == "contract_valid")
        assert valid_case.passed is True

    def test_no_duplicate_fields_passes_for_clean_contract(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        cases = generate_test_cases_for_contract("test.yaml", contract)
        dup_case = next(c for c in cases if c.check_name == "no_duplicate_fields")
        assert dup_case.passed is True

    def test_skips_consumer_check_when_model_not_importable(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        cases = generate_test_cases_for_contract("test.yaml", contract)
        consumer_case = next(
            c for c in cases if c.check_name == "consumer_fields_match"
        )
        # Model won't be importable in test env
        assert consumer_case.passed is True
        assert "Skipped" in consumer_case.details

    def test_repr_shows_topic_and_check(self) -> None:
        contract = load_wire_schema_contract(_make_contract())
        cases = generate_test_cases_for_contract("test.yaml", contract)
        assert "onex.evt.test.event.v1" in repr(cases[0])
        assert "PASS" in repr(cases[0])


class TestGenerateAllTestCases:
    """Test case generation across multiple contracts."""

    def test_discovers_and_generates_from_directory(self, tmp_path: Path) -> None:
        data = _make_contract()
        (tmp_path / "test_event_v1.yaml").write_text(yaml.dump(data))
        cases = generate_all_test_cases([tmp_path])
        assert len(cases) >= 2  # At least contract_valid + no_duplicate_fields

    def test_skips_non_contract_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "not_a_contract_v1.yaml").write_text(yaml.dump({"key": "value"}))
        cases = generate_all_test_cases([tmp_path])
        assert len(cases) == 0

    def test_multiple_contracts_generate_separate_cases(self, tmp_path: Path) -> None:
        data1 = _make_contract(topic="onex.evt.test.a.v1")
        data2 = _make_contract(topic="onex.evt.test.b.v1")
        (tmp_path / "a_v1.yaml").write_text(yaml.dump(data1))
        (tmp_path / "b_v1.yaml").write_text(yaml.dump(data2))
        cases = generate_all_test_cases([tmp_path])
        topics = {c.contract.topic for c in cases}
        assert topics == {"onex.evt.test.a.v1", "onex.evt.test.b.v1"}

    def test_empty_directory_returns_no_cases(self, tmp_path: Path) -> None:
        cases = generate_all_test_cases([tmp_path])
        assert cases == []


class TestPytestParamsFromContracts:
    """Pytest parametrize output format."""

    def test_returns_tuples(self, tmp_path: Path) -> None:
        data = _make_contract()
        (tmp_path / "test_event_v1.yaml").write_text(yaml.dump(data))
        params = pytest_params_from_contracts([tmp_path])
        assert len(params) >= 2
        # Each tuple: (topic, check_name, expected_pass, details)
        for topic, check_name, passed, details in params:
            assert isinstance(topic, str)
            assert isinstance(check_name, str)
            assert isinstance(passed, bool)
            assert isinstance(details, str)


class TestRoutingDecisionV1Compatibility:
    """Verify the generator works with the existing routing_decision_v1.yaml."""

    def test_generates_passing_tests_for_routing_decision(self) -> None:
        infra_root = _find_omnibase_infra()
        if infra_root is None:
            pytest.skip("omnibase_infra not available (set OMNI_HOME env var)")

        contracts_dir = (
            infra_root
            / "src/omnibase_infra/services/observability/agent_actions/contracts"
        )
        if not contracts_dir.exists():
            pytest.skip("contracts directory not found in omnibase_infra")

        cases = generate_all_test_cases([contracts_dir])
        routing_cases = [c for c in cases if "routing-decision" in c.contract.topic]
        assert len(routing_cases) >= 2
        for case in routing_cases:
            if case.check_name in ("contract_valid", "no_duplicate_fields"):
                assert case.passed is True, f"{case.check_name}: {case.details}"
