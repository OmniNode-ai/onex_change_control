# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Loader for the machine-readable OmniNode Deterministic Truth Doctrine clauses."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, field_validator, model_validator

_DEFAULT_CLAUSES_PATH = (
    Path(__file__).parent.parent.parent.parent
    / "docs"
    / "standards"
    / "doctrine_clauses.yaml"
)

CoverageStatus = Literal["enforced", "advisory", "uncovered"]

_REQUIRED_FIELDS = frozenset(
    {"clause_id", "title", "description", "doctrine_section", "check", "coverage"}
)


class DoctrineClause(BaseModel):
    """Machine-readable representation of a single doctrine clause."""

    clause_id: str
    title: str
    description: str
    doctrine_section: str
    check: str
    ci_gate: str | None
    coverage: CoverageStatus

    @field_validator("clause_id")
    @classmethod
    def clause_id_format(cls, v: str) -> str:
        if not v.startswith("DT-"):
            msg = f"clause_id must start with 'DT-', got: {v!r}"
            raise ValueError(msg)
        return v


class _SchemaVersion(BaseModel):
    major: int
    minor: int
    patch: int


class _DoctrineRegistry(BaseModel):
    schema_version: _SchemaVersion
    clauses: list[DoctrineClause]

    @model_validator(mode="after")
    def no_duplicate_ids(self) -> _DoctrineRegistry:
        seen: set[str] = set()
        for clause in self.clauses:
            if clause.clause_id in seen:
                msg = f"Duplicate clause_id: {clause.clause_id!r}"
                raise ValueError(msg)
            seen.add(clause.clause_id)
        return self


def load_doctrine_clauses(path: Path | None = None) -> list[DoctrineClause]:
    """Load and validate doctrine clauses from YAML.

    Validates: no duplicate clause_ids, all required fields present,
    coverage is one of enforced/advisory/uncovered.
    """
    resolved = path if path is not None else _DEFAULT_CLAUSES_PATH
    raw = yaml.safe_load(resolved.read_text())
    registry = _DoctrineRegistry.model_validate(raw)
    return registry.clauses
