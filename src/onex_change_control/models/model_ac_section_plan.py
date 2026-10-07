# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18270 — what a criteria-section serialization would do to one ticket.

Returned by ``plan_acceptance_criteria_update`` in
:mod:`onex_change_control.serialization.ac_section` before anything is
written, so the dry run and the apply compute the SAME body from the same
call and an apply cannot drift from the diff that was reviewed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ModelAcSectionPlan(BaseModel):
    """The proposed new ticket body, and whether it differs from the old one.

    ``changed`` is the write predicate. It is computed by comparing bodies, not
    by noticing that a render happened: re-running on an unchanged model has to
    be a no-op, because a sweep that rewrote an identical section would mint a
    Linear revision per pass and make every ticket look edited on a day nobody
    edited one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: str = Field(description="The ticket whose body this plan describes")
    old_body: str = Field(description="The body as read, before any change")
    new_body: str = Field(description="The body to write, with the section merged in")
    section: str = Field(description="The rendered managed section, on its own")
    labels: tuple[str, ...] = Field(
        default=(),
        description="Canonical labels rendered, in contract declaration order",
    )
    bound_labels: tuple[str, ...] = Field(
        default=(),
        description="Canonical labels the contract's dod_evidence binds",
    )
    changed: bool = Field(
        description="True when new_body differs from old_body and a write is owed"
    )


__all__ = ["ModelAcSectionPlan"]
