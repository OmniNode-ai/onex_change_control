# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression coverage for prompt OCC companion arming (OMN-17922, 2nd pass).

The first OMN-17922 pass made bot-authored OCC evidence companions arm at all.
It did not make them arm promptly. ``Check OCC eligibility preflight status``
took its reading once per event, and the ``pull_request: opened`` run reaches it
before the companion's own eligibility check has started, so arming fell through
to the ``check_suite: completed`` re-attempt path -- whose delivery is neither
immediate nor one-per-PR. Measured on this repo 2026-09-18:

PR 10179, opened 08:15:07, eligibility SUCCESS 08:16:01 (+54s), armed
08:28:27, 12m26s wasted. PR 10181, opened 08:15:51, SUCCESS 08:16:47
(+56s), armed 08:28:28, 11m41s wasted. PR 10180, opened 08:15:29, armed
08:33:42, 18m13s wasted. PR 10183, opened 08:31:39, SUCCESS 08:32:43
(+64s), armed 08:35:47, 3m04s wasted.

Eligibility was green inside ~60s every time, and the product PR behind each
companion stayed BLOCKED on its ``OCC Companion Merged Gate (OMN-15214)`` check
meanwhile. Four lanes hand-armed a companion that morning.

What this module pins is that the remedy is a BOUNDED POLL and not a relaxed
gate:

