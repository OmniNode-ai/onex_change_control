"""Unit tests for ModelGoldenPath and related models.

Tests cover:
- ModelGoldenPath validates from YAML
- timeout_ms defaults to 30000 (single source of truth, not in input/output)
- schema_name=None is valid
- input_correlation_id_field and output_correlation_id_field are independent fields
- All assertion ops round-trip through model_validate
- ModelTicketContract with and without golden_path both valid
- check-schema-purity invariant: no env/fs/network in new models (structural only)
"""

import pytest
from pydantic import ValidationError

from onex_change_control.models.model_golden_path import (
    ModelGoldenPath,
    ModelGoldenPathAssertion,
    ModelGoldenPathInput,
    ModelGoldenPathOutput,
)
from onex_change_control.models.model_ticket_contract import (
    ModelEmergencyBypass,
    ModelTicketContract,
)

# Named constants for timeout values (avoids PLR2004 magic value warnings)
_DEFAULT_TIMEOUT_MS = 30000
_CUSTOM_TIMEOUT_MS = 60000
_FAST_TIMEOUT_MS = 15000
_LONG_TIMEOUT_MS = 45000
_EXPECTED_ASSERTIONS_COUNT_2 = 2
_EXPECTED_ASSERTIONS_COUNT_3 = 3
_EXPECTED_ASSERTIONS_COUNT_1 = 1


def _make_input(**kwargs: object) -> ModelGoldenPathInput:
    """Create a minimal ModelGoldenPathInput for testing."""
    defaults: dict[str, object] = {
        "topic": "onex.cmd.myservice.myevent.v1",
        "fixture": "tests/fixtures/golden/my_fixture.json",
    }
    defaults.update(kwargs)
    return ModelGoldenPathInput(**defaults)  # type: ignore[arg-type]


def _make_output(**kwargs: object) -> ModelGoldenPathOutput:
    """Create a minimal ModelGoldenPathOutput for testing."""
    defaults: dict[str, object] = {
        "topic": "onex.evt.myservice.myevent.v1",
    }
    defaults.update(kwargs)
    return ModelGoldenPathOutput(**defaults)  # type: ignore[arg-type]


def _make_golden_path(**kwargs: object) -> ModelGoldenPath:
    """Create a minimal ModelGoldenPath for testing."""
    defaults: dict[str, object] = {
        "input": _make_input(),
        "output": _make_output(),
    }
    defaults.update(kwargs)
    return ModelGoldenPath(**defaults)  # type: ignore[arg-type]


def _make_contract(**kwargs: object) -> ModelTicketContract:
    """Create a minimal ModelTicketContract for testing."""
    defaults: dict[str, object] = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-2980",
        "summary": "Test contract",
        "is_seam_ticket": False,
        "interface_change": False,
        "emergency_bypass": ModelEmergencyBypass(enabled=False),
    }
    defaults.update(kwargs)
    return ModelTicketContract(**defaults)  # type: ignore[arg-type]


class TestModelGoldenPathInput:
    """Tests for ModelGoldenPathInput."""

    @pytest.mark.unit
    def test_minimal_input(self) -> None:
        """Test creating a minimal ModelGoldenPathInput."""
        inp = _make_input()
        assert inp.topic == "onex.cmd.myservice.myevent.v1"
        assert inp.fixture == "tests/fixtures/golden/my_fixture.json"
        assert inp.input_correlation_id_field == "correlation_id"

    @pytest.mark.unit
    def test_custom_correlation_id_field(self) -> None:
        """Test that input_correlation_id_field can be customized."""
        inp = _make_input(input_correlation_id_field="trace_id")
        assert inp.input_correlation_id_field == "trace_id"

    @pytest.mark.unit
    def test_immutable(self) -> None:
        """Test that ModelGoldenPathInput is frozen (immutable)."""
        inp = _make_input()
        with pytest.raises((AttributeError, ValidationError)):
            inp.topic = "other.topic"  # type: ignore[misc]

    @pytest.mark.unit
    def test_round_trip(self) -> None:
        """Test that ModelGoldenPathInput round-trips through model_validate."""
        inp = _make_input(input_correlation_id_field="custom_cid")
        data = inp.model_dump()
        restored = ModelGoldenPathInput.model_validate(data)
        assert restored == inp


