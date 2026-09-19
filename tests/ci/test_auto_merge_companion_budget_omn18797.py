# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""The companion arming budget outlives the runner queue (OMN-18797).

OMN-17922's second pass gave a bot OCC evidence companion a 180-second poll on
its own ``pull_request: opened`` run, and made an exhausted budget defer "to the
existing check_suite path". Both halves were wrong under a merge burst, measured
on this repo 2026-09-19:

``onex_change_control#10322`` (evidence for ``OmniNode-ai/omniclaude#2255``)
opened 01:08:45Z. Auto-Merge run 35411715476 resolved ``arm=true
companion=true`` at 01:08:49Z, took 18 readings, every one ``status=missing
conclusion=missing``, and gave up at 01:12:07Z. Its ``occ-preflight /
eligibility`` check-run was CREATED at 01:13:56Z -- 112 seconds after the poll
stopped -- started 01:13:59Z and concluded SUCCESS at 01:14:35Z. The job was
queued, not slow: ``actions/runs/35411720602/jobs`` reports
``created_at=01:13:56Z`` against a run whose ``startedAt`` is 01:08:54Z.

Nothing re-attempted. Between 01:02:31Z and 02:03:56Z not one Auto-Merge run
fired on any event, while at least six check suites completed on that head. The
PR was mergeable throughout; ``autoMergeRequest`` stayed null and a human merged
it by hand at 01:38:50Z. The run that gave up was green.

What this module pins:

1. The companion budget is long enough to outlive the measured queue, and it
   covers ``workflow_dispatch`` as well as ``pull_request``, so re-dispatching
   the workflow at a stuck companion is a real recovery rather than one more
   single reading.
2. An exhausted budget on the companion path FAILS the step. An unarmed
   companion behind a green run is indistinguishable from one nothing ever
   looked at, and telling those apart is the whole point.
3. Every path that carries a PR is keyed by it in the concurrency group. The
   group was ``${{ github.workflow }}-${{ github.event.pull_request.number ||
   github.ref }}``, and that property is empty on both the dispatch and the
   check_suite paths, so every such run for every PR shared one
   ``Auto-Merge-refs/heads/dev`` group under ``cancel-in-progress: true`` --
   cancelling the only automatic re-attempt this workflow has.

