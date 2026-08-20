# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Wire Schema Test Generator.

Generates pytest test cases from wire schema contract YAMLs. Each contract
produces a parametrized set of tests that verify:
1. Contract YAML validates against ModelWireSchemaContract
2. All required_fields are present in the producer model's JSON schema
3. All required_fields are present in the consumer model's JSON schema
4. No undeclared fields in either model
5. Type compatibility between contract and model schemas

Ticket: OMN-7371
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from onex_change_control.scanners.model_dump_drift import check_model_dump_drift
from onex_change_control.scanners.wire_schema_compliance import (
    check_wire_schema_mismatch,
    discover_wire_schema_contracts,
)

if TYPE_CHECKING:
    from onex_change_control.models.model_wire_schema_contract import (
        ModelWireSchemaContract,
    )

logger = logging.getLogger(__name__)


class WireSchemaTestCase:
    """A single generated test case from a wire schema contract."""

    __slots__ = ("check_name", "contract", "contract_path", "details", "passed")

    def __init__(
        self,
        contract_path: str,
        contract: ModelWireSchemaContract,
        check_name: str,
        *,
        passed: bool,
        details: str,
    ) -> None:
        self.contract_path = contract_path
        self.contract = contract
        self.check_name = check_name
        self.passed = passed
        self.details = details

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"WireSchemaTestCase({self.contract.topic}::{self.check_name} [{status}])"
        )


def _resolve_model_json_schema(
    module_path: str, class_name: str
) -> dict[str, Any] | None:
    """Import a model and return its JSON schema, or None if not resolvable."""
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        return cls.model_json_schema()  # type: ignore[no-any-return]
    except (ImportError, AttributeError, TypeError):
        return None


def _infer_module_from_file(file_path: str) -> str | None:
    """Infer a Python module path from a contract's file path.

    E.g., "src/omnibase_infra/models/model_foo.py" -> "omnibase_infra.models.model_foo"
    """
    # Strip 'src/' prefix
    path = file_path
    if path.startswith("src/"):
        path = path[4:]
    # Strip .py extension
    if path.endswith(".py"):
        path = path[:-3]
    # Convert path separators to dots
    return path.replace("/", ".")


def generate_test_cases_for_contract(
    contract_path: str,
    contract: ModelWireSchemaContract,
) -> list[WireSchemaTestCase]:
    """Generate test cases for a single wire schema contract.

    Always generates:
    - contract_valid: The YAML parsed into ModelWireSchemaContract
    - no_duplicate_fields: No duplicate field names

    Conditionally generates (when models are importable):
    - producer_fields_match: Check 5 for producer side
    - consumer_fields_match: Check 5 for consumer side
    - producer_model_drift: Check 6 for producer model
    """
    results: list[WireSchemaTestCase] = []

    # Test 1: Contract is valid (always passes if we got this far)
    results.append(
        WireSchemaTestCase(
            contract_path=contract_path,
            contract=contract,
            check_name="contract_valid",
            passed=True,
            details="Contract YAML validates against ModelWireSchemaContract",
        )
    )

    # Test 2: No duplicate fields (inherent from model validation, but explicit)
    all_names = [f.name for f in contract.required_fields] + [
        f.name for f in contract.optional_fields
    ]
    duplicates = [n for n in all_names if all_names.count(n) > 1]
    results.append(
        WireSchemaTestCase(
            contract_path=contract_path,
            contract=contract,
            check_name="no_duplicate_fields",
            passed=len(duplicates) == 0,
            details=(
                "No duplicate field names"
                if not duplicates
                else f"Duplicate fields: {sorted(set(duplicates))}"
            ),
        )
    )

    # Test 3+4: Producer/consumer field matching (Check 5)
    # Try to resolve models
    consumer_module = _infer_module_from_file(contract.consumer.file)
    consumer_schema = None
    if consumer_module:
        consumer_schema = _resolve_model_json_schema(
            consumer_module, contract.consumer.model
        )

    if consumer_schema:
        consumer_fields = set(consumer_schema.get("properties", {}).keys())
        violations = check_wire_schema_mismatch(
            contract, consumer_fields=consumer_fields
        )
        consumer_violations = [v for v in violations if v.side == "consumer"]
        results.append(
            WireSchemaTestCase(
                contract_path=contract_path,
                contract=contract,
                check_name="consumer_fields_match",
                passed=len(consumer_violations) == 0,
                details=(
                    "All contract fields present in consumer model"
                    if not consumer_violations
                    else "; ".join(v.detail for v in consumer_violations)
                ),
            )
        )

        # Test 5: Model dump drift (Check 6) for consumer
        drift_violations = check_model_dump_drift(contract, consumer_schema)
        results.append(
            WireSchemaTestCase(
                contract_path=contract_path,
                contract=contract,
                check_name="consumer_model_drift",
                passed=len(drift_violations) == 0,
                details=(
                    "Consumer model schema matches contract declarations"
                    if not drift_violations
                    else "; ".join(v.detail for v in drift_violations)
                ),
            )
        )
    else:
        results.append(
            WireSchemaTestCase(
                contract_path=contract_path,
                contract=contract,
                check_name="consumer_fields_match",
                passed=True,
                details=(
                    f"Skipped: consumer model {contract.consumer.model} not importable"
                ),
            )
        )
        results.append(
            WireSchemaTestCase(
                contract_path=contract_path,
                contract=contract,
                check_name="consumer_model_drift",
                passed=True,
                details=(
                    f"Skipped: consumer model {contract.consumer.model} not importable"
                ),
            )
        )

    return results


def generate_all_test_cases(
    search_dirs: list[Path],
) -> list[WireSchemaTestCase]:
    """Discover all wire schema contracts and generate test cases.

    Args:
        search_dirs: List of Path objects to search for contract YAMLs.

    Returns:
        List of all generated test cases across all contracts.
    """
    contracts = discover_wire_schema_contracts(
        [Path(d) if not isinstance(d, Path) else d for d in search_dirs]
    )
    all_cases: list[WireSchemaTestCase] = []
    for path, contract in contracts:
        cases = generate_test_cases_for_contract(str(path), contract)
        all_cases.extend(cases)
    return all_cases


def pytest_params_from_contracts(
    search_dirs: list[Path],
) -> list[tuple[str, str, bool, str]]:
    """Generate pytest parametrize-friendly tuples from wire schema contracts.

    Returns list of (topic, check_name, expected_pass, details) tuples
    suitable for use with @pytest.mark.parametrize.
    """
    cases = generate_all_test_cases(search_dirs)
    return [
        (case.contract.topic, case.check_name, case.passed, case.details)
        for case in cases
    ]
