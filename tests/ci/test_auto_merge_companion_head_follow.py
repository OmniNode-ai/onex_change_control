# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""The companion arming poll follows the live head (OMN-19867).

The OCC writer pushes a companion as three commits: author, self-bind about
seven seconds later, then the executed test_passes receipts (OMN-16859) 45-70
seconds after that. ``Check OCC eligibility preflight status`` used to resolve
the head once, before its poll, and to stop at the first ``completed`` reading.
The self-bind head's eligibility then concluded ``failure`` (the receipts were
not pushed yet) or ``cancelled`` (the next push superseded it). The step exited
1, and the final head went green with no Auto-Merge event left to arm it.
Measured 2026-09-27: 13 of the 15 most recent companions ended their only
Auto-Merge run red and waited for a lane to arm them by hand.

These tests execute the step's own bash against a stub ``gh`` whose PR head
moves on a script, and whose Checks API answers per head. They pin that:

1. a poll that sees a failure or a cancel on a superseded head keeps going and
   arms on the new head's SUCCESS;
2. a failure on a head that holds still for the settle window still exits 1,
   and a zero-budget failure still exits 1 on its first reading, so the change
   loosens nothing;
3. the zero-budget path never reads the head a second time.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTO_MERGE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "auto-merge.yml"
OCC_GATE_STEP_NAME = "Check OCC eligibility preflight status"

SELF_BIND_HEAD = "19368f39df010d256646121b5295861b4e63373c"
RECEIPTS_HEAD = "d06a63b6aa3e3774b431209a56b665286c5fd917"


def _occ_gate_step() -> dict[str, Any]:
    loaded = yaml.safe_load(AUTO_MERGE_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    steps = [
        step
        for step in loaded["jobs"]["auto-merge"]["steps"]
        if isinstance(step, dict) and step.get("name") == OCC_GATE_STEP_NAME
    ]
    assert len(steps) == 1
    return steps[0]


def _page(status: str, conclusion: str | None) -> str:
    concl = "null" if conclusion is None else f'"{conclusion}"'
    return (
        '[{"check_runs":[{"name":"occ-preflight / eligibility","id":1,'
        f'"started_at":"2026-09-27T12:46:45Z","status":"{status}",'
        f'"conclusion":{concl}}}]}}]'
    )


def _run(
    tmp_path: Path,
    heads: list[str],
    readings: dict[str, list[str]],
    poll_seconds: str,
    settle_seconds: str,
) -> tuple[dict[str, str], subprocess.CompletedProcess[str], int]:
    """Run the step with a stub gh.

    ``gh pr view`` answers ``heads`` in order, repeating the last. ``gh api``
    answers from ``readings[<head in the URL>]`` in order, repeating the last.
    Returns the step outputs, the process and the number of head reads.
    """
    script = _occ_gate_step()["run"]
    assert isinstance(script, str)

    output_file = tmp_path / "github_output"
    output_file.write_text("", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    (state / "heads.json").write_text(json.dumps(heads), encoding="utf-8")
    (state / "readings.json").write_text(json.dumps(readings), encoding="utf-8")

    stub = tmp_path / "gh_stub.py"
    stub.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        f"state = Path({str(state)!r})\n"
        "def nxt(key, seq):\n"
        "    f = state / (key + '.n')\n"
        "    n = int(f.read_text()) if f.exists() else 0\n"
        "    f.write_text(str(n + 1))\n"
        "    return seq[min(n, len(seq) - 1)]\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['pr', 'view']:\n"
        "    print(nxt('heads', json.loads((state / 'heads.json').read_text())))\n"
        "    sys.exit(0)\n"
        "if args[:1] == ['api']:\n"
        "    readings = json.loads((state / 'readings.json').read_text())\n"
        "    for sha, seq in readings.items():\n"
        "        if '/commits/' + sha + '/' in args[1]:\n"
        "            print(nxt(sha, seq))\n"
        "            sys.exit(0)\n"
        "    print('[{\"check_runs\":[]}]')\n"
        "    sys.exit(0)\n"
        "print('unexpected gh args: ' + ' '.join(args), file=sys.stderr)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_path = bin_dir / "gh"
    gh_path.write_text(
        f'#!/usr/bin/env bash\nexec python3 "{stub}" "$@"\n', encoding="utf-8"
    )
    gh_path.chmod(gh_path.stat().st_mode | stat.S_IXUSR)

    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output_file),
        "GH_TOKEN": "unused",
        "GH_REPO": "OmniNode-ai/onex_change_control",
        "PR": "11634",
        "COMPANION": "true" if poll_seconds != "0" else "false",
        "ELIGIBILITY_POLL_SECONDS": poll_seconds,
        "ELIGIBILITY_POLL_INTERVAL_SECONDS": "1",
        "ELIGIBILITY_FAILURE_SETTLE_SECONDS": settle_seconds,
    }
    proc = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    outputs: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        outputs[key] = value
    head_file = state / "heads.n"
    head_reads = int(head_file.read_text()) if head_file.exists() else 0
    return outputs, proc, head_reads


