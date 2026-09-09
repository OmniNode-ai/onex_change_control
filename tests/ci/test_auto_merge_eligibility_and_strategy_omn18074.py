# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression tests for exact-head eligibility and merge strategy (OMN-18074).

These tests execute the ``run:`` bodies from ``auto-merge.yml`` with a small
``gh`` shim.  They deliberately exercise the workflow's shell rather than a
parallel Python implementation of its decisions.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTO_MERGE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "auto-merge.yml"
BASE_COMMIT = "4def4e4df3add8996465bb5698b3b03e9a5b748d"
ELIGIBILITY_STEP = "Check OCC eligibility preflight status"
STRATEGY_STEP = "Enable auto-merge"
HEAD_SHA = "a" * 40


def _workflow_steps(workflow_text: str) -> list[dict[str, Any]]:
    loaded = yaml.safe_load(workflow_text)
    assert isinstance(loaded, dict)
    jobs = loaded["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["auto-merge"]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def _step_script(workflow_text: str, name: str) -> str:
    matches = [
        step for step in _workflow_steps(workflow_text) if step.get("name") == name
    ]
    assert len(matches) == 1, f"expected one {name!r}, got {len(matches)}"
    script = matches[0].get("run")
    assert isinstance(script, str)
    return script


def _current_workflow() -> str:
    return AUTO_MERGE_WORKFLOW.read_text(encoding="utf-8")


def _base_workflow() -> str:
    completed = subprocess.run(
        ["git", "show", f"{BASE_COMMIT}:.github/workflows/auto-merge.yml"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _run_script(
    tmp_path: Path,
    script: str,
    gh_body: str,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], str, str]:
    output_path = tmp_path / "github_output"
    output_path.write_text("", encoding="utf-8")
    log_path = tmp_path / "gh-argv.log"
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    gh_path = bin_path / "gh"
    gh_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"LOG_PATH={json.dumps(str(log_path))}\n" + gh_body,
        encoding="utf-8",
    )
    gh_path.chmod(gh_path.stat().st_mode | stat.S_IXUSR)

    env = {
        "PATH": f"{bin_path}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output_path),
        "GH_TOKEN": "unused",
        "GH_REPO": "OmniNode-ai/onex_change_control",
        "PR": "8674",
        "EVENT_NAME": "pull_request",
    }
    if extra_env:
        env.update(extra_env)
    completed = subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = output_path.read_text(encoding="utf-8")
    argv = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    return completed, output, argv


def _check_runs_shim(
    *,
    first_page: list[dict[str, Any]],
    second_page: list[dict[str, Any]],
    api_error: bool = False,
    malformed: bool = False,
    malformed_second_page: bool = False,
) -> str:
    pages = json.dumps(
        [
            {
                "check_runs": first_page,
                "total_count": len(first_page) + len(second_page),
            },
            {
                "check_runs": second_page,
                "total_count": len(first_page) + len(second_page),
            },
        ]
    )
    if malformed_second_page:
        pages = json.dumps(
            [
                {
                    "check_runs": first_page,
                    "total_count": len(first_page) + len(second_page),
                },
                {"total_count": len(first_page) + len(second_page)},
            ]
        )
    first = json.dumps({"check_runs": first_page, "total_count": len(first_page)})
    return f"""\
printf '%s\\n' "$*" >> "$LOG_PATH"
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  printf '%s\\n' "$HEAD_SHA"
  exit 0
fi
if [ "$1" = "api" ] && [[ "$*" == *"check-runs"* ]]; then
  if {str(api_error).lower()}; then
    echo "check-runs lookup failed" >&2
    exit 42
  fi
  if {str(malformed).lower()}; then
    printf '%s\\n' '{{}}'
    exit 0
  fi
  if [[ "$*" == *"--paginate"* ]]; then
            printf '%s\\n' {shlex.quote(pages)}
  else
    printf '%s\\n' {shlex.quote(first)}
  fi
  exit 0
fi
echo "unexpected gh args: $*" >&2
exit 91
"""


def _check_run(
    name: str, *, conclusion: str | None, status: str, run_id: int = 1
) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": name,
        "head_sha": HEAD_SHA,
        "conclusion": conclusion,
        "status": status,
        "started_at": "2026-09-09T00:00:00Z",
    }


