# SPDX-License-Identifier: MIT
"""Round-trip test for overnight_contract.template.yaml phase_name values.

Guards against OMN-8370 regression: template phase_name values must be the
lowercase EnumPhase values, because HandlerOvernight compares against
`phase.value` (lowercase). Uppercase values silently fail the comparator
and phases are skipped with no error.

OMN-16191: the template's schema target, ModelOvernightContract, was an
OCC-local duplicate that omnibase_core commit f49b433ac4 (OMN-11225) merged
into ModelSessionContract before OCC's copy was ever deleted here. The
template's shape already matches ModelSessionContract's field set (no OCC-only
fields), so re-pointing is schema-compatible; the round-trip test below proves
it by substituting the ``<UUID>``/``<ISO8601>`` placeholders with concrete
values and validating the result against the canonical omnibase_core model.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import yaml
from pydantic import ValidationError

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


def test_overnight_template_validates_against_model_session_contract() -> None:
    """OMN-16191: the template must validate against the canonical model.

    Substitutes the ``<UUID>``/``<ISO8601>`` generator placeholders with
    concrete values (as ``onex create-overnight-contract`` would at generation
    time) and validates the result against
    ``omnibase_core.models.overseer.model_session_contract.ModelSessionContract``
    — the class that superseded OCC's deleted local ``ModelOvernightContract``.
    """
    from omnibase_core.models.overseer.model_session_contract import (
        ModelSessionContract,
    )

    data = yaml.safe_load(TEMPLATE_PATH.read_text())
    data["session_id"] = str(uuid4())
    data["created_at"] = datetime.now(UTC).isoformat()

    try:
        contract = ModelSessionContract.model_validate(data)
    except ValidationError as exc:  # pragma: no cover - failure path documents itself
        msg = (
            "overnight_contract.template.yaml no longer validates against "
            f"ModelSessionContract: {exc}"
        )
        raise AssertionError(msg) from exc

    assert len(contract.phases) == len(data["phases"])
    assert {c.condition_id for c in contract.halt_conditions} == {
        h["condition_id"] for h in data["halt_conditions"]
    }