def test_settle_window_is_declared_on_the_step() -> None:
    env = _occ_gate_step()["env"]
    assert env["ELIGIBILITY_FAILURE_SETTLE_SECONDS"] == "180"


def test_moved_head_success_arms_after_a_failure_on_the_self_bind_head(
    tmp_path: Path,
) -> None:
    """OCC#11634's shape: failure on the self-bind head, then a receipts push."""
    outputs, proc, _ = _run(
        tmp_path,
        heads=[SELF_BIND_HEAD, SELF_BIND_HEAD, SELF_BIND_HEAD, RECEIPTS_HEAD],
        readings={
            SELF_BIND_HEAD: [_page("in_progress", None), _page("completed", "failure")],
            RECEIPTS_HEAD: [_page("queued", None), _page("completed", "success")],
        },
        poll_seconds="60",
        settle_seconds="30",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "false", proc.stdout
    assert f"head moved {SELF_BIND_HEAD} -> {RECEIPTS_HEAD}" in proc.stdout


def test_cancelled_then_success_on_the_new_head_arms(tmp_path: Path) -> None:
    """OCC#11629's shape: the stale head's check is cancelled by the next push."""
    outputs, proc, _ = _run(
        tmp_path,
        heads=[SELF_BIND_HEAD, SELF_BIND_HEAD, RECEIPTS_HEAD],
        readings={
            SELF_BIND_HEAD: [_page("completed", "cancelled")],
            RECEIPTS_HEAD: [_page("in_progress", None), _page("completed", "success")],
        },
        poll_seconds="60",
        settle_seconds="30",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "false", proc.stdout


def test_failure_on_an_unmoving_head_still_fails_after_the_settle_window(
    tmp_path: Path,
) -> None:
    outputs, proc, _ = _run(
        tmp_path,
        heads=[RECEIPTS_HEAD],
        readings={RECEIPTS_HEAD: [_page("completed", "failure")]},
        poll_seconds="60",
        settle_seconds="2",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert outputs.get("defer") != "false", outputs
    assert "OCC PREFLIGHT FAILED" in proc.stdout
    assert RECEIPTS_HEAD in proc.stdout


def test_cancel_on_an_unmoving_head_still_fails_after_the_settle_window(
    tmp_path: Path,
) -> None:
    outputs, proc, _ = _run(
        tmp_path,
        heads=[RECEIPTS_HEAD],
        readings={RECEIPTS_HEAD: [_page("completed", "cancelled")]},
        poll_seconds="60",
        settle_seconds="2",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert outputs.get("defer") != "false", outputs
    assert "NOT armed" in proc.stdout


def test_zero_budget_failure_still_fails_on_the_first_reading(
    tmp_path: Path,
) -> None:
    """Human PRs and check_suite: one head read, one reading, exit 1."""
    outputs, proc, head_reads = _run(
        tmp_path,
        heads=[SELF_BIND_HEAD, RECEIPTS_HEAD],
        readings={
            SELF_BIND_HEAD: [_page("completed", "failure")],
            RECEIPTS_HEAD: [_page("completed", "success")],
        },
        poll_seconds="0",
        settle_seconds="180",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert outputs.get("defer") != "false", outputs
    assert head_reads == 1, f"zero budget must not re-read the head; read {head_reads}"


def test_success_on_a_superseded_head_is_not_read_as_the_live_verdict(
    tmp_path: Path,
) -> None:
    """A green stale head does not arm over a live head that is still failing."""
    outputs, proc, _ = _run(
        tmp_path,
        heads=[RECEIPTS_HEAD],
        readings={
            SELF_BIND_HEAD: [_page("completed", "success")],
            RECEIPTS_HEAD: [_page("completed", "failure")],
        },
        poll_seconds="60",
        settle_seconds="2",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert outputs.get("defer") != "false", outputs