def _eligibility_env() -> dict[str, str]:
    return {"HEAD_SHA": HEAD_SHA}


@pytest.mark.parametrize("first_page_size", [31, 100])
def test_exact_head_success_beyond_api_page_is_selected(
    tmp_path: Path, first_page_size: int
) -> None:
    first_page = [
        _check_run("unrelated", conclusion="success", status="completed")
        for _ in range(first_page_size)
    ]
    second_page = [
        _check_run(
            "occ-preflight / eligibility", conclusion="success", status="completed"
        )
    ]
    completed, output, argv = _run_script(
        tmp_path,
        _step_script(_current_workflow(), ELIGIBILITY_STEP),
        _check_runs_shim(first_page=first_page, second_page=second_page),
        _eligibility_env(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "defer=false" in output
    assert f"commits/{HEAD_SHA}/check-runs?per_page=100" in argv
    assert "--paginate --slurp" in argv


@pytest.mark.parametrize(
    ("status", "conclusion", "started_at"),
    [
        ("in_progress", None, "2026-09-09T00:00:00Z"),
        ("queued", None, None),
        ("completed", "failure", "2026-09-09T00:00:00Z"),
    ],
)
def test_latest_exact_head_non_success_masks_older_success(
    tmp_path: Path, status: str, conclusion: str | None, started_at: str | None
) -> None:
    old_success = _check_run("eligibility", conclusion="success", status="completed")
    old_success["started_at"] = "2026-09-08T00:00:00Z"
    latest = _check_run(
        "occ-preflight / eligibility", conclusion=conclusion, status=status
    )
    latest["started_at"] = started_at
    completed, output, _ = _run_script(
        tmp_path,
        _step_script(_current_workflow(), ELIGIBILITY_STEP),
        _check_runs_shim(first_page=[old_success], second_page=[latest]),
        _eligibility_env(),
    )
    if conclusion == "failure":
        assert completed.returncode != 0, completed.stdout + completed.stderr
    else:
        assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "defer=false" not in output
    if started_at is None:
        assert "no started_at" in completed.stdout


def test_older_incomplete_candidate_does_not_mask_newer_completed_success(
    tmp_path: Path,
) -> None:
    older_pending = _check_run("eligibility", conclusion=None, status="in_progress")
    older_pending["started_at"] = "2026-09-08T00:00:00Z"
    newer_success = _check_run(
        "occ-preflight / eligibility", conclusion="success", status="completed"
    )
    newer_success["started_at"] = "2026-09-09T00:00:00Z"
    completed, output, _ = _run_script(
        tmp_path,
        _step_script(_current_workflow(), ELIGIBILITY_STEP),
        _check_runs_shim(first_page=[older_pending], second_page=[newer_success]),
        _eligibility_env(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "defer=false" in output


def test_started_at_order_wins_when_completion_order_is_overlapped(
    tmp_path: Path,
) -> None:
    older_success = _check_run("eligibility", conclusion="success", status="completed")
    older_success.update(
        id=101,
        started_at="2026-09-09T00:01:00Z",
        completed_at="2026-09-09T00:10:00Z",
    )
    newer_failure = _check_run(
        "occ-preflight / eligibility", conclusion="failure", status="completed"
    )
    newer_failure.update(
        id=102,
        started_at="2026-09-09T00:02:00Z",
        completed_at="2026-09-09T00:03:00Z",
    )
    completed, output, _ = _run_script(
        tmp_path,
        _step_script(_current_workflow(), ELIGIBILITY_STEP),
        _check_runs_shim(first_page=[older_success], second_page=[newer_failure]),
        _eligibility_env(),
    )
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "defer=false" not in output


@pytest.mark.parametrize(
    "missing_field",
    ["status", "conclusion"],
)
def test_missing_eligibility_fields_fail_closed(
    tmp_path: Path, missing_field: str
) -> None:
    candidate = _check_run(
        "occ-preflight / eligibility", conclusion="success", status="completed"
    )
    candidate.pop(missing_field)
    completed, output, _ = _run_script(
        tmp_path,
        _step_script(_current_workflow(), ELIGIBILITY_STEP),
        _check_runs_shim(first_page=[candidate], second_page=[]),
        _eligibility_env(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "defer=true" in output
    assert "defer=false" not in output


def test_unknown_eligibility_status_fails_closed(tmp_path: Path) -> None:
    candidate = _check_run(
        "occ-preflight / eligibility", conclusion="success", status="unknown"
    )
    completed, output, _ = _run_script(
        tmp_path,
        _step_script(_current_workflow(), ELIGIBILITY_STEP),
        _check_runs_shim(first_page=[candidate], second_page=[]),
        _eligibility_env(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "defer=true" in output
    assert "defer=false" not in output


def test_malformed_eligibility_id_fails_closed(tmp_path: Path) -> None:
    candidate = _check_run(
        "occ-preflight / eligibility", conclusion="success", status="completed"
    )
    candidate["id"] = "not-a-number"
    completed, output, _ = _run_script(
        tmp_path,
        _step_script(_current_workflow(), ELIGIBILITY_STEP),
        _check_runs_shim(first_page=[candidate], second_page=[]),
        _eligibility_env(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "defer=true" in output
    assert "defer=false" not in output


def test_pinned_base_reproduces_missing_second_page_success(tmp_path: Path) -> None:
    script = _step_script(_base_workflow(), ELIGIBILITY_STEP)
    completed, output, _ = _run_script(
        tmp_path,
        script,
        _check_runs_shim(
            first_page=[
                _check_run("unrelated", conclusion="success", status="completed")
                for _ in range(100)
            ],
            second_page=[
                _check_run(
                    "occ-preflight / eligibility",
                    conclusion="success",
                    status="completed",
                )
            ],
        ),
        _eligibility_env(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "defer=true" in output


def test_pinned_base_uses_bare_auto_for_no_queue(tmp_path: Path) -> None:
    completed, _, argv = _run_script(
        tmp_path,
        _step_script(_base_workflow(), STRATEGY_STEP),
        _strategy_shim(queue="null"),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    merge_lines = [line for line in argv.splitlines() if line.startswith("pr merge ")]
    assert len(merge_lines) == 1, argv
    assert "--auto" in merge_lines[0]
    assert "--squash" not in merge_lines[0]


def test_check_runs_api_error_defers_without_stale_success(tmp_path: Path) -> None:
    completed, output, _ = _run_script(
        tmp_path,
        _step_script(_current_workflow(), ELIGIBILITY_STEP),
        _check_runs_shim(first_page=[], second_page=[], api_error=True),
        _eligibility_env(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "defer=true" in output
    assert "defer=false" not in output


def test_malformed_check_runs_response_defers_without_stale_success(
    tmp_path: Path,
) -> None:
    completed, output, _ = _run_script(
        tmp_path,
        _step_script(_current_workflow(), ELIGIBILITY_STEP),
        _check_runs_shim(first_page=[], second_page=[], malformed=True),
        _eligibility_env(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Could not parse paginated OCC eligibility checks" in completed.stdout
    assert "defer=true" in output
    assert "defer=false" not in output


def test_malformed_check_runs_page_defers_without_partial_success(
    tmp_path: Path,
) -> None:
    completed, output, _ = _run_script(
        tmp_path,
        _step_script(_current_workflow(), ELIGIBILITY_STEP),
        _check_runs_shim(
            first_page=[
                _check_run(
                    "occ-preflight / eligibility",
                    conclusion="success",
                    status="completed",
                )
            ],
            second_page=[],
            malformed_second_page=True,
        ),
        _eligibility_env(),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "defer=true" in output
    assert "defer=false" not in output


def _strategy_shim(
    *,
    queue: str,
    graphql_error: bool = False,
    squash_allowed: str = "true",
    base_ref: str = "dev",
) -> str:
    queue_state = "queue" if queue != "null" else "no_queue"
    graphql = (
        "echo 'merge queue lookup failed' >&2; exit 42"
        if graphql_error
        else f"printf '%s\\n' {shlex.quote(queue_state)}"
    )
    return f"""\
printf '%s\\n' "$*" >> "$LOG_PATH"
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  printf '%s\\n' {json.dumps(base_ref)}
  exit 0
fi
if [ "$1" = "api" ] && [ "$2" = "graphql" ]; then
  {graphql}
  exit $?
fi
if [ "$1" = "api" ] && [[ "$*" == *"repos/"* ]]; then
  printf '%s\\n' {json.dumps(squash_allowed)}
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "merge" ]; then
  exit 0
fi
echo "unexpected gh args: $*" >&2
exit 91
"""


def _strategy_shape_shim(*, graphql_payload: str, squash_allowed: str = "true") -> str:
    """Apply the workflow's jq projection to a raw GraphQL response fixture."""
    queue_filter = (
        'if (.data | type) != "object" then '
        'error("GraphQL data missing or invalid") '
        'elif (.data.repository | type) != "object" then '
        'error("repository missing or invalid") '
        'elif (.data.repository | has("mergeQueue") | not) then '
        'error("mergeQueue field missing") '
        'elif .data.repository.mergeQueue == null then "no_queue" '
        'elif ((.data.repository.mergeQueue | type) == "object" '
        'and (.data.repository.mergeQueue.id | type) == "string" '
        'and (.data.repository.mergeQueue.id | length) > 0) then "queue" '
        'else error("invalid mergeQueue response") end'
    )
    return f"""\
printf '%s\\n' \"$*\" >> \"$LOG_PATH\"
if [ \"$1\" = \"pr\" ] && [ \"$2\" = \"view\" ]; then
  printf '%s\\n' dev
  exit 0
fi
if [ \"$1\" = \"api\" ] && [ \"$2\" = \"graphql\" ]; then
  printf '%s\\n' {shlex.quote(graphql_payload)} | jq -r {shlex.quote(queue_filter)}
  exit $?
fi
if [ \"$1\" = \"api\" ] && [[ \"$*\" == *\"repos/\"* ]]; then
  printf '%s\\n' {json.dumps(squash_allowed)}
  exit 0
fi
if [ \"$1\" = \"pr\" ] && [ \"$2\" = \"merge\" ]; then
  exit 0
fi
echo \"unexpected gh args: $*\" >&2
exit 91
"""


@pytest.mark.parametrize("queue", ['{"id":"queue"}', "null"])
def test_merge_method_follows_actual_queue_presence(tmp_path: Path, queue: str) -> None:
    completed, _, argv = _run_script(
        tmp_path,
        _step_script(_current_workflow(), STRATEGY_STEP),
        _strategy_shim(queue=queue),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "mergeQueue(branch:$branch)" in argv
    assert "-f branch=dev" in argv
    merge_lines = [line for line in argv.splitlines() if line.startswith("pr merge ")]
    assert len(merge_lines) == 1, argv
    if queue == "null":
        assert "--auto --squash" in merge_lines[0], merge_lines[0]
    else:
        assert "--auto" in merge_lines[0], merge_lines[0]
        assert "--squash" not in merge_lines[0], merge_lines[0]


def test_non_default_base_is_used_for_queue_lookup(tmp_path: Path) -> None:
    completed, _, argv = _run_script(
        tmp_path,
        _step_script(_current_workflow(), STRATEGY_STEP),
        _strategy_shim(queue='{"id":"queue"}', base_ref="release"),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "-f branch=release" in argv


def test_merge_queue_lookup_error_fails_closed_without_merge(tmp_path: Path) -> None:
    completed, _, argv = _run_script(
        tmp_path,
        _step_script(_current_workflow(), STRATEGY_STEP),
        _strategy_shim(queue="null", graphql_error=True),
    )
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert not any(line.startswith("pr merge ") for line in argv.splitlines()), argv


@pytest.mark.parametrize(
    "graphql_payload",
    [
        '{"data":{"repository":{}}}',
        '{"data":{"repository":{"mergeQueue":{"id":42}}}}',
    ],
)
def test_partial_or_malformed_merge_queue_response_fails_closed_without_merge(
    tmp_path: Path, graphql_payload: str
) -> None:
    completed, _, argv = _run_script(
        tmp_path,
        _step_script(_current_workflow(), STRATEGY_STEP),
        _strategy_shape_shim(graphql_payload=graphql_payload),
    )
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert not any(line.startswith("pr merge ") for line in argv.splitlines()), argv


def test_no_queue_without_squash_permission_fails_closed_without_merge(
    tmp_path: Path,
) -> None:
    completed, _, argv = _run_script(
        tmp_path,
        _step_script(_current_workflow(), STRATEGY_STEP),
        _strategy_shim(queue="null", squash_allowed="false"),
    )
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert not any(line.startswith("pr merge ") for line in argv.splitlines()), argv
