# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Input model for contract dependency computation."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ModelDbTableRef(BaseModel):
    """A database table reference with access mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    access: str  # "read", "write", "read_write"


class ModelContractEntry(BaseModel):
    """A single contract's declared protocol surfaces."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str
    node_name: str
    subscribe_topics: list[str]
    publish_topics: list[str]
    protocols: list[str]  # EnumInterfaceSurface values
    db_tables: list[ModelDbTableRef] = []


class ModelContractDependencyInput(BaseModel):
    """Input to the contract dependency compute node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: list[ModelContractEntry]
    repo_filter: list[str] = []  # empty = all repos
