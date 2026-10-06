# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Imperative-contract-guard allowlist entry model (OMN-11878).

A single reviewed entry in an ``allowlists/<repo>.yaml`` ``allowlisted_handlers:``
list — pre-existing imperative-contract debt (``missing_handler_routing``,
``logic_in_node``, ``freestanding_*``, etc.) that the guard permits only because
it is bound to a real tracking ticket. The ``ticket`` field's ``OMN-\\d+``
pattern is the fail-closed mechanism: a placeholder value such as
``'# migration pending'`` fails validation rather than being silently accepted,
so debt can never be baselined without a durable Linear ticket owning it.
"""

from pydantic import BaseModel, ConfigDict, Field


class ModelAllowlistedHandler(BaseModel):
    """One reviewed imperative-contract-guard allowlist entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(
        ...,
        min_length=1,
        description="Repo-relative path of the allowlisted handler/module.",
    )
    violations: list[str] = Field(
        ...,
        min_length=1,
        description="Violation type(s) allowlisted for this path.",
    )
    ticket: str = Field(
        ...,
        pattern=r"^OMN-\d+$",
        description=(
            "The tracking ticket that owns migrating this entry off the "
            "allowlist. Must be a real OMN-#### id — never a placeholder."
        ),
    )
    note: str | None = Field(
        default=None,
        description="Optional human-readable context for the entry.",
    )