class TestModelGoldenPathOutput:
    """Tests for ModelGoldenPathOutput."""

    @pytest.mark.unit
    def test_minimal_output(self) -> None:
        """Test creating a minimal ModelGoldenPathOutput."""
        out = _make_output()
        assert out.topic == "onex.evt.myservice.myevent.v1"
        assert out.output_correlation_id_field == "correlation_id"
        assert out.schema_name is None
        assert out.assertions == []

    @pytest.mark.unit
    def test_schema_name_none_is_valid(self) -> None:
        """Test that schema_name=None is explicitly valid."""
        out = _make_output(schema_name=None)
        assert out.schema_name is None

    @pytest.mark.unit
    def test_schema_name_optional_present(self) -> None:
        """Test that schema_name can be set to a class name."""
        out = _make_output(schema_name="ModelMyEvent")
        assert out.schema_name == "ModelMyEvent"

    @pytest.mark.unit
    def test_custom_correlation_id_field(self) -> None:
        """Test that output_correlation_id_field can be customized independently."""
        out = _make_output(output_correlation_id_field="event_id")
        assert out.output_correlation_id_field == "event_id"

    @pytest.mark.unit
    def test_output_with_assertions(self) -> None:
        """Test creating output with field assertions."""
        out = _make_output(
            assertions=[
                ModelGoldenPathAssertion(field="status", op="eq", value="completed"),
                ModelGoldenPathAssertion(field="count", op="gte", value=1),
            ]
        )
        assert len(out.assertions) == _EXPECTED_ASSERTIONS_COUNT_2

    @pytest.mark.unit
    def test_immutable(self) -> None:
        """Test that ModelGoldenPathOutput is frozen (immutable)."""
        out = _make_output()
        with pytest.raises((AttributeError, ValidationError)):
            out.topic = "other.topic"  # type: ignore[misc]

    @pytest.mark.unit
    def test_round_trip(self) -> None:
        """Test that ModelGoldenPathOutput round-trips through model_validate."""
        out = _make_output(
            schema_name="ModelFoo",
            output_correlation_id_field="custom_cid",
            assertions=[
                ModelGoldenPathAssertion(field="status", op="eq", value="done"),
            ],
        )
        data = out.model_dump()
        restored = ModelGoldenPathOutput.model_validate(data)
        assert restored == out


class TestModelGoldenPathAssertion:
    """Tests for ModelGoldenPathAssertion."""

    @pytest.mark.unit
    @pytest.mark.parametrize("op", ["eq", "neq", "gte", "lte", "in", "contains"])
    def test_all_ops_valid(self, op: str) -> None:
        """Test that all assertion ops are valid and round-trip correctly."""
        assertion = ModelGoldenPathAssertion(field="status", op=op, value="anything")  # type: ignore[arg-type]
        assert assertion.op == op

    @pytest.mark.unit
    @pytest.mark.parametrize("op", ["eq", "neq", "gte", "lte", "in", "contains"])
    def test_ops_round_trip_through_model_validate(self, op: str) -> None:
        """Test that all ops round-trip through model_validate."""
        original = ModelGoldenPathAssertion(field="status", op=op, value=42)  # type: ignore[arg-type]
        data = original.model_dump()
        restored = ModelGoldenPathAssertion.model_validate(data)
        assert restored == original

    @pytest.mark.unit
    def test_invalid_op(self) -> None:
        """Test that an invalid op raises ValidationError."""
        with pytest.raises(ValidationError):
            ModelGoldenPathAssertion(field="status", op="invalid_op", value="x")  # type: ignore[arg-type]

    @pytest.mark.unit
    def test_various_value_types(self) -> None:
        """Test that value accepts various types."""
        string_val = ModelGoldenPathAssertion(
            field="status", op="eq", value="completed"
        )
        int_val = ModelGoldenPathAssertion(field="count", op="gte", value=1)
        bool_val = ModelGoldenPathAssertion(field="success", op="eq", value=True)
        list_val = ModelGoldenPathAssertion(field="tags", op="in", value=["a", "b"])

        assert string_val.value == "completed"
        assert int_val.value == 1
        assert bool_val.value is True
        assert list_val.value == ["a", "b"]

    @pytest.mark.unit
    def test_immutable(self) -> None:
        """Test that ModelGoldenPathAssertion is frozen (immutable)."""
        assertion = ModelGoldenPathAssertion(field="status", op="eq", value="done")
        with pytest.raises((AttributeError, ValidationError)):
            assertion.field = "other"  # type: ignore[misc]


