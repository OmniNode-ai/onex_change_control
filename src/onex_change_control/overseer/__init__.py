# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""onex_change_control.overseer — promotion bot policy (OMN-16191).

The rest of the overseer wire-type family (contracts, dispatch, task/verifier
envelopes, and their supporting enums) was deleted here and now lives
exclusively in ``omnibase_core.models.overseer`` / ``omnibase_core.enums.overseer``
(OMN-11225, OMN-16191). OCC is the governance/receipts repo; it does not own
data shapes. Import those types directly from omnibase_core.

``ModelPromotionBotPolicy`` / ``EnumPromotionBotAction`` remain here: they have
no home in omnibase_core yet and no consumer outside OCC (residual ticket).
"""

from onex_change_control.overseer.model_promotion_bot_policy import (
    DEFAULT_PROMOTION_BOT_POLICY,
    OMNINODE_PROMOTION_REPOS,
    EnumPromotionBotAction,
    ModelPromotionBotPolicy,
)

__all__ = [
    "DEFAULT_PROMOTION_BOT_POLICY",
    "OMNINODE_PROMOTION_REPOS",
    "EnumPromotionBotAction",
    "ModelPromotionBotPolicy",
]
