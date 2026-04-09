# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Scan contract.yaml files across repos to extract dependency entries."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from onex_change_control.models.model_contract_dependency_input import (
    ModelContractEntry,
    ModelDbTableRef,
)

logger = logging.getLogger(__name__)


def scan_contracts(nodes_dir: Path, repo_name: str) -> list[ModelContractEntry]:
    """Scan a nodes directory for contract.yaml files and extract protocol surfaces."""
    entries: list[ModelContractEntry] = []

    for contract_path in sorted(nodes_dir.rglob("contract.yaml")):
        try:
            with open(contract_path) as f:  # noqa: PTH123
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("Skipping unreadable contract %s: %s", contract_path, exc)
            continue

        if not isinstance(data, dict):
            logger.warning(
                "Skipping malformed contract %s: not a mapping", contract_path
            )
            continue

        data = data or {}
        node_name = data.get("name")
        if not node_name:
            logger.warning("Skipping contract %s: missing 'name' field", contract_path)
            continue

        event_bus = data.get("event_bus", {})
        if event_bus is not None and not isinstance(event_bus, dict):
            logger.warning(
                "Skipping contract %s: 'event_bus' has unexpected shape (got %s)",
                contract_path,
                type(event_bus).__name__,
            )
            continue
        event_bus = event_bus or {}
        db_io = data.get("db_io", {}) or {}

        # Topics may be plain strings OR rich dicts with a
        # 'name' key — normalize to strings
        raw_subscribe = event_bus.get("subscribe_topics", []) or []
        raw_publish = event_bus.get("publish_topics", []) or []
        subscribe_topics = [
            t if isinstance(t, str) else t.get("name", "")
            for t in raw_subscribe
            if isinstance(t, (str, dict))
        ]
        publish_topics = [
            t if isinstance(t, str) else t.get("name", "")
            for t in raw_publish
            if isinstance(t, (str, dict))
        ]
        subscribe_topics = [t for t in subscribe_topics if t]
        publish_topics = [t for t in publish_topics if t]

        # Extract db_tables with access mode from db_io declarations
        raw_db_tables = db_io.get("db_tables", []) or []
        db_tables = [
            ModelDbTableRef(
                name=t["name"],
                access=t.get("access", "read_write"),
            )
            for t in raw_db_tables
            if isinstance(t, dict) and "name" in t
        ]

        # protocols is left empty — no contract currently declares
        # explicit protocol surfaces. Topic overlap is captured
        # via subscribe_topics/publish_topics directly.
        protocols: list[str] = []

        entries.append(
            ModelContractEntry(
                repo=repo_name,
                node_name=node_name,
                subscribe_topics=subscribe_topics,
                publish_topics=publish_topics,
                protocols=protocols,
                db_tables=db_tables,
            )
        )

    return entries


def scan_all_repos(omni_home: Path) -> list[ModelContractEntry]:
    """Scan all repos in the omni_home registry."""
    repo_map = {
        "omniclaude": "omniclaude/src/omniclaude/nodes",
        "omnibase_core": "omnibase_core/src/omnibase_core/nodes",
        "omnibase_infra": "omnibase_infra/src/omnibase_infra/nodes",
        "omnidash": None,  # TypeScript, no contract.yaml nodes
        "omniintelligence": "omniintelligence/src/omniintelligence/nodes",
        "omnimemory": "omnimemory/src/omnimemory/nodes",
        "omnimarket": "omnimarket/src/omnimarket/nodes",
        "onex_change_control": "onex_change_control/src/onex_change_control/nodes",
    }

    all_entries: list[ModelContractEntry] = []
    for repo_name, nodes_path in repo_map.items():
        if nodes_path is None:
            continue
        nodes_dir = omni_home / nodes_path
        if nodes_dir.is_dir():
            all_entries.extend(scan_contracts(nodes_dir, repo_name))

    return all_entries


def main() -> None:
    """CLI entry point for scan-contract-dependencies."""
    import sys

    omni_home = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    entries = scan_all_repos(omni_home)
    print(f"Scanned {len(entries)} contracts from {omni_home}")
    for entry in entries:
        sub = len(entry.subscribe_topics)
        pub = len(entry.publish_topics)
        print(f"  {entry.repo}/{entry.node_name}: {sub} sub, {pub} pub")
