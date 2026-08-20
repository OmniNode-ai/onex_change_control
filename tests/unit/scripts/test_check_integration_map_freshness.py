# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for the integration-map freshness checker."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from onex_change_control.scripts import check_integration_map_freshness as checker

if TYPE_CHECKING:
    from pathlib import Path


def _write_text(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.unit
def test_topics_present_in_map_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    integration_map = _write_text(
        tmp_path,
        "integration-map.md",
        """
| Topic |
| --- |
| onex.cmd.omnimarket.pr-lifecycle-orchestrator-start.v1 |
""",
    )
    plan = _write_text(
        tmp_path,
        "plan.md",
        """
Use topic `onex.cmd.omnimarket.pr-lifecycle-orchestrator-start.v1`.
""",
    )

    rc = checker.main(["--map", str(integration_map), str(plan)])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["missing_topics"] == []


@pytest.mark.unit
def test_missing_topic_fails_strict_and_warns_advisory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    integration_map = _write_text(
        tmp_path,
        "integration-map.md",
        """
| Topic |
| --- |
| onex.cmd.omnimarket.pr-lifecycle-orchestrator-start.v1 |
""",
    )
    plan = _write_text(
        tmp_path,
        "plan.md",
        """
New topic: `onex.evt.omniclaude.task-delegated.v1`
""",
    )

    rc = checker.main(["--map", str(integration_map), str(plan)])
    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fail"
    assert report["missing_topics"] == [
        {
            "path": plan.as_posix(),
            "topics": ["onex.evt.omniclaude.task-delegated.v1"],
        }
    ]

    rc = checker.main(["--map", str(integration_map), str(plan), "--advisory"])
    assert rc == 0
    advisory_report = json.loads(capsys.readouterr().out)
    assert advisory_report["status"] == "warning"
