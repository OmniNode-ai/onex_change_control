# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Hand-authored OCC evidence companions get the in-run poll (OMN-17427).

PR #13180's Auto-Merge run 37641650373 took one deferred eligibility reading
and received no check_suite re-fire. Execute the workflow's resolve bash on
both poll-bearing events to pin companion classification without widening arm.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTO_MERGE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "auto-merge.yml"
INCIDENT_TITLE = "evidence(OMN-17427): hand-authored companion for omniclaude#2587"


def _resolve_script() -> str:
    loaded = yaml.safe_load(AUTO_MERGE_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    steps = loaded["jobs"]["auto-merge"]["steps"]
    matches = [step for step in steps if step.get("name") == "Resolve PR and author"]
    assert len(matches) == 1
    assert matches[0]["id"] == "resolve"
    script = matches[0]["run"]
    assert isinstance(script, str)
    return script


def _run_resolve(tmp_path: Path, event_name: str, title: str) -> dict[str, str]:
    output_file = tmp_path / "github_output"
    output_file.write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_path = bin_dir / "gh"
    gh_path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [ "$EVENT_NAME" = "workflow_dispatch" ]; then
  case "$*" in
    "pr view 13180 --json author --jq .author.login")
      echo "jonahgabriel" ;;
    "pr view 13180 --json title --jq .title")
      echo "$SHIM_TITLE" ;;
    *) echo "unexpected gh args: $*" >&2; exit 1 ;;
  esac
  exit 0
fi
echo "unexpected gh call on $EVENT_NAME: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    gh_path.chmod(gh_path.stat().st_mode | stat.S_IXUSR)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output_file),
        "GH_TOKEN": "unused",
        "GH_REPO": "OmniNode-ai/onex_change_control",
        "EVENT_NAME": event_name,
        "PR_FROM_PAYLOAD": "13180" if event_name == "pull_request" else "",
        "PR_FROM_DISPATCH": "13180" if event_name == "workflow_dispatch" else "",
        "CHECK_SUITE_PRS": "",
        "PR_AUTHOR_FROM_PAYLOAD": (
            "jonahgabriel" if event_name == "pull_request" else ""
        ),
        "PR_TITLE_FROM_PAYLOAD": title if event_name == "pull_request" else "",
        "SHIM_TITLE": title,
    }
    proc = subprocess.run(
        ["bash", "-c", _resolve_script()],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    outputs: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        outputs[key] = value
    return outputs


@pytest.mark.parametrize("event_name", ["pull_request", "workflow_dispatch"])
@pytest.mark.parametrize(
    ("title", "expected_companion"),
    [
        (INCIDENT_TITLE, "true"),
        ("fix(OMN-17427): ordinary human PR", "false"),
    ],
)
def test_hand_authored_companion_classification(
    tmp_path: Path, event_name: str, title: str, expected_companion: str
) -> None:
    outputs = _run_resolve(tmp_path, event_name, title)
    assert outputs == {
        "pr": "13180",
        "actor": "jonahgabriel",
        "arm": "true",
        "companion": expected_companion,
        "skip": "false",
    }
