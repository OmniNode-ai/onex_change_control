# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""CLI entry point for contract drift analysis.

Usage:
    check-drift --baseline baseline.yaml --current current.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from onex_change_control.handlers.handler_drift_analysis import (
    analyze_drift,
    compute_canonical_hash,
)
from onex_change_control.models.model_contract_drift_input import (
    ModelContractDriftInput,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict."""
    with path.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        print(f"Error: {path} does not contain a YAML mapping", file=sys.stderr)
        sys.exit(1)
    return data


def main() -> None:
    """Run drift analysis between baseline and current contract YAML files."""
    parser = argparse.ArgumentParser(
        description="Analyze drift between baseline and current ONEX contracts."
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="Path to the baseline contract YAML file.",
    )
    parser.add_argument(
        "--current",
        type=Path,
        required=True,
        help="Path to the current contract YAML file.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output results as JSON instead of human-readable text.",
    )
    args = parser.parse_args()

    baseline = _load_yaml(args.baseline)
    current = _load_yaml(args.current)

    drift_input = ModelContractDriftInput(
        contract_name=args.current.stem,
        current_contract=current,
        pinned_hash=compute_canonical_hash(baseline),
    )

    result = analyze_drift(drift_input)

    if args.output_json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        print(f"Contract: {result.contract_name}")
        print(f"Drift detected: {result.drift_detected}")
        print(f"Severity: {result.severity.value}")
        if result.field_changes:
            print(f"\nField changes ({len(result.field_changes)}):")
            for change in result.field_changes:
                print(f"  - {change.path}: {change.change_type}")
        if not result.drift_detected:
            print("No drift detected.")

    if result.drift_detected:
        from onex_change_control.kafka.governance_emitter import emit_drift_detected

        emit_drift_detected(
            ticket_id=result.contract_name,
            drift_kind=result.severity.value,
            description=result.summary,
            severity="error" if result.breaking_changes else "warning",
        )

    sys.exit(1 if result.drift_detected else 0)


if __name__ == "__main__":
    main()
