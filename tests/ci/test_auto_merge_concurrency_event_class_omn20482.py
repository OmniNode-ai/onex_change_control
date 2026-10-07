# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""A check_suite re-attempt never cancels the pull_request run (OMN-20482).

``auto-merge.yml`` keyed its concurrency group by the PR alone, with
``cancel-in-progress: true``. The pull_request run and every check_suite
re-attempt for the same PR therefore shared one group, and the first
check_suite completion to land inside the pull_request run's first seconds
cancelled it at ``actions/checkout``. The annotation on the cancelled job reads
``Canceling since a higher priority waiting request for Auto-Merge-12584
exists``.

The survivor does not replace the cancelled run. A check_suite run executes at
the default branch's sha (``head_sha`` is the tip of ``dev``, ``head_branch`` is
``dev``) and writes its ``Enable Auto-Merge`` check-run there, so the PR head is
left with only the cancelled copy and ``CI Summary`` fails closed on it. Measured
on this repo over the runs created 2026-10-03T00:40Z to 2026-10-04T01:30Z: 343 of
785 pull_request runs by the OCC writer App were cancelled (median 10 s against
60 s for the 411 that succeeded), and 21 heads had ``CI Summary`` failing ONLY on
that cancelled copy, 8 of them the current head of their PR.

Real pairs, each (cancelled pull_request run, check_suite run whose event payload
names the same PR; created 2026-10-03):

* 37153470685 (20:58:57Z) / 37153480335 (20:59:06Z), PR 12584
* 37153432793 (20:58:16Z) / 37153444180 (20:58:27Z), PR 12624
* 37155933509 (21:40:33Z) / 37155941023 (21:40:40Z), PR 12584

The group now carries the event name, so a run is superseded only by a newer run
of its own event class. The test renders the group expression the way Actions
does and replays those pairs through GitHub's concurrency rules (one running and
one pending run per group).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

AUTO_MERGE_WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "auto-merge.yml"
)

# Median wall time of the OCC writer App's pull_request runs that finished
# (411 runs created 2026-10-03T00:40Z to 2026-10-04T01:30Z): it polls for the
# companion's eligibility check, so it is in flight long after a check_suite run
# lands.
_PULL_REQUEST_RUN_SECONDS = 60
# Median wall time of a successful check_suite run in the same window (545 runs).
_CHECK_SUITE_RUN_SECONDS = 22

_SEGMENT = re.compile(r"\$\{\{(.*?)\}\}", re.S)
_LITERAL = re.compile(r"'([^']*)'")
_PART = re.compile(r"(\w+)(?:\[(\d+)\])?")


def _concurrency() -> dict[str, Any]:
    data = yaml.safe_load(AUTO_MERGE_WORKFLOW.read_text())
    concurrency: dict[str, Any] = data["concurrency"]
    return concurrency


def _lookup(path: str, context: dict[str, Any]) -> Any:
    """Resolve a dotted context path; a missing property is null, as in Actions."""
    current: Any = context
    for part in path.split("."):
        match = _PART.fullmatch(part)
        assert match, f"unsupported expression operand: {path!r}"
        if not isinstance(current, dict) or match.group(1) not in current:
            return None
        current = current[match.group(1)]
        if match.group(2) is not None:
            index = int(match.group(2))
            if not isinstance(current, list) or index >= len(current):
                return None
            current = current[index]
    return current


def _evaluate(expression: str, context: dict[str, Any]) -> str:
    """Evaluate ``a || b || 'c'``: the first truthy operand, else the last."""
    value: Any = None
    for operand in (part.strip() for part in expression.split("||")):
        literal = _LITERAL.fullmatch(operand)
        value = literal.group(1) if literal else _lookup(operand, context)
        if value not in (None, "", 0, False):
            break
    return "" if value is None else str(value)


def render_group(event_name: str, event: dict[str, Any]) -> str:
    context = {
        "github": {
            "workflow": "Auto-Merge",
            "event_name": event_name,
            "ref": "refs/heads/dev",
            "event": event,
        }
    }
    template = " ".join(str(_concurrency()["group"]).split())
    return _SEGMENT.sub(lambda m: _evaluate(m.group(1), context), template)


def _pull_request(number: int) -> tuple[str, dict[str, Any]]:
    return "pull_request", {"pull_request": {"number": number}}


def _review(number: int) -> tuple[str, dict[str, Any]]:
    return "pull_request_review", {"pull_request": {"number": number}}


def _check_suite(number: int) -> tuple[str, dict[str, Any]]:
    return "check_suite", {"check_suite": {"pull_requests": [{"number": number}]}}


def _dispatch(number: int) -> tuple[str, dict[str, Any]]:
    return "workflow_dispatch", {"inputs": {"pr_number": str(number)}}


@dataclass(frozen=True)
class Run:
    run_id: int
    event: tuple[str, dict[str, Any]]
    at: int
    seconds: int


