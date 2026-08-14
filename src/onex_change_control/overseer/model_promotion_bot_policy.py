# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Promotion bot identity and permission policy for dev-to-main promotion."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EnumPromotionBotAction(StrEnum):
    """Actions governed by the nightly promotion bot policy."""

    CREATE_PROMOTION_PR = "create_promotion_pr"
    MERGE_PROMOTION_PR = "merge_promotion_pr"
    TAG_PROMOTION_BATCH = "tag_promotion_batch"
    PUBLISH_DEV_PACKAGE = "publish_dev_package"
    FILE_OCC_EVIDENCE = "file_occ_evidence"
    DIRECT_PUSH_MAIN = "direct_push_main"
    FORCE_PUSH = "force_push"
    DELETE_BRANCH = "delete_branch"
    MODIFY_BRANCH_PROTECTION = "modify_branch_protection"
    SKIP_GATE = "skip_gate"


OMNINODE_PROMOTION_REPOS: frozenset[str] = frozenset(
    {
        "omnibase_compat",
        "omnibase_core",
        "omnibase_spi",
        "omnibase_infra",
        "omnimarket",
        "omniclaude",
        "omniintelligence",
        "omnimemory",
        "omnidash",
        "omniweb",
        "onex_change_control",
        "omninode_infra",
        "onex-self-extending-agent",
    }
)


DEFAULT_PROMOTION_ALLOWED_ACTIONS: frozenset[EnumPromotionBotAction] = frozenset(
    {
        EnumPromotionBotAction.CREATE_PROMOTION_PR,
        EnumPromotionBotAction.MERGE_PROMOTION_PR,
        EnumPromotionBotAction.TAG_PROMOTION_BATCH,
        EnumPromotionBotAction.PUBLISH_DEV_PACKAGE,
        EnumPromotionBotAction.FILE_OCC_EVIDENCE,
    }
)


DEFAULT_PROMOTION_DENIED_ACTIONS: frozenset[EnumPromotionBotAction] = frozenset(
    {
        EnumPromotionBotAction.DIRECT_PUSH_MAIN,
        EnumPromotionBotAction.FORCE_PUSH,
        EnumPromotionBotAction.DELETE_BRANCH,
        EnumPromotionBotAction.MODIFY_BRANCH_PROTECTION,
        EnumPromotionBotAction.SKIP_GATE,
    }
)


class ModelPromotionBotPolicy(BaseModel):
    """Narrow permission envelope for the nightly dev-to-main promotion bot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: str = Field(
        default="nightly-promotion-bot",
        min_length=1,
        description="Stable automation identity that authors promotion work.",
    )
    verifier_identity: str = Field(
        default="nightly-promotion-bot@occ",
        min_length=1,
        description="Identity recorded in OCC promotion receipts.",
    )
    allowed_repos: frozenset[str] = Field(default=OMNINODE_PROMOTION_REPOS)
    source_branch: str = Field(default="dev", min_length=1)
    target_branch: str = Field(default="main", min_length=1)
    allowed_actions: frozenset[EnumPromotionBotAction] = Field(
        default=DEFAULT_PROMOTION_ALLOWED_ACTIONS
    )
    denied_actions: frozenset[EnumPromotionBotAction] = Field(
        default=DEFAULT_PROMOTION_DENIED_ACTIONS
    )

    @model_validator(mode="after")
    def _validate_policy(self) -> ModelPromotionBotPolicy:
        overlap = self.allowed_actions & self.denied_actions
        if overlap:
            overlap_values = ", ".join(sorted(action.value for action in overlap))
            msg = f"promotion bot action policy overlaps: {overlap_values}"
            raise ValueError(msg)
        if self.source_branch == self.target_branch:
            msg = "promotion source and target branches must differ"
            raise ValueError(msg)
        return self

    def is_repo_allowed(self, repo: str) -> bool:
        """Return whether the bot may act on a repository."""
        return repo in self.allowed_repos

    def is_action_permitted(self, action: EnumPromotionBotAction | str) -> bool:
        """Return whether an action is allowed after applying explicit denies."""
        parsed_action = EnumPromotionBotAction(action)
        if parsed_action in self.denied_actions:
            return False
        return parsed_action in self.allowed_actions

    def can_promote(self, repo: str, source_branch: str, target_branch: str) -> bool:
        """Return whether this policy permits a repo promotion PR."""
        return (
            self.is_repo_allowed(repo)
            and source_branch == self.source_branch
            and target_branch == self.target_branch
            and self.is_action_permitted(EnumPromotionBotAction.CREATE_PROMOTION_PR)
        )


DEFAULT_PROMOTION_BOT_POLICY = ModelPromotionBotPolicy()


__all__ = [
    "DEFAULT_PROMOTION_ALLOWED_ACTIONS",
    "DEFAULT_PROMOTION_BOT_POLICY",
    "DEFAULT_PROMOTION_DENIED_ACTIONS",
    "OMNINODE_PROMOTION_REPOS",
    "EnumPromotionBotAction",
    "ModelPromotionBotPolicy",
]
