# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18577 — withdrawing a binding a merged item cannot be edited to remove.

A ``dod_evidence`` entry is immutable once merged: the required OCC Append-Only
Gate refuses an edit with ``kind: entry_edited`` and tells the author to append
instead. That is correct, and it is also why a WRONG binding had no exit. The
one add-only shape that existed, ``evidence_artifact:
"supersedes_dod_evidence:<id>"``, is resolved by the DoD verifier's evidence
collector and surfaces to the closer as a per-check status. The acceptance-
criterion binding gate never reads it, so a supersession appended beside a wrong
binding left that binding claiming its criteria and passing its check. It would
have looked like a fix and changed nothing — which is why
``onex_change_control#9980`` was closed unmerged rather than merged as a
workaround.

This record is the missing shape, and it is read by the BINDING gate. It names
the item, the criterion, and why. All three are required, because a withdrawal
of evidence that does not say what it withdraws or why is indistinguishable from
a typo, and a consumer checking only that the key is present would honour it
either way.

**It does not delete anything.** The retired binding stays in the contract,
readable, with its acceptance record intact for audit. What changes is that the
gate stops counting it as a claim, so the criterion reverts to unbound and has
to be bound to evidence that can actually settle it.

**Typed and audited (OMN-20157).** The DoD verifier counts every binding no
second lane accepted, including those on superseded items, and until now had no
way to take a withdrawn one out of that count. A retirement therefore also
carries ``reason_kind`` (``superseded_by`` naming the replacing item, or
``no_longer_applicable``), ``retired_by`` and ``retired_at``. They are optional
in this model so retirements already merged still parse; the verifier honours a
retirement only when it carries them, only for a binding that no second lane
accepted, and reports every retirement it applied or refused in its verdict.

**The honest limit.** The evidence closer in ``omnibase_infra`` does not read
this field. Until it does, a retirement landed here corrects the authoring
record and the binding gate's verdict, and the closer goes on reading the
original ``binds_ac``. That gap is named rather than papered over; closing it is
a change in that repository.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: The same closed label shape ``ModelAcBinding`` enforces. A retirement that
#: could name a label the binding record could not would be unable to match it.
_AC_LABEL_RE = re.compile(r"^(AC|DOD)[-_ .]?(\d+)([a-zA-Z]?)$", re.IGNORECASE)

#: RFC 3339 UTC, seconds precision, `Z` suffix -- the one spelling
#: ``ModelAcBinding.accepted_at`` uses, so the two records compare without a parser.
_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_MAX_ID_LENGTH = 50
_MAX_ACTOR_LENGTH = 200
_MAX_REASON_LENGTH = 2000

#: A reason has to carry enough to tell the next reader what was wrong. The
#: floor is deliberately low -- this refuses `"x"` and `"wrong"`, not brevity.
_MIN_REASON_LENGTH = 12


class ModelAcBindingRetirement(BaseModel):
    """One binding withdrawn: which item, which criterion, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item: str = Field(
        ...,
        description=(
            "The `dod_evidence` id whose binding is being retired. It must be "
            "an item this same contract declares, and that item must actually "
            "bind the label below -- a retirement pointing at nothing takes no "
            "effect while reading as a correction that was made."
        ),
        max_length=_MAX_ID_LENGTH,
    )
    label: str = Field(
        ...,
        description=(
            "The acceptance-criterion label being unbound from that item "
            "(`AC1`, `DoD2`). Per criterion, never per item: an item that "
            "correctly binds one criterion and wrongly binds another keeps the "
            "one it earned."
        ),
        max_length=50,
    )
    reason: str = Field(
        ...,
        description=(
            "Why this binding cannot settle this criterion, in the author's "
            "own words. Required, because the next reader's only alternative "
            "is to re-derive the judgement from scratch."
        ),
        min_length=_MIN_REASON_LENGTH,
        max_length=_MAX_REASON_LENGTH,
    )

    # The audit half. These four fields are OPTIONAL here so that every
    # retirement already merged in the corpus (item, label, reason only) still
    # parses, and REQUIRED BY THE DoD VERIFIER before it will honour a
    # retirement: node_dod_verify counts every unaccepted binding on a ticket
    # and takes a retirement out of that count only when it names who withdrew
    # the binding, when, and one of the two typed reasons below.
    reason_kind: Literal["superseded_by", "no_longer_applicable"] | None = Field(
        default=None,
        description=(
            "Why the binding is withdrawn, as a closed choice: `superseded_by` "
            "(another item in this contract now binds the label, named in "
            "`superseded_by`) or `no_longer_applicable` (nothing replaces it; "
            "`reason` explains why the criterion no longer needs this binding)."
        ),
    )
    superseded_by: str | None = Field(
        default=None,
        description=(
            "The `dod_evidence` id of the item that replaces the retired "
            "binding. Required exactly when `reason_kind` is `superseded_by`."
        ),
        max_length=_MAX_ID_LENGTH,
    )
    retired_by: str | None = Field(
        default=None,
        description=(
            "Who withdrew the binding: the lane or person that wrote this entry."
        ),
        max_length=_MAX_ACTOR_LENGTH,
    )
    retired_at: str | None = Field(
        default=None,
        description="When it was withdrawn, RFC 3339 UTC to the second.",
    )

    @field_validator("label")
    @classmethod
    def _label_is_an_acceptance_criterion_label(cls, value: str) -> str:
        if not _AC_LABEL_RE.match(value):
            msg = (
                "label must be an acceptance-criterion label (`AC1`, `ac-1`, "
                f"`DoD2`, `AC2b`); rejected: {value!r}"
            )
            raise ValueError(msg)
        return value

    @field_validator("reason")
    @classmethod
    def _reason_is_not_whitespace(cls, value: str) -> str:
        if not value.strip():
            msg = "reason must not be blank"
            raise ValueError(msg)
        return value

    @field_validator("retired_at")
    @classmethod
    def _retired_at_is_a_utc_timestamp(cls, value: str | None) -> str | None:
        if value is not None and not _UTC_TIMESTAMP_RE.match(value):
            msg = (
                "retired_at must be RFC 3339 UTC to the second "
                f"(`2026-10-08T16:20:57Z`); rejected: {value!r}"
            )
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _the_typed_reason_is_whole_or_absent(self) -> ModelAcBindingRetirement:
        """A typed reason names its replacement; an audit names both actor and time.

        `superseded_by` without a `superseded_by` item points at nothing, and a
        `no_longer_applicable` that names a replacement contradicts itself.
        An actor with no time (or a time with no actor) cannot be audited.
        """
        if self.reason_kind == "superseded_by" and not self.superseded_by:
            msg = (
                "reason_kind superseded_by requires `superseded_by`, the replacing item"
            )
            raise ValueError(msg)
        if self.reason_kind != "superseded_by" and self.superseded_by:
            msg = "`superseded_by` is only valid with reason_kind superseded_by"
            raise ValueError(msg)
        if self.reason_kind is None and (self.retired_by or self.retired_at):
            msg = "retired_by and retired_at are only valid with a typed reason_kind"
            raise ValueError(msg)
        if bool(self.retired_by) != bool(self.retired_at):
            msg = "retired_by and retired_at are recorded together or not at all"
            raise ValueError(msg)
        return self


__all__ = ["ModelAcBindingRetirement"]
