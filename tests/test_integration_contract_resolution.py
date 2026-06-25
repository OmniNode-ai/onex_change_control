# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Resolution-equivalence + fail-closed tests for the integration contract.

OMN-13563 / OMN-13556 env→contract+overlay migration.

Proves that resolving each endpoint/config/secret from the contract+overlay
(``onex_change_control.integrations.contract_descriptor``) yields the SAME value
the prior raw ``os.environ`` read / hardcoded literal produced, and that required
fields fail closed when their env var is unset.
"""

from __future__ import annotations

import pytest

from onex_change_control.integrations import contract_descriptor as cd
from onex_change_control.overlays.contract_env_ref import expand_contract_env_refs

# --------------------------------------------------------------------------- #
# expand_contract_env_refs — the sanctioned overlay boundary
# --------------------------------------------------------------------------- #


def test_expand_resolves_set_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCC_TEST_VAR", "resolved-value")
    assert expand_contract_env_refs("${env.OCC_TEST_VAR}") == "resolved-value"


def test_expand_uses_inline_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OCC_TEST_VAR", raising=False)
    assert expand_contract_env_refs("${env.OCC_TEST_VAR:fallback}") == "fallback"


def test_expand_unset_no_default_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCC_TEST_VAR", raising=False)
    assert expand_contract_env_refs("${env.OCC_TEST_VAR}") == ""


# --------------------------------------------------------------------------- #
# Linear GraphQL endpoint — 4 hardcoded literals consolidated
# --------------------------------------------------------------------------- #


def test_linear_graphql_url_default_matches_prior_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset LINEAR_API_URL resolves to the canonical prior literal."""
    monkeypatch.delenv("LINEAR_API_URL", raising=False)
    # The three canonical call sites used this literal verbatim.
    assert cd.linear_graphql_url() == "https://api.linear.app/graphql"


def test_linear_graphql_url_overlay_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINEAR_API_URL", "https://linear.example.test/graphql")
    assert cd.linear_graphql_url() == "https://linear.example.test/graphql"


def test_linear_api_key_required_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    with pytest.raises(ValueError, match="LINEAR_API_KEY"):
        cd.linear_api_key(required=True)


def test_linear_api_key_optional_empty_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    assert cd.linear_api_key(required=False) == ""


def test_linear_api_key_resolves_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_secret")
    assert cd.linear_api_key(required=True) == "lin_api_secret"
    assert cd.linear_api_key(required=False) == "lin_api_secret"


# --------------------------------------------------------------------------- #
# omnidash_analytics Postgres endpoint + credentials
# --------------------------------------------------------------------------- #


def test_omnidash_db_endpoint_equivalence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overlay yields the same host/port/name/user the raw env reads gave."""
    monkeypatch.setenv("POSTGRES_HOST", "192.168.86.201")
    monkeypatch.setenv("POSTGRES_PORT", "5436")
    monkeypatch.setenv("OMNIDASH_DB_NAME", "omnidash_analytics")
    monkeypatch.setenv("POSTGRES_USER", "omnidash")

    assert cd.omnidash_db_host() == "192.168.86.201"
    assert cd.omnidash_db_port() == 5436
    assert cd.omnidash_db_name() == "omnidash_analytics"
    assert cd.omnidash_db_user() == "omnidash"


def test_omnidash_db_port_default_matches_prior_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset POSTGRES_PORT resolves to the prior `5436` default."""
    monkeypatch.setenv("POSTGRES_HOST", "db.test")
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    assert cd.omnidash_db_port() == 5436


def test_omnidash_db_host_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    with pytest.raises(ValueError, match="omnidash_db_host"):
        cd.omnidash_db_host()


def test_omnidash_db_name_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIDASH_DB_NAME", raising=False)
    with pytest.raises(ValueError, match="omnidash_db_name"):
        cd.omnidash_db_name()


def test_omnidash_db_user_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    with pytest.raises(ValueError, match="omnidash_db_user"):
        cd.omnidash_db_user()


def test_omnidash_db_port_non_integer_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_PORT", "not-a-port")
    with pytest.raises(ValueError, match="not a valid integer"):
        cd.omnidash_db_port()


def test_omnidash_db_password_equivalence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cr3t")
    assert cd.omnidash_db_password() == "s3cr3t"


def test_omnidash_db_password_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        cd.omnidash_db_password()


# --------------------------------------------------------------------------- #
# Kafka governance broker (optional)
# --------------------------------------------------------------------------- #


def test_kafka_bootstrap_servers_equivalence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "192.168.86.201:39092")
    assert cd.kafka_bootstrap_servers() == "192.168.86.201:39092"


def test_kafka_bootstrap_servers_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset broker resolves to None (prior `or None` behaviour)."""
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    assert cd.kafka_bootstrap_servers() is None


# --------------------------------------------------------------------------- #
# ONEX state-store root (required)
# --------------------------------------------------------------------------- #


def test_onex_state_dir_equivalence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONEX_STATE_DIR", "/var/onex/state")
    assert cd.onex_state_dir() == "/var/onex/state"


def test_onex_state_dir_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ONEX_STATE_DIR", raising=False)
    with pytest.raises(ValueError, match="onex_state_dir"):
        cd.onex_state_dir()


# --------------------------------------------------------------------------- #
# Emergency-bypass governance toggle (optional)
# --------------------------------------------------------------------------- #


def test_emergency_bypass_equivalence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMERGENCY_BYPASS", "OMN-9999-incident")
    assert cd.emergency_bypass() == "OMN-9999-incident"


def test_emergency_bypass_empty_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMERGENCY_BYPASS", raising=False)
    assert cd.emergency_bypass() == ""
