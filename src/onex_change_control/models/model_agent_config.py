# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Agent Config Model.

Pydantic schema for validating agent configuration YAML files
(plugins/onex/agents/configs/*.yaml).
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelAgentIdentity(BaseModel):
    """Agent identity block — who this agent is."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, description="Agent identifier")
    description: str = Field(..., min_length=1, description="What this agent does")


class ModelActivationPatterns(BaseModel):
    """When this agent should activate."""

    model_config = ConfigDict(extra="ignore")

    explicit_triggers: list[str] = Field(
        ..., description="Phrases that explicitly invoke this agent"
    )
    context_triggers: list[str] = Field(
        default_factory=list, description="Context-based activation phrases"
    )

    @field_validator("explicit_triggers")
    @classmethod
    def explicit_triggers_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            msg = "explicit_triggers must contain at least one trigger"
            raise ValueError(msg)
        return v


class ModelAgentConfig(BaseModel):
    """Top-level schema for an agent configuration YAML."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_version: str = Field(
        ..., description="Schema version (semver)"
    )  # string-version-ok: wire type read from YAML agent config files
    agent_type: str = Field(..., description="Agent type identifier")
    agent_identity: ModelAgentIdentity = Field(..., description="Agent identity block")
    activation_patterns: ModelActivationPatterns = Field(
        ..., description="Activation trigger patterns"
    )
    disallowed_tools: list[str] = Field(
        default_factory=list,
        alias="disallowedTools",
        description="Tools this agent must not use",
    )
