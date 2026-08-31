# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Resolve onex_change_control's contract-declared integration endpoints/config.

The integration contract (``integrations/contract.yaml``) is the single source of
truth for every external endpoint, connection target, tunable config value, and
secret reference the onex_change_control CLI scripts + dod-sweep handler use
(OMN-13563 / OMN-13556 env→contract+overlay migration). Each field is declared
with the ``${env.VAR}`` / ``${env.VAR:default}`` overlay convention so an operator
overlay / the per-lane service env supplies the real value per lane — never a
hardcoded host, port, URL, or path in source.

Resolution goes through ``expand_contract_env_refs`` (the sanctioned env-reading
boundary) so callers never read ``os.environ`` directly for these values.
Required endpoints/config resolve **fail-closed**: an unset/empty value raises
``ValueError`` rather than silently defaulting to localhost or a hardcoded
literal. Optional values (the governance Kafka broker, the emergency-bypass
toggle, the Linear API key when the caller treats it as optional) resolve to an
empty string / ``None`` and the caller decides.

Secrets are never inlined in the contract: the contract declares a ``*_ref`` name
(the env/Infisical key) and the value is resolved at the effect boundary via
``resolve_secret``.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from onex_change_control.overlays.contract_env_ref import expand_contract_env_refs

_CONTRACT = Path(__file__).resolve().parent / "contract.yaml"


def _load_descriptor(contract_path: Path = _CONTRACT) -> dict[str, object]:
    """Load the ``descriptor`` mapping from the integration contract."""
    with contract_path.open(encoding="utf-8") as contract_file:
        raw = yaml.safe_load(contract_file)
    if not isinstance(raw, dict):
        msg = f"contract {contract_path} must contain a mapping"
        raise TypeError(msg)
    descriptor = raw.get("descriptor")
    if not isinstance(descriptor, dict):
        msg = f"contract {contract_path} must declare a descriptor mapping"
        raise TypeError(msg)
    return descriptor


def _declared(field: str, contract_path: Path) -> str:
    """Return the raw ``${env.VAR}`` declaration for ``field``."""
    descriptor = _load_descriptor(contract_path)
    declared = descriptor.get(field)
    if not isinstance(declared, str):
        msg = f"contract {contract_path} must declare a string descriptor.{field}"
        raise TypeError(msg)
    return declared


def _resolve_required(field: str, contract_path: Path) -> str:
    """Resolve ``field`` fail-closed: raise on unset/empty."""
    resolved = expand_contract_env_refs(_declared(field, contract_path)).strip()
    if not resolved:
        msg = (
            f"descriptor.{field} resolved empty — set the underlying env var. "
            "The consumer fails closed rather than silently defaulting to "
            "localhost or a hardcoded literal."
        )
        raise ValueError(msg)
    return resolved


def _resolve_optional(field: str, contract_path: Path) -> str:
    """Resolve ``field`` permissively: empty string when unset (no default)."""
    return expand_contract_env_refs(_declared(field, contract_path)).strip()


# --------------------------------------------------------------------------- #
# Linear GraphQL endpoint + API key
# --------------------------------------------------------------------------- #


def linear_graphql_url(contract_path: Path = _CONTRACT) -> str:
    """Return the resolved Linear GraphQL endpoint (fail-closed).

    Replaces the four hardcoded ``https://(api.)linear.app/graphql`` literals.
    Carries the canonical ``https://api.linear.app/graphql`` inline default so
    prior behaviour is preserved when ``LINEAR_API_URL`` is unset.
    """
    return _resolve_required("linear_graphql_url", contract_path)


def linear_api_key(*, required: bool, contract_path: Path = _CONTRACT) -> str:
    """Resolve the Linear API key from the contract-declared secret ref.

    ``required=True`` fails closed (raises) when the key is unset; ``required=
    False`` returns the empty string so the caller can branch (matching the
    prior ``os.environ.get("LINEAR_API_KEY", "")`` behaviour at the optional
    call sites).
    """
    ref = _declared("linear_api_key_ref", contract_path).strip()
    value = os.environ.get(ref, "").strip()
    if required and not value:
        msg = (
            f"{ref} is required but is not set — resolve the Linear API key "
            "from the operator environment / Infisical store."
        )
        raise ValueError(msg)
    return value


# --------------------------------------------------------------------------- #
# omnidash_analytics Postgres endpoint + credentials
# --------------------------------------------------------------------------- #


def omnidash_db_host(contract_path: Path = _CONTRACT) -> str:
    """Return the resolved omnidash_analytics Postgres host (fail-closed)."""
    return _resolve_required("omnidash_db_host", contract_path)


def omnidash_db_port(contract_path: Path = _CONTRACT) -> int:
    """Return the resolved omnidash_analytics Postgres port (default 5436)."""
    resolved = _resolve_required("omnidash_db_port", contract_path)
    try:
        return int(resolved)
    except ValueError as exc:
        msg = (
            f"descriptor.omnidash_db_port resolved to {resolved!r}, which is "
            "not a valid integer — set POSTGRES_PORT to the Postgres port."
        )
        raise ValueError(msg) from exc


def omnidash_db_name(contract_path: Path = _CONTRACT) -> str:
    """Return the resolved omnidash_analytics database name (fail-closed)."""
    return _resolve_required("omnidash_db_name", contract_path)


def omnidash_db_user(contract_path: Path = _CONTRACT) -> str:
    """Return the resolved omnidash_analytics database user (fail-closed)."""
    return _resolve_required("omnidash_db_user", contract_path)


def omnidash_db_password(contract_path: Path = _CONTRACT) -> str:
    """Resolve the omnidash Postgres password from the secret ref (fail-closed).

    The contract declares only the ``*_ref`` name; the literal value is resolved
    from the operator environment / Infisical store at the effect boundary.
    """
    ref = _declared("omnidash_db_password_ref", contract_path).strip()
    value = os.environ.get(ref, "").strip()
    if not value:
        msg = (
            f"{ref} is required but is not set — resolve the omnidash Postgres "
            "password from the operator environment / Infisical store."
        )
        raise ValueError(msg)
    return value


# --------------------------------------------------------------------------- #
# Kafka governance broker (optional — best-effort emission)
# --------------------------------------------------------------------------- #


def kafka_bootstrap_servers(contract_path: Path = _CONTRACT) -> str | None:
    """Return the resolved Kafka bootstrap servers, or ``None`` when unset.

    Optional: the governance emitter no-ops when the broker is not configured,
    matching the prior ``os.environ.get(...) or None`` behaviour.
    """
    resolved = _resolve_optional("kafka_bootstrap_servers", contract_path)
    return resolved or None


# --------------------------------------------------------------------------- #
# ONEX state-store root (required — fail-closed)
# --------------------------------------------------------------------------- #


def onex_state_dir(contract_path: Path = _CONTRACT) -> str:
    """Return the resolved ONEX state-store root (fail-closed)."""
    return _resolve_required("onex_state_dir", contract_path)


# --------------------------------------------------------------------------- #
# Emergency-bypass governance toggle (optional)
# --------------------------------------------------------------------------- #


def emergency_bypass(contract_path: Path = _CONTRACT) -> str:
    """Return the resolved emergency-bypass token (empty string == disabled)."""
    return _resolve_optional("emergency_bypass", contract_path)


__all__: list[str] = [
    "emergency_bypass",
    "kafka_bootstrap_servers",
    "linear_api_key",
    "linear_graphql_url",
    "omnidash_db_host",
    "omnidash_db_name",
    "omnidash_db_password",
    "omnidash_db_port",
    "omnidash_db_user",
    "onex_state_dir",
]
