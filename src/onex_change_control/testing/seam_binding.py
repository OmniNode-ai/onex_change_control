# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Dual-binding seam harness — P5 + P6 of the canonical contract shape v1.

OMN-15669, operator ruling R-0802-9 (2026-08-02).

Two things live here, and nothing else:

``assert_seam_shape``
    The ONE validation entrypoint a seam case executes. Both the mock fixture
    and the real dependency's payload are validated against the SAME
    ``seam_schema`` ref declared in the contract, so a mock cannot drift into a
    shape the real dependency never produces (the OMN-15598 class). This is why
    the gate requires the literal ``assert_seam_shape(`` in a seam case file:
    citing a schema in a docstring is not validation.

``SEAM_BINDINGS`` / ``binding_params``
    The single parameterized fixture axis. A case declared ``bindings: both``
    is parameterized over ``("mock", "real")`` — ONE test body, two bindings.
    Duplicating the body into a separate integration test is the anti-pattern
    this axis exists to forbid: two bodies drift, one body cannot. The
    real-bound leg carries ``pytest.mark.integration``, which is the existing
    OCC golden-chain convention (``tests/test_golden_chain_*.py`` mock-bound at
    a named boundary, ``tests/integration/`` real-bound) — no parallel
    machinery is introduced.

Divergence between the mock-bound and the real-bound run of the SAME case is
itself a reportable seam defect, not a flaky test.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from jsonschema import Draft202012Validator

__all__ = [
    "SEAM_BINDINGS",
    "SeamShapeError",
    "assert_seam_shape",
    "binding_params",
    "resolve_seam_schema",
]

#: The one axis. Order is stable so collected node ids are stable.
SEAM_BINDINGS: Final[tuple[str, str]] = ("mock", "real")


class SeamShapeError(AssertionError):
    """A payload did not match the seam schema its contract declared."""


def binding_params(
    declared: str = "both", *, real_marks: tuple[Any, ...] = ()
) -> list[Any]:
    """Return pytest params for the dual-binding axis of a declared case.

    ``declared`` is the contract's own ``bindings`` value, so the parameterized
    axis and the declaration cannot drift: the gate reads the collected param
    ids back and fails when they disagree.

    ``real`` carries ``pytest.mark.integration`` (plus any extra marks) so the
    real-bound leg deselects cleanly with ``-m 'not integration'`` for the fast
    pre-PR run while remaining the SAME test body.
    """
    if declared not in {"mock", "real", "both"}:
        msg = f"bindings must be one of mock|real|both, got {declared!r}"
        raise ValueError(msg)
    params: list[Any] = []
    if declared in {"mock", "both"}:
        params.append(pytest.param("mock", id="mock"))
    if declared in {"real", "both"}:
        params.append(
            pytest.param(
                "real", id="real", marks=(pytest.mark.integration, *real_marks)
            )
        )
    return params


def resolve_seam_schema(seam_schema: str, root: Path | None = None) -> dict[str, Any]:
    """Resolve a contract's ``seam_schema`` ref to a JSON Schema mapping.

    Two accepted forms, both of which the gate independently proves resolvable:
      * a repo-relative path to a YAML/JSON schema file;
      * an importable dotted path to a pydantic model (``.model_json_schema()``).
    """
    base = root or Path.cwd()
    candidate = base / seam_schema
    if candidate.exists():
        loaded = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            msg = f"seam schema file is not a mapping: {seam_schema}"
            raise SeamShapeError(msg)
        return loaded
    module_name, _, attribute = seam_schema.rpartition(".")
    if not module_name:
        msg = f"seam schema ref does not resolve: {seam_schema}"
        raise SeamShapeError(msg)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        msg = f"seam schema ref does not resolve: {seam_schema} ({exc})"
        raise SeamShapeError(msg) from exc
    model = getattr(module, attribute, None)
    if model is None or not hasattr(model, "model_json_schema"):
        msg = f"seam schema ref is not a pydantic model: {seam_schema}"
        raise SeamShapeError(msg)
    schema = model.model_json_schema()
    if not isinstance(schema, dict):  # pragma: no cover - pydantic contract
        msg = f"seam schema ref produced no schema: {seam_schema}"
        raise SeamShapeError(msg)
    return schema


def assert_seam_shape(
    payload: Any,
    seam_schema: str,
    *,
    binding: str = "unspecified",
    root: Path | None = None,
) -> None:
    """Validate ``payload`` against the contract-declared seam schema.

    Called with the mock payload on the mock-bound leg and with the real
    dependency's payload on the real-bound leg — the same schema both times.
    That symmetry is the mechanism: a mock whose shape the real dependency
    could never produce fails here, in the fast leg, before any infra runs.
    """
    schema = resolve_seam_schema(seam_schema, root)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.path)
    )
    if errors:
        rendered = "\n".join(
            f"  - {'/'.join(str(p) for p in e.path) or '(root)'}: {e.message}"
            for e in errors
        )
        msg = (
            f"seam payload does not match {seam_schema} on the {binding!r} "
            f"binding:\n{rendered}\n"
            "Mock and real payloads validate against the SAME schema — a "
            "failure here on the mock leg IS the seam defect, caught early."
        )
        raise SeamShapeError(msg)
