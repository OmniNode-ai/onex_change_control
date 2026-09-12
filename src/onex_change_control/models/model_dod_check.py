# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""OCC-local ticket contract supporting models. OMN-10066

ModelEmergencyBypass and ModelEvidenceRequirement are re-exported from
omnibase_core (OMN-10064) so all consumers share a single class identity.

ModelDodCheck is OCC-local (richer than core's ModelDodEvidenceCheck:
Literal check_type, str | dict check_value, optional cwd) and is
intentionally distinct. ModelDodEvidenceItem references ModelDodCheck.
"""

from __future__ import annotations

import re
from typing import Literal

from omnibase_core.models.ticket.model_emergency_bypass import (
    ModelEmergencyBypass as ModelEmergencyBypass,  # re-export
)
from omnibase_core.models.ticket.model_evidence_requirement import (
    ModelEvidenceRequirement as ModelEvidenceRequirement,  # re-export
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from onex_change_control.models.model_ac_binding import (
    ModelAcBinding,  # noqa: TC001  Why: Pydantic model needs runtime type for field annotation
)

# Security constraints to prevent DoS attacks
_MAX_STRING_LENGTH = 10000
_MAX_LIST_ITEMS = 1000

# OMN-18056. The one label shape a ``binds_ac`` entry may take.
#
# The closer (omnibase_infra ``handler_evidence_autoclose_sweep``) canonicalises
# BOTH sides of its join -- the ticket's criterion text and this field's entries
# -- through a single ``_canonical_ac_label`` built on
# ``^[\s>*_+-]*(?:\*\*)?\s*(AC|DOD)[-_ .]?(\d+)\b``. That regex tolerates
# leading bullet/emphasis debris and trailing prose because the criterion side
# is a free-text markdown bullet.
#
# A ``binds_ac`` entry is not a bullet: it is an author writing a label into a
# machine-read field, so this side is the STRICT half of the same rule --
# ``AC1``, ``ac-1``, ``DoD 2`` and nothing else. The asymmetry is deliberate and
# runs in the safe direction: everything this accepts, the closer's regex also
# canonicalises to the same value, so a contract that passes here cannot fail to
# join. What it refuses is an entry like ``"AC1 -- the gate is wired"``, which
# the closer WOULD read as ``AC1``; refusing it at authoring time turns a
# silently-truncated binding into a named error instead.
_AC_LABEL_ENTRY_RE = re.compile(r"^(AC|DOD)[-_ .]?(\d+)$", re.IGNORECASE)


class ModelDodCheck(BaseModel):
    """A single executable check for a DoD evidence item.

    Each check has a type that determines how check_value is interpreted:
    - test_exists: check_value is a glob pattern for test files
    - test_passes: check_value is a shell command that runs tests -- an
      EXECUTED alias of ``command`` (OMN-16824). Both the hosted Contract
      Compliance Check and node_dod_verify run it under
      ``bash -o pipefail -c`` and read its exit status. It does NOT mean
      "this PR's CI is green"; assert that explicitly with
      ``check_type: command`` + ``gh pr checks`` if you want it.
    - file_exists: check_value is a glob pattern for expected files
    - grep: check_value is a dict with 'pattern' and 'path' keys
    - command: check_value is a shell command (exit 0 = pass)
    - endpoint: check_value is a URL or path to check
    - behavior_proven: historical attestation vocabulary retained for parsing;
      the hosted runner reports it as non-executable WARN evidence
    - semantic_grading: check_value is a receipt path produced by
      node_pr_semantic_grader_llm_effect; the receipt gate requires a
      semantic_grading.yaml receipt at the canonical path for this evidence
      item. Phase 1: ADVISORY status passes; Phase 2 (calibrated): hard fail.

    The optional ``cwd`` field declares the working directory the check should
    execute under. The runner expands ``${OMNI_HOME}``, ``${PR_NUMBER}``,
    ``${REPO}``, and ``${TICKET_ID}`` template tokens before invocation, and
    is responsible for path-traversal containment checks. When ``cwd`` is
    omitted the runner uses the product checkout under test (hosted) or
    inherits its caller's working directory (local).

    OMN-16824: a declared ``cwd`` a runner cannot resolve makes the check
    NOT_EVALUATED -- it is never rerouted to the runner's own workspace, since
    running the command in a different tree answers a different question under
    this entry's name. The hosted gate checks out one repo, so a cross-repo
    ``cwd`` belongs to an item declaring
    ``execution_scope: local_done_gate`` (OMN-15392). Authoring reference:
    the knowledge base's ``dod-check-types.md``
    (https://github.com/OmniNode-ai/knowledge-base/blob/main/reference/dod-check-types.md).

    OMN-10078: replaces the brittle ``cd ${OMNI_HOME}/<repo> && `` shell
    prefix introduced as a temporary fix in OMN-10049 / PR #448.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    check_type: Literal[
        "test_exists",
        "test_passes",
        "file_exists",
        "grep",
        "command",
        "endpoint",
        "behavior_proven",
        "semantic_grading",
    ] = Field(..., description="Type of executable check")
    check_value: str | dict[str, str] = Field(
        ...,
        description="Check-type-specific value (glob, command, URL, or pattern dict)",
    )
    cwd: str | None = Field(
        default=None,
        description=(
            "Optional working directory for the check command. Supports "
            "${OMNI_HOME}, ${PR_NUMBER}, ${REPO}, ${TICKET_ID} template "
            "tokens that the runner substitutes at execution time. When "
            "omitted the runner inherits its caller's cwd."
        ),
        max_length=_MAX_STRING_LENGTH,
    )


