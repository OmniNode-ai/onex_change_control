# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Integration Surface Enum.

Operational probe categories for the /integration-sweep endpoint.
Distinct from EnumInterfaceSurface (which classifies interface change type).
"""

from enum import Enum, unique


@unique
class EnumIntegrationSurface(str, Enum):
    """Operational probe categories for integration sweep investigations.

    Classifies which operational surface a probe targets during the
    /integration-sweep workflow:
    - KAFKA: Kafka/Redpanda topic and consumer group probes
    - DB: Database schema and migration probes
    - CI: Continuous integration workflow probes
    - PLUGIN: omniclaude plugin and hook probes
    - GITHUB_CI: GitHub Actions workflow and runner probes
    - SCRIPT: Shell script and automation probes
    """

    KAFKA = "kafka"
    """Kafka/Redpanda topic and consumer group probes."""

    DB = "db"
    """Database schema and migration probes."""

    CI = "ci"
    """Continuous integration workflow probes."""

    PLUGIN = "plugin"
    """omniclaude plugin and hook probes."""

    GITHUB_CI = "github_ci"
    """GitHub Actions workflow and runner probes."""

    SCRIPT = "script"
    """Shell script and automation probes."""

    def __str__(self) -> str:
        """Return the string value for serialization."""
        return self.value
