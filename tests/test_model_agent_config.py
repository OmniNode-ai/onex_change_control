# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for ModelAgentConfig — agent YAML schema validation."""

import pytest
from pydantic import ValidationError

from onex_change_control.models.model_agent_config import ModelAgentConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_config(**overrides: object) -> dict[str, object]:
    """Return a minimal valid agent config dict, overridable via kwargs."""
    defaults: dict[str, object] = {
        "schema_version": "1.0.0",
        "agent_type": "test_agent",
        "agent_identity": {
            "name": "agent-test",
            "description": "A test agent",
        },
        "activation_patterns": {
            "explicit_triggers": ["test trigger"],
        },
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestModelAgentConfigValid:
    def test_minimal_valid(self) -> None:
        config = ModelAgentConfig.model_validate(_minimal_config())
        assert config.schema_version == "1.0.0"
        assert config.agent_type == "test_agent"
        assert config.agent_identity.name == "agent-test"
        assert config.agent_identity.description == "A test agent"
        assert config.activation_patterns.explicit_triggers == ["test trigger"]

    def test_extra_fields_ignored(self) -> None:
        data = _minimal_config(
            mode="full",
            capabilities={"primary": ["cap1"]},
            intelligence_integration={},
            onex_integration={},
        )
        config = ModelAgentConfig.model_validate(data)
        assert config.agent_type == "test_agent"

    def test_optional_disallowed_tools_default_empty(self) -> None:
        config = ModelAgentConfig.model_validate(_minimal_config())
        assert config.disallowed_tools == []

    def test_disallowed_tools_populated(self) -> None:
        config = ModelAgentConfig.model_validate(
            _minimal_config(disallowedTools=["Edit", "Write"])
        )
        assert config.disallowed_tools == ["Edit", "Write"]

    def test_agent_identity_extra_fields_ignored(self) -> None:
        data = _minimal_config()
        identity = data["agent_identity"]
        assert isinstance(identity, dict)
        identity["color"] = "green"
        identity["title"] = "Test Agent Title"
        identity["category"] = "testing"
        config = ModelAgentConfig.model_validate(data)
        assert config.agent_identity.name == "agent-test"

    def test_activation_patterns_context_triggers_optional(self) -> None:
        data = _minimal_config()
        ap = data["activation_patterns"]
        assert isinstance(ap, dict)
        ap["context_triggers"] = ["context one"]
        config = ModelAgentConfig.model_validate(data)
        assert config.activation_patterns.context_triggers == ["context one"]


# ---------------------------------------------------------------------------
# Missing required fields (dod-001)
# ---------------------------------------------------------------------------


class TestMissingRequiredFieldFails:
    def test_missing_schema_version(self) -> None:
        data = _minimal_config()
        del data["schema_version"]
        with pytest.raises(ValidationError, match="schema_version"):
            ModelAgentConfig.model_validate(data)

    def test_missing_agent_type(self) -> None:
        data = _minimal_config()
        del data["agent_type"]
        with pytest.raises(ValidationError, match="agent_type"):
            ModelAgentConfig.model_validate(data)

    def test_missing_agent_identity(self) -> None:
        data = _minimal_config()
        del data["agent_identity"]
        with pytest.raises(ValidationError, match="agent_identity"):
            ModelAgentConfig.model_validate(data)

    def test_missing_agent_identity_name(self) -> None:
        data = _minimal_config(agent_identity={"description": "no name"})
        with pytest.raises(ValidationError, match="name"):
            ModelAgentConfig.model_validate(data)

    def test_missing_agent_identity_description(self) -> None:
        data = _minimal_config(agent_identity={"name": "agent-x"})
        with pytest.raises(ValidationError, match="description"):
            ModelAgentConfig.model_validate(data)

    def test_missing_activation_patterns(self) -> None:
        data = _minimal_config()
        del data["activation_patterns"]
        with pytest.raises(ValidationError, match="activation_patterns"):
            ModelAgentConfig.model_validate(data)

    def test_missing_explicit_triggers(self) -> None:
        data = _minimal_config(activation_patterns={"context_triggers": ["x"]})
        with pytest.raises(ValidationError, match="explicit_triggers"):
            ModelAgentConfig.model_validate(data)

    def test_empty_explicit_triggers_fails(self) -> None:
        data = _minimal_config(activation_patterns={"explicit_triggers": []})
        with pytest.raises(ValidationError, match="explicit_triggers"):
            ModelAgentConfig.model_validate(data)

    def test_empty_agent_identity_name_fails(self) -> None:
        data = _minimal_config(agent_identity={"name": "", "description": "desc"})
        with pytest.raises(ValidationError, match="name"):
            ModelAgentConfig.model_validate(data)

    def test_empty_agent_identity_description_fails(self) -> None:
        data = _minimal_config(agent_identity={"name": "agent-x", "description": ""})
        with pytest.raises(ValidationError, match="description"):
            ModelAgentConfig.model_validate(data)