1. ``Resolve PR and author`` emits a ``companion`` output that is strictly
   narrower than ``arm`` -- true only for the OCC writer App with an
   ``evidence(`` title, false for ``jonahgabriel`` and for every other author.
2. The poll budget is non-zero for exactly that shape on the ``pull_request``
   event, and zero everywhere else, so human PRs and the check_suite path cost
   what they cost today.
3. Executing the step's own bash against a scripted Checks API: a pending
   reading is re-taken until it concludes; a concluded SUCCESS arms; a concluded
   FAILURE exits 1; an exhausted budget defers; and an API that errors or
   returns junk defers. ``defer=false`` is emitted on the SUCCESS path and on no
   other, which is the property the whole gate rests on.
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

RESOLVE_STEP_NAME = "Resolve PR and author"
OCC_GATE_STEP_NAME = "Check OCC eligibility preflight status"
HEAD_SHA = "b70e080498728a94566e9dbd7199e53e94903e32"


def _steps() -> list[dict[str, Any]]:
    loaded = yaml.safe_load(AUTO_MERGE_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    steps = loaded["jobs"]["auto-merge"]["steps"]
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def _step_by_name(name: str) -> dict[str, Any]:
    matches = [step for step in _steps() if step.get("name") == name]
    assert len(matches) == 1, (
        f"expected exactly one step named {name!r}, got {len(matches)}"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# 1. `companion` is emitted, and is strictly narrower than `arm`.
# ---------------------------------------------------------------------------


def _run_resolve(tmp_path: Path, actor: str, title: str) -> dict[str, str]:
    script = _step_by_name(RESOLVE_STEP_NAME)["run"]
    assert isinstance(script, str)
    output_file = tmp_path / "github_output"
    output_file.write_text("", encoding="utf-8")
    env = {
        "PATH": os.environ["PATH"],
        "GITHUB_OUTPUT": str(output_file),
        "GH_TOKEN": "unused",
        "GH_REPO": "OmniNode-ai/onex_change_control",
        "EVENT_NAME": "pull_request",
        "PR_FROM_PAYLOAD": "10179",
        "PR_FROM_DISPATCH": "",
        "CHECK_SUITE_PRS": "",
        "PR_AUTHOR_FROM_PAYLOAD": actor,
        "PR_TITLE_FROM_PAYLOAD": title,
    }
    proc = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    outputs: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        outputs[key] = value
    return outputs


@pytest.mark.parametrize(
    ("actor", "title", "expected_arm", "expected_companion"),
    [
        # The one shape that polls.
        (
            "onexbot-occ-writer[bot]",
            "evidence(OMN-18669): OCC companion for OmniNode-ai/omnimemory#517",
            "true",
            "true",
        ),
        # Arms, never polls: no human PR pays for the wait.
        ("jonahgabriel", "fix(OMN-17922): anything", "true", "false"),
        (
            "jonahgabriel",
            "evidence(OMN-17922): evidence-shaped human title",
            "true",
            "false",
        ),
        # The App with a non-evidence title neither arms nor polls.
        (
            "onexbot-occ-writer[bot]",
            "fix(occ): supersede merged #8075",
            "false",
            "false",
        ),
        # Another bot cannot buy a poll with a forged title.
        ("github-actions[bot]", "evidence(OMN-1): forged shape", "false", "false"),
        ("dependabot[bot]", "evidence(OMN-1): forged shape", "false", "false"),
        ("someone-else", "evidence(OMN-1): forged shape", "false", "false"),
    ],
)
def test_companion_output_is_narrower_than_arm(
    tmp_path: Path,
    actor: str,
    title: str,
    expected_arm: str,
    expected_companion: str,
) -> None:
    outputs = _run_resolve(tmp_path, actor, title)
    assert outputs["arm"] == expected_arm, outputs
    assert outputs["companion"] == expected_companion, outputs
    # `companion` never grants what `arm` withholds.
    if outputs["companion"] == "true":
        assert outputs["arm"] == "true", outputs


# ---------------------------------------------------------------------------
# 2. The budget is non-zero for exactly that shape on exactly that event.
# ---------------------------------------------------------------------------


def test_poll_budget_expression_is_scoped_to_companion_opened_path() -> None:
    env = _step_by_name(OCC_GATE_STEP_NAME)["env"]
    budget = env["ELIGIBILITY_POLL_SECONDS"]
    assert isinstance(budget, str)
    # Both halves of the scope must be in the expression, and the fallback must
    # be the zero budget -- i.e. today's single-reading behaviour.
    assert "steps.resolve.outputs.companion == 'true'" in budget, budget
    assert "github.event_name == 'pull_request'" in budget, budget
    assert "'0'" in budget, budget
    assert "ELIGIBILITY_POLL_INTERVAL_SECONDS" in env


def test_occ_gate_still_gates_on_arm_and_skips_merge_group() -> None:
    condition = _step_by_name(OCC_GATE_STEP_NAME)["if"]
    assert "steps.resolve.outputs.arm == 'true'" in condition
    assert "steps.resolve.outputs.skip != 'true'" in condition
    assert "github.event_name != 'merge_group'" in condition


def test_occ_gate_does_not_take_the_arming_pat() -> None:
    """The poll is read-only; the PAT stays on the two mutating steps."""
    env = _step_by_name(OCC_GATE_STEP_NAME)["env"]
    assert env["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"  # noqa: S105 -- a GitHub expression, not a value
    rendered = yaml.safe_dump(_step_by_name(OCC_GATE_STEP_NAME))
    assert "CROSS_REPO_PAT" not in rendered


# ---------------------------------------------------------------------------
# 3. The step's own bash, against a scripted Checks API.
# ---------------------------------------------------------------------------


def _check_runs_page(status: str, conclusion: str | None) -> str:
    """One Checks API page, as `gh api --paginate --slurp` renders it."""
    concl = "null" if conclusion is None else f'"{conclusion}"'
    return (
        '[{"check_runs":[{"name":"occ-preflight / eligibility","id":1,'
        f'"started_at":"2026-09-18T08:15:23Z","status":"{status}",'
        f'"conclusion":{concl}}}]}}]'
    )


def _run_occ_gate(
    tmp_path: Path,
    readings: list[str],
    poll_seconds: str,
    interval_seconds: str = "1",
    api_exit: int = 0,
) -> tuple[dict[str, str], subprocess.CompletedProcess[str], int]:
    """Execute the occ_gate step's bash with a `gh` that replays `readings`.

    The last reading is repeated once the list is exhausted, so a
    never-concluding check is expressed as a single pending reading.
    """
    script = _step_by_name(OCC_GATE_STEP_NAME)["run"]
    assert isinstance(script, str)

    output_file = tmp_path / "github_output"
    output_file.write_text("", encoding="utf-8")
    counter = tmp_path / "calls"
    counter.write_text("0", encoding="utf-8")
    replies = tmp_path / "replies"
    replies.mkdir()
    for index, body in enumerate(readings):
        (replies / f"{index}").write_text(body, encoding="utf-8")

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
        f"  if [ {api_exit} -ne 0 ]; then\n"
        '    echo "simulated API failure" >&2\n'
        f"    exit {api_exit}\n"
        "  fi\n"
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
        "PR": "10179",
        "ELIGIBILITY_POLL_SECONDS": poll_seconds,
        "ELIGIBILITY_POLL_INTERVAL_SECONDS": interval_seconds,
    }
    proc = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, check=False
    )
    outputs: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        outputs[key] = value
    return outputs, proc, int(counter.read_text(encoding="utf-8"))


def test_zero_budget_takes_exactly_one_reading_and_defers(tmp_path: Path) -> None:
    """The non-companion path is byte-for-byte today's behaviour."""
    outputs, proc, calls = _run_occ_gate(
        tmp_path, [_check_runs_page("in_progress", None)], poll_seconds="0"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "true", proc.stdout
    assert calls == 1, f"zero budget must not poll; took {calls} readings"


def test_already_green_arms_on_the_first_reading(tmp_path: Path) -> None:
    outputs, proc, calls = _run_occ_gate(
        tmp_path, [_check_runs_page("completed", "success")], poll_seconds="30"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "false", proc.stdout
    assert calls == 1, "a terminal first reading must not sleep"


def test_pending_then_success_arms_without_a_check_suite_event(
    tmp_path: Path,
) -> None:
    """The whole point: the `opened` run arms once eligibility concludes."""
    outputs, proc, calls = _run_occ_gate(
        tmp_path,
        [
            _check_runs_page("queued", None),
            _check_runs_page("in_progress", None),
            _check_runs_page("completed", "success"),
        ],
        poll_seconds="30",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "false", proc.stdout
    assert calls == 3, f"expected three readings, took {calls}"


@pytest.mark.parametrize("conclusion", ["failure", "action_required"])
def test_concluded_failure_still_hard_fails_under_the_poll(
    tmp_path: Path, conclusion: str
) -> None:
    outputs, proc, _ = _run_occ_gate(
        tmp_path,
        [
            _check_runs_page("in_progress", None),
            _check_runs_page("completed", conclusion),
        ],
        poll_seconds="30",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert outputs.get("defer") != "false", outputs
    assert "OCC PREFLIGHT FAILED" in proc.stdout


def test_exhausted_budget_defers_exactly_as_before(tmp_path: Path) -> None:
    outputs, proc, calls = _run_occ_gate(
        tmp_path,
        [_check_runs_page("in_progress", None)],
        poll_seconds="2",
        interval_seconds="1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "true", proc.stdout
    assert calls >= 2, "a non-zero budget must re-read at least once"


def test_api_failure_defers_and_never_arms(tmp_path: Path) -> None:
    outputs, proc, _ = _run_occ_gate(
        tmp_path,
        [_check_runs_page("completed", "success")],
        poll_seconds="2",
        interval_seconds="1",
        api_exit=1,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "true", proc.stdout


def test_unparseable_api_body_defers_and_never_arms(tmp_path: Path) -> None:
    outputs, proc, _ = _run_occ_gate(
        tmp_path, ["{not json at all"], poll_seconds="2", interval_seconds="1"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "true", proc.stdout


def test_missing_started_at_still_defers(tmp_path: Path) -> None:
    """A candidate with no start timestamp has ambiguous recency (pre-existing)."""
    page = (
        '[{"check_runs":[{"name":"occ-preflight / eligibility","id":1,'
        '"status":"completed","conclusion":"success"}]}]'
    )
    outputs, proc, _ = _run_occ_gate(
        tmp_path, [page], poll_seconds="2", interval_seconds="1"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "true", proc.stdout


def test_no_eligibility_check_at_all_defers(tmp_path: Path) -> None:
    page = (
        '[{"check_runs":[{"name":"CI Summary","id":9,'
        '"started_at":"2026-09-18T08:16:13Z","status":"completed",'
        '"conclusion":"success"}]}]'
    )
    outputs, proc, _ = _run_occ_gate(
        tmp_path, [page], poll_seconds="2", interval_seconds="1"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert outputs["defer"] == "true", proc.stdout