class ModelDodEvidenceItem(BaseModel):
    """A single DoD evidence item mapping a requirement to executable checks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(
        ...,
        description="Unique identifier within the contract (e.g., 'dod-001')",
        max_length=50,
    )
    description: str = Field(
        ...,
        description="Human-readable description of the DoD requirement",
        max_length=_MAX_STRING_LENGTH,
    )
    source: Literal["linear", "manual", "generated"] = Field(
        default="generated",
        description="Where this DoD item originated",
    )
    # OMN-18056. WHICH ACCEPTANCE CRITERIA THIS ITEM CLAIMS TO COVER.
    #
    # This model is what the WIRED OCC compliance gate validates every active
    # dod_evidence item against: ci.yml's `contract-compliance-check` job ->
    # scripts/ci/run_contract_compliance_check.py ->
    # contract_compliance_check._validate_dod_item. It is `extra="forbid"`, so
    # before this field existed a contract declaring a binding was rejected
    # WHOLESALE as `INVALID_DOD_EVIDENCE_ITEM -- strict schema rejected
    # field(s): binds_ac` and none of its checks ran. The corresponding core
    # models (`ModelContractDodItem`, `ModelDodEvidenceItem`) carry the same
    # field for the same reason; this one is OCC-local and is a THIRD gate
    # audience, not a copy kept in sync by convention.
    #
    # OPTIONAL AND DEFAULTED, so every existing contract in the corpus parses
    # unchanged. `extra="forbid"` stays -- forbidding what is not declared is
    # the property that made the field necessary, not a defect to route around.
    #
    # HONEST LIMIT, stated rather than implied: this is the AUTHOR'S CLAIM.
    # This model can check that an entry is a well-formed label; it cannot
    # check that the item's checks prove the criterion that label names. What
    # the field removes is the SILENT case -- a criterion nothing even claims
    # to cover -- not authorial error.
    binds_ac: tuple[str, ...] = Field(
        default=(),
        max_length=_MAX_LIST_ITEMS,
        description=(
            "Acceptance-criterion labels (`AC1`, `DoD2`) from the ticket body "
            "that this evidence item claims to prove. Empty means it claims "
            "none, which is a coverage gap rather than a pass."
        ),
    )
    # OMN-18236. THE ACCEPTANCE RECORD AND THE CRITERION REVISION.
    #
    # `binds_ac` above is the author's CLAIM and carries neither of the two
    # facts that make a claim into a binding: that somebody agreed to it, and
    # which revision of the criterion they agreed to. Without the first, a
    # machine-proposed mapping and a reviewed one are indistinguishable, so a
    # proposer could manufacture unreviewed bindings at corpus scale. Without
    # the second, a criterion can be rewritten under a check that is still
    # passing and the binding goes on reading as proof of a sentence that no
    # longer exists.
    #
    # An entry here is pinned to a criterion hash always, and accepted only
    # when it names an actor and a time. A binding with no `accepted_by` is a
    # DRAFT proposal, which is not evidence; a binding whose hash no longer
    # matches the ticket is STALE and reverts to unproven until re-accepted.
    #
    # Every label here must also appear in `binds_ac`, so the consumers that
    # already read that list keep reading ONE declaration rather than two that
    # can disagree.
    ac_bindings: tuple[ModelAcBinding, ...] = Field(
        default=(),
        max_length=_MAX_LIST_ITEMS,
        description=(
            "Per-criterion binding records: the criterion hash each `binds_ac` "
            "label was pinned to, and who accepted it. An entry with no "
            "`accepted_by` is a draft proposal, not evidence."
        ),
    )
    linear_dod_text: str | None = Field(
        default=None,
        description="Original DoD text from Linear, if sourced from Linear",
        max_length=_MAX_STRING_LENGTH,
    )
    checks: list[ModelDodCheck] = Field(
        default_factory=list,
        description="Executable checks that verify this DoD item",
        max_length=_MAX_LIST_ITEMS,
    )
    execution_scope: Literal["hosted_and_local", "local_done_gate"] = Field(
        default="hosted_and_local",
        description=(
            "Gate audience authorized to execute this evidence item. "
            "local_done_gate items are not evaluated by hosted compliance."
        ),
    )
    status: Literal["pending", "verified", "failed", "skipped"] = Field(
        default="pending",
        description="Current verification status of this DoD item",
    )
    evidence_artifact: str | None = Field(
        default=None,
        description="Path to evidence artifact (e.g., test output, screenshot)",
        max_length=_MAX_STRING_LENGTH,
    )

    @field_validator("binds_ac")
    @classmethod
    def _entries_are_acceptance_criterion_labels(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Reject a `binds_ac` entry that is not a well-formed criterion label.

        An entry that does not parse as a label cannot join to anything: the
        closer's canonicaliser returns `""` for it and the binding is silently
        absent from the join, which reads downstream as "this criterion is
        bound by nothing" -- indistinguishable from a contract that never
        declared it. That is the exact silent case OMN-18056 exists to remove,
        so a malformed entry fails the contract instead of degrading into one.

        Entries are NOT normalised here. The value is preserved verbatim so the
        contract keeps saying what its author wrote; canonicalisation stays in
        the single `_canonical_ac_label` the closer applies to both sides of
        the join, because a second normaliser is a second truth.
        """
        malformed = [entry for entry in value if not _AC_LABEL_ENTRY_RE.match(entry)]
        if malformed:
            rendered = ", ".join(repr(entry) for entry in malformed)
            msg = (
                f"binds_ac entries must be acceptance-criterion labels "
                f"(`AC1`, `ac-1`, `DoD2`); rejected: {rendered}"
            )
            raise ValueError(msg)
        return value

    @field_validator("ac_bindings")
    @classmethod
    def _binding_labels_are_unique(
        cls, value: tuple[ModelAcBinding, ...]
    ) -> tuple[ModelAcBinding, ...]:
        """One record per criterion. Two records for one label is ambiguous.

        A second record for the same label lets an accepted binding and a
        stale or draft one sit side by side, and any consumer that stops at
        the first match reports whichever happens to be written first. There
        is no reading of "this criterion is both accepted and not" that is
        safe to pick a side of.
        """
        seen: set[str] = set()
        duplicated: list[str] = []
        for binding in value:
            canonical = binding.label.upper().replace("-", "").replace("_", "")
            canonical = canonical.replace(" ", "").replace(".", "")
            if canonical in seen:
                duplicated.append(binding.label)
            seen.add(canonical)
        if duplicated:
            rendered = ", ".join(repr(label) for label in duplicated)
            msg = f"ac_bindings declares more than one record for: {rendered}"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _every_binding_is_also_claimed(self) -> ModelDodEvidenceItem:
        """A binding record for a criterion the item does not claim is a lie.

        `binds_ac` is what every existing consumer reads. A record here whose
        label is absent from that list would carry an acceptance for a
        criterion the item never claimed, which is not a stricter statement
        than `binds_ac` makes -- it is a different one, invisible to everything
        already reading the claim.
        """

        def _canonical(label: str) -> str:
            folded = label.upper()
            for junk in ("-", "_", " ", "."):
                folded = folded.replace(junk, "")
            return folded

        claimed = {_canonical(entry) for entry in self.binds_ac}
        unclaimed = [
            binding.label
            for binding in self.ac_bindings
            if _canonical(binding.label) not in claimed
        ]
        if unclaimed:
            rendered = ", ".join(repr(label) for label in unclaimed)
            msg = (
                "every ac_bindings label must also appear in binds_ac; these "
                f"are bound but not claimed: {rendered}"
            )
            raise ValueError(msg)
        return self


__all__ = [
    "ModelDodCheck",
    "ModelDodEvidenceItem",
    "ModelEmergencyBypass",
    "ModelEvidenceRequirement",
]
