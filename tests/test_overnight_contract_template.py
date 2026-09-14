# SPDX-License-Identifier: MIT
"""Round-trip test for overnight_contract.template.yaml phase_name values.

Guards against OMN-8370 regression: template phase_name values must be the
lowercase EnumPhase values, because HandlerOvernight compares against
`phase.value` (lowercase). Uppercase values silently fail the comparator
and phases are skipped with no error.
"""

from pathlib import Path

import yaml

# Hardcoded to avoid cross-repo import; mirrors EnumPhase in
# omnimarket/src/omnimarket/nodes/node_overnight/handlers/handler_overnight.py.
# If EnumPhase gains a new value, update this set (and ideally the template).
VALID_PHASE_VALUES = {
    "nightly_loop_controller",
    "build_loop_orchestrator",
    "merge_sweep",
    "ci_watch",
    "platform_readiness",
}

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "templates"
    / "overnight_contract.template.yaml"
)


def test_overnight_template_phase_names_match_enum_phase() -> None:
    data = yaml.safe_load(TEMPLATE_PATH.read_text())
    phases = data.get("phases", [])
    assert phases, "template must declare at least one phase"
    for phase_spec in phases:
        phase_name = phase_spec.get("phase_name")
        assert phase_name in VALID_PHASE_VALUES, (
            f"phase_name {phase_name!r} not in EnumPhase values: "
            f"{sorted(VALID_PHASE_VALUES)}. HandlerOvernight compares "
            f"against lowercase EnumPhase.value — uppercase silently skips."
        )


def test_overnight_template_phase_names_are_lowercase() -> None:
    data = yaml.safe_load(TEMPLATE_PATH.read_text())
    for phase_spec in data.get("phases", []):
        phase_name = phase_spec.get("phase_name", "")
        assert phase_name == phase_name.lower(), (
            f"phase_name {phase_name!r} must be lowercase to match EnumPhase.value"
        )