Nothing here widens WHAT arms or WHO arms: SUCCESS is still the only arming
reading, a concluded FAILURE still exits 1, no credential moved, and the
non-companion paths still take exactly one reading and defer silently, which
``tests/ci/test_auto_merge_companion_poll_omn17922.py`` continues to pin.
"""

from __future__ import annotations

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
HEAD_SHA = "3679b481a1057bf01b56fd064de428b6bdf1379e"

# The measured worst case from companion open to the eligibility job being
# CREATED, occ#10322 on 2026-09-19: 01:08:45Z -> 01:13:56Z.
MEASURED_QUEUE_DELAY_SECONDS = 311


def _workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(AUTO_MERGE_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _steps() -> list[dict[str, Any]]:
    steps = _workflow()["jobs"]["auto-merge"]["steps"]
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def _step_by_name(name: str) -> dict[str, Any]:
    matches = [step for step in _steps() if step.get("name") == name]
    assert len(matches) == 1, (
        f"expected exactly one step named {name!r}, got {len(matches)}"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# 1. The budget outlives the measured queue, and covers the recovery path.
# ---------------------------------------------------------------------------


def test_companion_budget_outlives_the_measured_runner_queue() -> None:
    budget = _step_by_name(OCC_GATE_STEP_NAME)["env"]["ELIGIBILITY_POLL_SECONDS"]
    assert isinstance(budget, str)
    seconds = [
        int(token.strip("'")) for token in budget.split() if token.strip("'").isdigit()
    ]
    armed_budget = max(seconds)
    assert armed_budget > MEASURED_QUEUE_DELAY_SECONDS, (
        f"budget {armed_budget}s does not outlive the measured "
        f"{MEASURED_QUEUE_DELAY_SECONDS}s queue that produced occ#10322"
    )


def test_budget_covers_the_dispatch_recovery_path() -> None:
    """`gh workflow run ... -f pr_number=N` must poll, not take one reading."""
    budget = _step_by_name(OCC_GATE_STEP_NAME)["env"]["ELIGIBILITY_POLL_SECONDS"]
    assert "github.event_name == 'workflow_dispatch'" in budget, budget
    assert "github.event_name == 'pull_request'" in budget, budget


def test_budget_is_still_scoped_to_the_companion_shape() -> None:
    """The widening is WHEN a companion waits, never WHO waits."""
    budget = _step_by_name(OCC_GATE_STEP_NAME)["env"]["ELIGIBILITY_POLL_SECONDS"]
    assert "steps.resolve.outputs.companion == 'true'" in budget, budget
    assert "'0'" in budget, budget
    assert "check_suite" not in budget, budget


def test_occ_gate_receives_the_companion_flag() -> None:
    env = _step_by_name(OCC_GATE_STEP_NAME)["env"]
    assert env["COMPANION"] == "${{ steps.resolve.outputs.companion }}", env


def test_occ_gate_still_holds_no_arming_credential() -> None:
    """The reading stays read-only; no credential moved for this change."""
    rendered = yaml.safe_dump(_step_by_name(OCC_GATE_STEP_NAME))
    assert "CROSS_REPO_PAT" not in rendered


# ---------------------------------------------------------------------------
# 2. Every path that carries a PR is keyed by it.
# ---------------------------------------------------------------------------


def test_concurrency_group_is_keyed_per_pr_on_every_re_attempt_path() -> None:
    group = " ".join(str(_workflow()["concurrency"]["group"]).split())
    assert "github.event.pull_request.number" in group, group
    assert "github.event.inputs.pr_number" in group, group
    assert "github.event.check_suite.pull_requests[0].number" in group, group
    # `github.ref` stays the last-resort key, never ahead of a PR key.
    assert group.index("github.event.inputs.pr_number") < group.index("github.ref"), (
        group
    )
    assert group.index(
        "github.event.check_suite.pull_requests[0].number"
    ) < group.index("github.ref"), group


def test_concurrency_still_cancels_in_progress() -> None:
    assert _workflow()["concurrency"]["cancel-in-progress"] is True


# ---------------------------------------------------------------------------
# 3. The step's own bash, against a check that never starts.
# ---------------------------------------------------------------------------


def _missing_page() -> str:
    """A Checks API page with no eligibility check at all -- the measured case."""
    return (
        '[{"check_runs":[{"name":"CI Summary","id":9,'
        '"started_at":"2026-09-19T01:09:16Z","status":"in_progress",'
        '"conclusion":null}]}]'
    )


def _page(status: str, conclusion: str | None) -> str:
    concl = "null" if conclusion is None else f'"{conclusion}"'
    return (
        '[{"check_runs":[{"name":"occ-preflight / eligibility","id":1,'
        f'"started_at":"2026-09-19T01:13:59Z","status":"{status}",'
        f'"conclusion":{concl}}}]}}]'
    )


def _run_occ_gate(
    tmp_path: Path,
    readings: list[str],
    *,
    companion: str,
    poll_seconds: str = "2",
) -> tuple[dict[str, str], subprocess.CompletedProcess[str]]:
    script = _step_by_name(OCC_GATE_STEP_NAME)["run"]
    assert isinstance(script, str)

    output_file = tmp_path / "github_output"
    output_file.write_text("", encoding="utf-8")
    counter = tmp_path / "calls"
    counter.write_text("0", encoding="utf-8")
    replies = tmp_path / "replies"
    replies.mkdir()
    for index, body in enumerate(readings):
        (replies / str(index)).write_text(body, encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_path = bin_dir / "gh"
    gh_path.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "pr" ] && [ "$2" = "view" ]; then\n'
        f'  echo "{HEAD_SHA}"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "api" ]; then\n'
        f'  n="$(cat "{counter}")"\n'
        f'  echo $(( n + 1 )) > "{counter}"\n'
        f"  last=$(( {len(readings)} - 1 ))\n"
        '  [ "$n" -gt "$last" ] && n="$last"\n'
        f'  cat "{replies}/$n"\n'
        "  exit 0\n"
        "fi\n"
        'echo "unexpected gh args: $*" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    gh_path.chmod(gh_path.stat().st_mode | stat.S_IXUSR)

    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output_file),
        "GH_TOKEN": "unused",
        "GH_REPO": "OmniNode-ai/onex_change_control",
        "PR": "10322",
        "ELIGIBILITY_POLL_SECONDS": poll_seconds,
        "ELIGIBILITY_POLL_INTERVAL_SECONDS": "1",
        "COMPANION": companion,
    }
    proc = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, check=False
    )
    outputs: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        outputs[key] = value
    return outputs, proc


def test_a_companion_whose_check_never_starts_fails_the_run(tmp_path: Path) -> None:
    """The occ#10322 shape: green-and-unarmed is no longer an outcome."""
    outputs, proc = _run_occ_gate(tmp_path, [_missing_page()], companion="true")
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert outputs.get("defer") == "true", outputs
    assert "10322" in proc.stdout, proc.stdout
    assert "NOT armed" in proc.stdout, proc.stdout
    assert "::error::" in proc.stdout, proc.stdout


