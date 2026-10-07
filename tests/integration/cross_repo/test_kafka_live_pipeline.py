# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""
Live Kafka broker-path sanity tests — requires running Redpanda/Kafka broker.

Produces and consumes a test payload through a real broker to verify:
1. Broker is reachable and accepts connections
2. Topic auto-creation works for test topics
3. JSON serialization round-trips through the broker without corruption

TRUTH CLAIM: This is a broker-path sanity check using a dedicated test topic
and a synthetic payload. It does NOT verify real producer or consumer
implementations on production boundary topics. It complements boundary-specific
integration coverage but does not replace it. Think of this as "can the bus
carry a packet" — not "does the packet have the right schema for its consumer."

Gate semantics:
- Static cross-repo tests (schema round-trip + topic constant match) are the
  mandatory regression gate.
- This live probe is best-effort: it auto-skips when no broker is reachable.
- In local close-out environments with Kafka running, it should be treated as
  expected rather than optional.

Run:
    KAFKA_BOOTSTRAP_SERVERS=localhost:19092 uv run pytest \
        tests/integration/cross_repo/test_kafka_live_pipeline.py -v
"""

import json
import os
import time

import pytest

KAFKA_BROKERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:19092")

# Lazy import guard: kafka-python is an optional dependency.
# Install with: uv sync --extra kafka  (or uv add kafka-python)
try:
    from kafka import KafkaConsumer, KafkaProducer  # type: ignore[import-not-found]

    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False


def _broker_reachable() -> bool:
    """Return True if the broker accepts a connection attempt."""
    if not KAFKA_AVAILABLE:
        return False
    try:
        # Attempt a real connection with a short timeout
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BROKERS,
            request_timeout_ms=3000,
            connections_max_idle_ms=4000,
        )
        producer.close()
    except Exception:  # noqa: BLE001
        return False
    else:
        return True


_BROKER_UP = _broker_reachable()


@pytest.mark.integration
@pytest.mark.skipif(
    not _BROKER_UP,
    reason=(
        f"Kafka broker not reachable at {KAFKA_BROKERS}. "
        "Start local infra with `infra-up` or set KAFKA_BOOTSTRAP_SERVERS."
    ),
)
class TestKafkaLivePipeline:
    """Produce and consume through a live Kafka broker.

    Broker-path sanity only — NOT a cross-repo boundary end-to-end test.
    """

    TEST_TOPIC = "onex.test.integration.cross-repo-roundtrip.v1"
    CONSUME_TIMEOUT_MS = 15_000

    def test_produce_consume_roundtrip(self) -> None:
        """Produce a message, consume it back, verify content is preserved.

        Uses a unique marker per run to avoid consuming stale messages from
        previous test runs (e.g. leftover messages from an auto-offset-reset).
        """
        unique_marker = f"roundtrip-{int(time.time() * 1000)}"
        test_payload = {
            "session_id": "integration-test",
            "event_type": "cross_repo_sanity",
            "timestamp": time.time(),
            "marker": unique_marker,
        }

        # Produce
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BROKERS,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            request_timeout_ms=10_000,
        )
        future = producer.send(self.TEST_TOPIC, value=test_payload)
        try:
            future.get(timeout=10)
        finally:
            producer.close()

        # Consume — unique group_id ensures we start from earliest for this run
        consumer = KafkaConsumer(
            self.TEST_TOPIC,
            bootstrap_servers=KAFKA_BROKERS,
            auto_offset_reset="earliest",
            consumer_timeout_ms=self.CONSUME_TIMEOUT_MS,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            group_id=f"cross-repo-test-{unique_marker}",
        )

        consumed = None
        try:
            for message in consumer:
                if (
                    isinstance(message.value, dict)
                    and message.value.get("marker") == unique_marker
                ):
                    consumed = message.value
                    break
        finally:
            consumer.close()

        assert consumed is not None, (
            f"Test message with marker '{unique_marker}' was not consumed within "
            f"{self.CONSUME_TIMEOUT_MS}ms. Broker: {KAFKA_BROKERS}"
        )
        assert consumed["session_id"] == "integration-test", (
            f"session_id mismatch: {consumed['session_id']!r}"
        )
        assert consumed["marker"] == unique_marker, (
            f"marker mismatch: {consumed['marker']!r} != {unique_marker!r}"
        )
        assert consumed["event_type"] == "cross_repo_sanity", (
            f"event_type mismatch: {consumed['event_type']!r}"
        )

    def test_kafka_available_import(self) -> None:
        """Sanity: kafka-python is importable when broker is reachable.

        This test documents the dependency: if kafka-python is not installed,
        the live tests are skipped by the class-level skipif. If kafka-python
        IS installed and the broker IS up, both conditions must hold.
        """
        assert KAFKA_AVAILABLE, (
            "kafka-python is not installed but broker was deemed reachable. "
            "This should not happen — _broker_reachable() returns False if "
            "KAFKA_AVAILABLE is False."
        )
