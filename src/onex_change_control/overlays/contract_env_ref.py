# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Canonical ``${env.VAR}`` overlay expansion for onex_change_control contracts.

This is the sanctioned overlay-resolution surface for the ``${env.VAR}`` /
``${env.VAR:default}`` convention that this repo's integration contract uses to
bind a lane-specific value (an endpoint host, a port, a connection target, a
state-store root, a governance toggle) from the operator environment WITHOUT
hardcoding the value in source (OMN-13563 / OMN-13556 env→contract+overlay
migration).

``omnibase_core`` owns the resolver authority every downstream repo imports
(``omnibase_core.overlays.contract_env_ref``); the runtime overlay package in
``omnibase_infra`` (``omnibase_infra.runtime.overlay.contract_env_ref``) is the
infra-layer mirror. ``onex_change_control`` depends only on ``omnibase_core`` and
its scripts must not pin an unreleased core commit, so — exactly as core itself
vendors its own copy because it cannot import infra (compat → core → spi → infra
layering) — this module vendors the identical canonical expansion for any
onex_change_control consumer (CLI scripts, the dod-sweep handler) that resolves a
contract-declared endpoint/config reference. The expansion is byte-for-byte the
same convention as the core/infra surfaces; it is not a divergent fork.

An unset var with no inline default expands to the empty string, so the caller's
fail-closed check rejects it (rather than leaving a literal ``${env.…}``
placeholder, or silently falling back to localhost).
"""

from __future__ import annotations

import os
import re

# ``${env.VAR}`` / ``${env.VAR:default}`` — the same env-overlay convention the
# core/infra runtime overlay surfaces use for endpoints.
_ENV_REF = re.compile(
    r"\$\{env\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::(?P<default>[^}]*))?\}"
)


def expand_contract_env_refs(value: str) -> str:
    """Expand ``${env.VAR}`` / ``${env.VAR:default}`` references in ``value``.

    Resolves each reference from the operator environment; an unset var with no
    inline default expands to the empty string (so the caller fails closed rather
    than passing a literal ``${env.…}`` placeholder downstream or defaulting to
    localhost).
    """

    def _sub(match: re.Match[str]) -> str:
        name = match.group("name")
        default = match.group("default")
        return os.environ.get(name, default if default is not None else "")

    return _ENV_REF.sub(_sub, value)


__all__: list[str] = ["expand_contract_env_refs"]