def test_the_failure_names_the_hand_recovery_command(tmp_path: Path) -> None:
    _, proc = _run_occ_gate(tmp_path, [_missing_page()], companion="true")
    assert "gh workflow run auto-merge.yml" in proc.stdout, proc.stdout
    assert "pr_number=10322" in proc.stdout, proc.stdout


def test_a_companion_check_that_never_concludes_also_fails(tmp_path: Path) -> None:
    outputs, proc = _run_occ_gate(
        tmp_path, [_page("in_progress", None)], companion="true"
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert outputs.get("defer") != "false", outputs


def test_a_companion_that_goes_green_inside_the_budget_arms(tmp_path: Path) -> None:
    outputs, proc = _run_occ_gate(
        tmp_path,
        [_missing_page(), _page("completed", "success")],
        companion="true",
        poll_seconds="5",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "false", outputs


@pytest.mark.parametrize("conclusion", ["failure", "action_required"])
def test_a_concluded_failure_still_takes_its_own_branch(
    tmp_path: Path, conclusion: str
) -> None:
    """The pre-existing hard fail keeps its own message, not the new one."""
    outputs, proc = _run_occ_gate(
        tmp_path, [_page("completed", conclusion)], companion="true"
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "OCC PREFLIGHT FAILED" in proc.stdout, proc.stdout
    assert outputs.get("defer") != "false", outputs


def test_a_non_companion_exhaustion_is_unchanged(tmp_path: Path) -> None:
    """A human PR still defers green; this change costs it nothing."""
    outputs, proc = _run_occ_gate(tmp_path, [_missing_page()], companion="false")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "true", outputs


def test_the_zero_budget_path_never_reaches_the_new_failure(tmp_path: Path) -> None:
    """The check_suite path carries no budget, so it cannot have exhausted one."""
    outputs, proc = _run_occ_gate(
        tmp_path, [_missing_page()], companion="true", poll_seconds="0"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "true", outputs


# ---------------------------------------------------------------------------
# 4. No credential moved. A hand-off would need one nothing in this org holds.
# ---------------------------------------------------------------------------


def test_the_pat_read_count_in_this_job_is_untouched() -> None:
    """OMN-16373 ratchets this job at two PAT reads; this change adds none.

    A re-dispatch on exhaustion would be the shape that survives a lost runner,
    and it needs a third. `secrets.GITHUB_TOKEN` cannot send a
    `workflow_dispatch` that creates a run, and the `onexbot-occ-writer`
    installation holds `actions: read` (read live 2026-09-19 from
    /orgs/OmniNode-ai/installations), so the PAT is the only candidate and
    widening that ratchet is an authorization decision, not this change's.
    """
    holders = [
        step.get("name")
        for step in _steps()
        if any(
            "CROSS_REPO_PAT" in str(value) for value in (step.get("env") or {}).values()
        )
    ]
    assert holders == [
        "Enable auto-merge",
        "Enqueue armed PR and verify it entered the queue",
    ], (
        "the two merge-state-mutating steps hold the PAT and nothing else "
        f"does; got {holders}"
    )