def replay(runs: list[Run]) -> dict[int, str]:
    """GitHub's concurrency rules: one running and one pending run per group.

    A new run in a busy group cancels the pending run and, when the workflow sets
    ``cancel-in-progress``, the running one as well; otherwise it waits as the
    pending run.
    """
    cancel_in_progress = bool(_concurrency()["cancel-in-progress"])
    outcome: dict[int, str] = {}
    running: dict[str, tuple[Run, int]] = {}
    pending: dict[str, Run] = {}

    def settle(group: str, now: float) -> None:
        while group in running and running[group][1] <= now:
            _, ended = running.pop(group)
            outcome[running_id[group]] = "success"
            if group in pending:
                nxt = pending.pop(group)
                running[group] = (nxt, ended + nxt.seconds)
                running_id[group] = nxt.run_id

    running_id: dict[str, int] = {}
    for run in sorted(runs, key=lambda r: r.at):
        group = render_group(*run.event)
        settle(group, run.at)
        if group not in running:
            running[group] = (run, run.at + run.seconds)
            running_id[group] = run.run_id
            continue
        if group in pending:
            outcome[pending.pop(group).run_id] = "cancelled"
        if cancel_in_progress:
            outcome[running_id[group]] = "cancelled"
            running[group] = (run, run.at + run.seconds)
            running_id[group] = run.run_id
        else:
            pending[group] = run
    for group in list(running):
        settle(group, float("inf"))
    return outcome


@pytest.mark.parametrize(
    ("pull_request_run", "check_suite_run", "pr", "gap_seconds"),
    [
        (37153470685, 37153480335, 12584, 9),
        (37153432793, 37153444180, 12624, 11),
        (37155933509, 37155941023, 12584, 7),
    ],
)
def test_a_check_suite_run_does_not_cancel_the_pull_request_run_of_its_pr(
    pull_request_run: int, check_suite_run: int, pr: int, gap_seconds: int
) -> None:
    outcome = replay(
        [
            Run(pull_request_run, _pull_request(pr), 0, _PULL_REQUEST_RUN_SECONDS),
            Run(
                check_suite_run, _check_suite(pr), gap_seconds, _CHECK_SUITE_RUN_SECONDS
            ),
        ]
    )
    assert outcome[pull_request_run] == "success", outcome
    assert outcome[check_suite_run] == "success", outcome


def test_a_pull_request_review_run_does_not_cancel_the_pull_request_run() -> None:
    outcome = replay(
        [
            Run(1, _pull_request(7), 0, _PULL_REQUEST_RUN_SECONDS),
            Run(2, _review(7), 2, 20),
        ]
    )
    assert outcome == {1: "success", 2: "success"}


def test_a_dispatch_run_does_not_cancel_the_pull_request_run() -> None:
    outcome = replay(
        [
            Run(1, _pull_request(7), 0, _PULL_REQUEST_RUN_SECONDS),
            Run(2, _dispatch(7), 3, 20),
        ]
    )
    assert outcome == {1: "success", 2: "success"}


def test_a_newer_check_suite_run_still_supersedes_the_older_one_for_the_pr() -> None:
    """The re-attempt path keeps its single flight per PR."""
    outcome = replay(
        [
            Run(1, _check_suite(7), 0, _CHECK_SUITE_RUN_SECONDS),
            Run(2, _check_suite(7), 4, _CHECK_SUITE_RUN_SECONDS),
        ]
    )
    assert outcome == {1: "cancelled", 2: "success"}


def test_a_newer_push_still_supersedes_the_older_pull_request_run() -> None:
    outcome = replay(
        [
            Run(1, _pull_request(7), 0, _PULL_REQUEST_RUN_SECONDS),
            Run(2, _pull_request(7), 30, _PULL_REQUEST_RUN_SECONDS),
        ]
    )
    assert outcome == {1: "cancelled", 2: "success"}


def test_another_prs_check_suite_run_never_cancels_this_pr() -> None:
    outcome = replay(
        [
            Run(1, _pull_request(7), 0, _PULL_REQUEST_RUN_SECONDS),
            Run(2, _check_suite(8), 1, _CHECK_SUITE_RUN_SECONDS),
            Run(3, _pull_request(8), 2, _PULL_REQUEST_RUN_SECONDS),
        ]
    )
    assert outcome == {1: "success", 2: "success", 3: "success"}


def test_every_event_class_carries_its_own_group() -> None:
    groups = {
        render_group(*_pull_request(7)),
        render_group(*_review(7)),
        render_group(*_check_suite(7)),
        render_group(*_dispatch(7)),
    }
    assert len(groups) == 4, groups


def test_the_group_still_names_the_pr_on_every_path() -> None:
    for event in (_pull_request(31), _review(31), _check_suite(31), _dispatch(31)):
        assert render_group(*event).endswith("-31"), render_group(*event)