class TestModelGoldenPath:
    """Tests for ModelGoldenPath."""

    @pytest.mark.unit
    def test_minimal_golden_path(self) -> None:
        """Test creating a minimal ModelGoldenPath with defaults."""
        gp = _make_golden_path()
        assert gp.timeout_ms == _DEFAULT_TIMEOUT_MS
        assert gp.infra == "real"
        assert gp.test_file is None

    @pytest.mark.unit
    def test_timeout_ms_default_is_30000(self) -> None:
        """Test that timeout_ms defaults to 30000 (single source of truth)."""
        gp = _make_golden_path()
        assert gp.timeout_ms == _DEFAULT_TIMEOUT_MS

        # Verify it's not on input or output
        assert not hasattr(gp.input, "timeout_ms")
        assert not hasattr(gp.output, "timeout_ms")

    @pytest.mark.unit
    def test_custom_timeout_ms(self) -> None:
        """Test setting a custom timeout_ms."""
        gp = _make_golden_path(timeout_ms=_CUSTOM_TIMEOUT_MS)
        assert gp.timeout_ms == _CUSTOM_TIMEOUT_MS

    @pytest.mark.unit
    def test_timeout_ms_minimum_1(self) -> None:
        """Test that timeout_ms must be at least 1."""
        with pytest.raises(ValidationError):
            _make_golden_path(timeout_ms=0)

        with pytest.raises(ValidationError):
            _make_golden_path(timeout_ms=-1)

    @pytest.mark.unit
    def test_infra_real(self) -> None:
        """Test infra='real' is valid."""
        gp = _make_golden_path(infra="real")
        assert gp.infra == "real"

    @pytest.mark.unit
    def test_infra_mock(self) -> None:
        """Test infra='mock' is valid."""
        gp = _make_golden_path(infra="mock")
        assert gp.infra == "mock"

    @pytest.mark.unit
    def test_infra_invalid(self) -> None:
        """Test that an invalid infra value raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_golden_path(infra="kubernetes")  # type: ignore[arg-type]

    @pytest.mark.unit
    def test_test_file_optional(self) -> None:
        """Test that test_file is optional."""
        gp_no_file = _make_golden_path()
        assert gp_no_file.test_file is None

        gp_with_file = _make_golden_path(
            test_file="tests/golden/test_omn2980_golden.py"
        )
        assert gp_with_file.test_file == "tests/golden/test_omn2980_golden.py"

    @pytest.mark.unit
    def test_correlation_id_fields_are_independent(self) -> None:
        """Test that input and output correlation ID fields are independent."""
        inp = _make_input(input_correlation_id_field="input_trace_id")
        out = _make_output(output_correlation_id_field="output_event_id")
        gp = _make_golden_path(input=inp, output=out)

        assert gp.input.input_correlation_id_field == "input_trace_id"
        assert gp.output.output_correlation_id_field == "output_event_id"

    @pytest.mark.unit
    def test_complete_golden_path(self) -> None:
        """Test creating a fully populated ModelGoldenPath."""
        gp = ModelGoldenPath(
            input=ModelGoldenPathInput(
                topic="onex.cmd.mynode.process.v1",
                fixture="tests/fixtures/golden/process_input.json",
                input_correlation_id_field="correlation_id",
            ),
            output=ModelGoldenPathOutput(
                topic="onex.evt.mynode.processed.v1",
                output_correlation_id_field="correlation_id",
                schema_name="ModelMyNodeOutput",
                assertions=[
                    ModelGoldenPathAssertion(
                        field="status", op="eq", value="completed"
                    ),
                    ModelGoldenPathAssertion(field="data.count", op="gte", value=1),
                    ModelGoldenPathAssertion(
                        field="tags", op="contains", value="golden"
                    ),
                ],
            ),
            timeout_ms=_LONG_TIMEOUT_MS,
            infra="real",
            test_file="tests/golden/test_mynode_golden.py",
        )

        assert gp.input.topic == "onex.cmd.mynode.process.v1"
        assert gp.output.schema_name == "ModelMyNodeOutput"
        assert len(gp.output.assertions) == _EXPECTED_ASSERTIONS_COUNT_3
        assert gp.timeout_ms == _LONG_TIMEOUT_MS
        assert gp.infra == "real"
        assert gp.test_file == "tests/golden/test_mynode_golden.py"

    @pytest.mark.unit
    def test_round_trip_from_yaml_dict(self) -> None:
        """Test that ModelGoldenPath validates from a dict (as parsed from YAML)."""
        yaml_data = {
            "input": {
                "topic": "onex.cmd.mynode.process.v1",
                "fixture": "tests/fixtures/golden/process_input.json",
                "input_correlation_id_field": "correlation_id",
            },
            "output": {
                "topic": "onex.evt.mynode.processed.v1",
                "output_correlation_id_field": "correlation_id",
                "schema_name": None,
                "assertions": [
                    {"field": "status", "op": "eq", "value": "completed"},
                ],
            },
            "timeout_ms": 30000,
            "infra": "real",
            "test_file": None,
        }
        gp = ModelGoldenPath.model_validate(yaml_data)
        assert gp.timeout_ms == _DEFAULT_TIMEOUT_MS
        assert gp.output.schema_name is None
        assert len(gp.output.assertions) == _EXPECTED_ASSERTIONS_COUNT_1

    @pytest.mark.unit
    def test_immutable(self) -> None:
        """Test that ModelGoldenPath is frozen (immutable)."""
        gp = _make_golden_path()
        with pytest.raises((AttributeError, ValidationError)):
            gp.timeout_ms = 99999  # type: ignore[misc]


class TestModelTicketContractWithGoldenPath:
    """Tests for ModelTicketContract with golden_path field."""

    @pytest.mark.unit
    def test_contract_without_golden_path(self) -> None:
        """Test that ModelTicketContract without golden_path (None) remains valid."""
        contract = _make_contract()
        assert contract.golden_path is None

    @pytest.mark.unit
    def test_contract_with_explicit_none_golden_path(self) -> None:
        """Test that ModelTicketContract with golden_path=None is valid."""
        contract = _make_contract(golden_path=None)
        assert contract.golden_path is None

    @pytest.mark.unit
    def test_contract_with_golden_path(self) -> None:
        """Test that ModelTicketContract with a golden_path is valid."""
        gp = _make_golden_path()
        contract = _make_contract(golden_path=gp)
        assert contract.golden_path is not None
        assert contract.golden_path.timeout_ms == _DEFAULT_TIMEOUT_MS

    @pytest.mark.unit
    def test_contract_with_full_golden_path(self) -> None:
        """Test ModelTicketContract with a fully populated golden_path."""
        gp = ModelGoldenPath(
            input=ModelGoldenPathInput(
                topic="onex.cmd.myservice.action.v1",
                fixture="tests/fixtures/golden/action.json",
            ),
            output=ModelGoldenPathOutput(
                topic="onex.evt.myservice.action_completed.v1",
                schema_name="ModelActionResult",
                assertions=[
                    ModelGoldenPathAssertion(
                        field="status", op="eq", value="completed"
                    ),
                ],
            ),
            timeout_ms=_FAST_TIMEOUT_MS,
            infra="mock",
            test_file="tests/golden/test_action_golden.py",
        )
        contract = _make_contract(golden_path=gp)

        assert contract.golden_path is not None
        assert contract.golden_path.infra == "mock"
        assert contract.golden_path.timeout_ms == _FAST_TIMEOUT_MS
        assert contract.golden_path.output.schema_name == "ModelActionResult"

    @pytest.mark.unit
    def test_contract_round_trip_without_golden_path(self) -> None:
        """Test that contract without golden_path round-trips through model_validate."""
        contract = _make_contract()
        data = contract.model_dump()
        restored = ModelTicketContract.model_validate(data)
        assert restored == contract
        assert restored.golden_path is None

    @pytest.mark.unit
    def test_contract_round_trip_with_golden_path(self) -> None:
        """Test that contract with golden_path round-trips through model_validate."""
        gp = ModelGoldenPath(
            input=ModelGoldenPathInput(
                topic="onex.cmd.svc.evt.v1",
                fixture="tests/fixtures/golden/evt.json",
            ),
            output=ModelGoldenPathOutput(
                topic="onex.evt.svc.result.v1",
                assertions=[
                    ModelGoldenPathAssertion(field="status", op="neq", value="failed"),
                ],
            ),
        )
        contract = _make_contract(golden_path=gp)
        data = contract.model_dump()
        restored = ModelTicketContract.model_validate(data)
        assert restored == contract
        assert restored.golden_path is not None
        assert restored.golden_path.timeout_ms == _DEFAULT_TIMEOUT_MS
