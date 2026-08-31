"""Deterministic repro for OMN-12587 Finding 1 — verification evidence.

`StabilityDelegationBus.publish_and_wait` (src/pipeline/delegation_bus_stability.py)
wraps its async round-trip in `asyncio.run(...)`. That call raises
`RuntimeError: asyncio.run() cannot be called from a running event loop` whenever
publish_and_wait is invoked from inside an already-running loop — which is
exactly how the ADK `--agent` path invokes the sync `scaffold_onex_node` tool.

The bus's transport is INJECTABLE, so this isolates the sync/async-bridge defect
with NO live Kafka broker — that is why the existing unit tests miss it: they
call publish_and_wait from a plain sync context (no running loop), where
`asyncio.run` is legal.

Status (SEA dev e1f0503, the #202 "fix" commit): RED — raises the RuntimeError
before any transport call.
Goes GREEN once publish_and_wait is loop-aware (run `_round_trip` in a dedicated
thread, or thread the async path through end-to-end).

Suggested home: tests/unit/test_delegation_bus_stability.py
Run: PYTHONPATH=<sea-repo-root> .venv/bin/python -m pytest <this file> -q
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.pipeline.delegation_bus_stability import (
    ModelStabilityBusConfig,
    StabilityDelegationBus,
)

_REQUEST_TOPIC = "onex.cmd.omnibase-infra.delegation-inference-request.v1"
_RESPONSE_TOPIC = "onex.evt.omnibase-infra.inference-response.v1"


class _FakeTransport:
    """Minimal in-memory transport: returns a canned correlated response.

    Implements the ProtocolStabilityTransport surface so the bus can be exercised
    without a live broker. This is the same injection seam the shipped tests use.
    """

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def arm_response(
        self, *, response_topic: str, correlation_id: str
    ) -> None: ...
    async def publish(
        self, topic: str, key: bytes | None, value: bytes, headers: object = None
    ) -> None: ...

    async def await_response(
        self, *, response_topic: str, correlation_id: str, timeout_s: float
    ) -> dict[str, Any]:
        return {"correlation_id": correlation_id, "result": "ok"}


def _build_bus() -> StabilityDelegationBus:
    config = ModelStabilityBusConfig(
        bootstrap_servers="fake:9092",
        api_version="2.8.0",
        request_topic=_REQUEST_TOPIC,
        response_topic=_RESPONSE_TOPIC,
    )
    return StabilityDelegationBus(config, _FakeTransport())


def test_publish_and_wait_completes_when_called_inside_a_running_event_loop() -> None:
    """publish_and_wait must complete when invoked from inside a running loop.

    Mirrors the ADK `--agent` invocation: the sync scaffold tool (and therefore
    publish_and_wait) runs inside the agent's already-running event loop. The bus
    must return the response there, not raise. RED on e1f0503; GREEN once the
    sync/async bridge is loop-aware.
    """
    bus = _build_bus()

    async def _invoke_like_adk_does() -> dict[str, Any]:
        # Synchronous call, from inside a running loop — the exact shape the ADK
        # uses when it invokes the sync scaffold_onex_node tool.
        return bus.publish_and_wait(
            _REQUEST_TOPIC, {"correlation_id": "repro-cid"}, timeout_s=5.0
        )

    result = asyncio.run(_invoke_like_adk_does())
    assert result == {"correlation_id": "repro-cid", "result": "ok"}
