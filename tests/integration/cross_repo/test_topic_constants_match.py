# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""
Cross-repo topic constant verification.

Verifies that topic strings appear in both producer and consumer files.
Uses kafka_boundaries.yaml as the authoritative boundary registry.

TRUTH CLAIM: These tests are a lightweight smoke check for obvious topic-string
drift — e.g. a producer renamed a topic constant but the consumer file still
contains the old string. They are NOT authoritative proof of live subscription
or emission wiring. A regex hit can come from a comment, a dead constant, or an
example string that is never executed. This harness catches regressions, not
silence.

NEGATIVE FIXTURE (test_regex_scan_can_match_dead_code): Proves that topic-pattern
scans can match patterns in comments and dead code, establishing that a scan hit
does NOT prove live production/consumption. See the test for details.

Run: OMNI_HOME=/path/to/omni_home uv run pytest tests/integration/cross_repo/ -v
"""

import re
import textwrap
from pathlib import Path

import pytest


@pytest.mark.integration
class TestTopicConstantsMatch:
    """Verify producer topic constants match consumer subscriptions."""

    def test_producer_files_contain_topic(
        self,
        boundary_manifest: list[dict[str, str]],
        omni_home: Path,
    ) -> None:
        """Each boundary's producer_file must contain the topic_pattern."""
        failures = []
        for entry in boundary_manifest:
            producer_file = omni_home / entry["producer_repo"] / entry["producer_file"]
            if not producer_file.exists():
                failures.append(
                    f"Producer file missing: {entry['producer_repo']}/"
                    f"{entry['producer_file']} (topic: {entry['topic_name']})"
                )
                continue

            content = producer_file.read_text()
            pattern = entry["topic_pattern"]
            if not re.search(pattern, content):
                failures.append(
                    f"Topic pattern '{pattern}' not found in "
                    f"{entry['producer_repo']}/{entry['producer_file']} "
                    f"(topic: {entry['topic_name']})"
                )

        assert not failures, (
            f"{len(failures)} producer/topic mismatches:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    def test_consumer_files_contain_topic(
        self,
        boundary_manifest: list[dict[str, str]],
        omni_home: Path,
    ) -> None:
        """Each boundary's consumer_file must contain the topic_pattern."""
        failures = []
        for entry in boundary_manifest:
            consumer_file = omni_home / entry["consumer_repo"] / entry["consumer_file"]
            if not consumer_file.exists():
                failures.append(
                    f"Consumer file missing: {entry['consumer_repo']}/"
                    f"{entry['consumer_file']} (topic: {entry['topic_name']})"
                )
                continue

            content = consumer_file.read_text()
            pattern = entry["topic_pattern"]
            if not re.search(pattern, content):
                failures.append(
                    f"Topic pattern '{pattern}' not found in "
                    f"{entry['consumer_repo']}/{entry['consumer_file']} "
                    f"(topic: {entry['topic_name']})"
                )

        assert not failures, (
            f"{len(failures)} consumer/topic mismatches:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    def test_no_orphaned_boundaries(
        self,
        boundary_manifest: list[dict[str, str]],
        omni_home: Path,
    ) -> None:
        """Every boundary should have both producer and consumer files present."""
        orphans = []
        for entry in boundary_manifest:
            producer = omni_home / entry["producer_repo"] / entry["producer_file"]
            consumer = omni_home / entry["consumer_repo"] / entry["consumer_file"]
            if not producer.exists() or not consumer.exists():
                orphans.append(
                    f"{entry['topic_name']}: "
                    f"producer={'EXISTS' if producer.exists() else 'MISSING'}, "
                    f"consumer={'EXISTS' if consumer.exists() else 'MISSING'}"
                )

        assert not orphans, f"{len(orphans)} orphaned boundaries:\n" + "\n".join(
            f"  - {o}" for o in orphans
        )

    def test_regex_scan_can_match_dead_code(self) -> None:
        """Negative fixture: proves topic-pattern scans can match dead/commented code.

        This test establishes that a regex hit in test_producer_files_contain_topic
        or test_consumer_files_contain_topic does NOT prove the topic is live.
        The pattern may match a comment, an example string, or a deprecated constant
        that is never executed.

        This is intentional documentation of the scan's smoke-check nature.
        It prevents future over-reliance on these tests as proof of live wiring.
        """
        # Simulate a file where the topic appears only in a comment and dead code
        # TODO_FORMAT_EXEMPT: string literal below simulates dead-code comment
        dead_code_content = textwrap.dedent("""
            # TODO: remove this old topic: onex.evt.omniclaude.deprecated-topic.v1
            #
            # DEAD_TOPIC = "onex.evt.omniclaude.deprecated-topic.v1"  # no longer used
            #
            # The live topic is now: onex.evt.omniclaude.active-topic.v2

            ACTIVE_TOPIC = "onex.evt.omniclaude.active-topic.v2"
        """)

        # The regex for the old topic MATCHES despite being in comments only
        old_pattern = r"deprecated-topic\.v1"
        assert re.search(old_pattern, dead_code_content), (
            "Expected dead_code_content to match old_pattern via comment — "
            "this proves scan hits do not guarantee live usage"
        )

        # The scan cannot distinguish live from dead usage
        new_pattern = r"active-topic\.v2"
        assert re.search(new_pattern, dead_code_content), (
            "Active topic also matches, but we cannot tell from regex alone "
            "which one is live"
        )

        # CONCLUSION: These scans are smoke checks. A scan FAILURE is meaningful
        # (topic string completely absent). A scan PASS is necessary but not sufficient.
