# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression tests for OMN-11719 promotion bot policy."""

from __future__ import annotations

import pytest

from onex_change_control.overseer import (
    DEFAULT_PROMOTION_BOT_POLICY,
    OMNINODE_PROMOTION_REPOS,
    EnumPromotionBotAction,
    ModelPromotionBotPolicy,
)


def test_default_policy_matches_dev_to_main_identity_contract() -> None:
    policy = DEFAULT_PROMOTION_BOT_POLICY

    assert policy.identity == "nightly-promotion-bot"
    assert policy.verifier_identity == "nightly-promotion-bot@occ"
    assert policy.source_branch == "dev"
    assert policy.target_branch == "main"
    assert policy.allowed_repos == OMNINODE_PROMOTION_REPOS
    assert len(policy.allowed_repos) == 13


def test_default_policy_allows_only_promotion_actions() -> None:
    policy = DEFAULT_PROMOTION_BOT_POLICY

    assert policy.is_action_permitted(EnumPromotionBotAction.CREATE_PROMOTION_PR)
    assert policy.is_action_permitted(EnumPromotionBotAction.MERGE_PROMOTION_PR)
    assert policy.is_action_permitted(EnumPromotionBotAction.TAG_PROMOTION_BATCH)
    assert policy.is_action_permitted(EnumPromotionBotAction.PUBLISH_DEV_PACKAGE)
    assert policy.is_action_permitted(EnumPromotionBotAction.FILE_OCC_EVIDENCE)

    assert not policy.is_action_permitted(EnumPromotionBotAction.DIRECT_PUSH_MAIN)
    assert not policy.is_action_permitted(EnumPromotionBotAction.FORCE_PUSH)
    assert not policy.is_action_permitted(EnumPromotionBotAction.DELETE_BRANCH)
    assert not policy.is_action_permitted(
        EnumPromotionBotAction.MODIFY_BRANCH_PROTECTION
    )
    assert not policy.is_action_permitted(EnumPromotionBotAction.SKIP_GATE)


def test_default_policy_promotes_only_dev_to_main_in_allowed_repos() -> None:
    policy = DEFAULT_PROMOTION_BOT_POLICY

    assert policy.can_promote("omnibase_core", "dev", "main")
    assert policy.can_promote("onex-self-extending-agent", "dev", "main")
    assert not policy.can_promote("archived-repo", "dev", "main")
    assert not policy.can_promote("omnibase_core", "main", "dev")
    assert not policy.can_promote("omnibase_core", "feature/OMN-1", "main")


def test_policy_rejects_overlapping_allow_and_deny_actions() -> None:
    with pytest.raises(ValueError, match="overlaps"):
        ModelPromotionBotPolicy(
            allowed_actions=frozenset({EnumPromotionBotAction.SKIP_GATE}),
            denied_actions=frozenset({EnumPromotionBotAction.SKIP_GATE}),
        )


def test_policy_rejects_same_source_and_target_branch() -> None:
    with pytest.raises(ValueError, match="must differ"):
        ModelPromotionBotPolicy(source_branch="main", target_branch="main")
