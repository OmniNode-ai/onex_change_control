# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Wire type for worker contract (OMN-8408).

Defines the machine-readable contract every spawned Agent (worker) gets at
spawn time. Parallel to ``ModelOvernightContract`` (session-level) and
``ModelSessionContract`` (pipeline-level); this is the per-worker tier.

Used by:

- ``HandlerOvernight`` tick loop — enforces ``heartbeat_interval_seconds`` and
  ``stall_action`` per worker (OMN-8409).
- Task store CAS semantics — ``lease_seconds`` governs claim leases
  (OMN-8414).
- PreToolUse evidence hook — ``required_evidence`` rejects TaskUpdate calls
  that would mark a task completed without the declared evidence (OMN-8410).
- Snapshot writer — ``snapshot_on_tick`` opts a worker into per-tick state
  snapshots (OMN-8412).
- Runbook auto-invocation — ``applicable_runbooks`` lists slugs the overseer
  will match against observed events (OMN-8413).
"""

from __future__ import annotations

import re as _re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.functional_validators import AfterValidator


def _validate_semver(v: str) -> str:
    if not _re.fullmatch(r"\d+\.\d+\.\d+", v):
        msg = f"schema_version must match x.y.z, got {v!r}"
        raise ValueError(msg)
    return v


_SemVer = Annotated[str, AfterValidator(_validate_semver)]


class ModelEvidenceRequirement(BaseModel):
    """A single piece of evidence required on a task-status transition.

    The matcher (OMN-8410) reads the TaskUpdate body and tests each
    requirement against it. ``kind`` chooses the comparison:

    - ``contains``: ``pattern`` is a substring the body must contain
      (case-sensitive).
    - ``regex``: ``pattern`` is a regular expression matched against the body.
    - ``fenced_block``: the body must contain a fenced code block whose
      language tag matches ``pattern`` (e.g. ``pattern="json"``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    description: str
    kind: Literal["contains", "regex", "fenced_block"]
    pattern: str


class ModelWorkerContract(BaseModel):
    """Per-worker machine-readable contract.

    Loaded at worker spawn time from a ``contract.yaml`` beside the worker
    definition. The parsed model is embedded in the worker's spawn metadata
    so downstream subscribers (overseer tick loop, PreToolUse hooks, task
    store) can read invariants without another disk hop.

    All fields are immutable (``frozen=True``) and unknown fields are
    rejected (``extra="forbid"``). This mirrors the other overseer wire
    types so drift between contracts is a validation error, not a silent
    regression.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: _SemVer = "1.0.0"
    worker_name: str

    heartbeat_interval_seconds: int = Field(
        default=300,
        gt=0,
        description="Max silence (no TaskUpdate or SendMessage) before stall fires.",
    )
    stall_action: Literal["kill_and_respawn", "kill_only", "warn_only"] = (
        "kill_and_respawn"
    )

    required_evidence: Mapping[str, tuple[ModelEvidenceRequirement, ...]] = Field(
        default_factory=lambda: MappingProxyType({}),
        description=(
            "Map from TaskUpdate status transition (e.g. 'completed', "
            "'in_progress') to the list of evidence requirements that must "
            "be satisfied on the TaskUpdate body."
        ),
    )

    @field_validator("required_evidence", mode="before")
    @classmethod
    def _freeze_required_evidence(
        cls, value: Any
    ) -> Mapping[str, tuple[ModelEvidenceRequirement, ...]]:
        if value is None:
            return MappingProxyType({})
        if not isinstance(value, Mapping):
            msg = "required_evidence must be a mapping"
            raise TypeError(msg)
        coerced: dict[str, tuple[ModelEvidenceRequirement, ...]] = {}
        for k, v in value.items():
            if not isinstance(v, (list, tuple)):
                msg = f"required_evidence[{k!r}] must be a list, got {type(v).__name__}"
                raise TypeError(msg)
            items: list[ModelEvidenceRequirement] = []
            for item in v:
                if isinstance(item, ModelEvidenceRequirement):
                    items.append(item)
                elif isinstance(item, dict):
                    items.append(ModelEvidenceRequirement.model_validate(item))
                else:
                    msg = (
                        f"required_evidence[{k!r}] items must be dicts or "
                        f"ModelEvidenceRequirement, got {type(item).__name__}"
                    )
                    raise TypeError(msg)
            coerced[k] = tuple(items)
        return MappingProxyType(coerced)

    allowed_skills: tuple[str, ...] | Literal["*"] = Field(
        default="*",
        description=(
            "Skill slugs this worker may invoke via the Skill tool. '*' "
            "means no restriction. An empty tuple means no skills allowed."
        ),
    )
    allowed_tools: tuple[str, ...] | Literal["*"] = Field(
        default="*",
        description=(
            "Tool names this worker may invoke. '*' means no restriction. "
            "An empty tuple means no tools allowed (unusual)."
        ),
    )

    applicable_runbooks: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Runbook slugs the overseer should match against observed "
            "events for this worker. See OMN-8413."
        ),
    )
    preflight_gates: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Preflight check names that must pass before the worker's "
            "first tool call (e.g. 'reality_check', 'branch_clean')."
        ),
    )

    snapshot_on_tick: bool = Field(
        default=False,
        description=(
            "If true, HandlerOvernight writes a state snapshot for this "
            "worker on every tick. See OMN-8412."
        ),
    )
    lease_seconds: int = Field(
        default=900,
        gt=0,
        description=(
            "Claim lease duration for task ownership. TaskUpdate owner= "
            "becomes a compare-and-swap with this TTL. See OMN-8414."
        ),
    )


def load_worker_contract(data: Mapping[str, Any]) -> ModelWorkerContract:
    """Validate a ``ModelWorkerContract`` from an already-parsed mapping.

    Callers are responsible for reading and parsing the ``contract.yaml``
    file (typically via ``yaml.safe_load``) and passing the resulting
    mapping here. Keeping the parse step outside this module preserves
    omnibase_compat's zero-runtime-dep invariant — the structural package
    depends only on pydantic and typing-extensions.

    Raises ``pydantic.ValidationError`` if the content does not validate,
    and ``TypeError`` if ``data`` is not a mapping.
    """
    if not isinstance(data, Mapping):
        msg = f"worker contract data must be a mapping, got {type(data).__name__}"
        raise TypeError(msg)
    return ModelWorkerContract.model_validate(dict(data))


__all__ = [
    "ModelEvidenceRequirement",
    "ModelWorkerContract",
    "load_worker_contract",
]
