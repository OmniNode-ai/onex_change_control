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

**The honest limit.** The evidence closer in ``omnibase_infra`` does not read
this field. Until it does, a retirement landed here corrects the authoring
record and the binding gate's verdict, and the closer goes on reading the
original ``binds_ac``. That gap is named rather than papered over; closing it is
a change in that repository.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: The same closed label shape ``ModelAcBinding`` enforces. A retirement that
#: could name a label the binding record could not would be unable to match it.
_AC_LABEL_RE = re.compile(r"^(AC|DOD)[-_ .]?(\d+)([a-zA-Z]?)$", re.IGNORECASE)

_MAX_ID_LENGTH = 50
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


__all__ = ["ModelAcBindingRetirement"]
