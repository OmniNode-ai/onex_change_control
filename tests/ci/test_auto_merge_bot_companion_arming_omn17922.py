# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression coverage for bot-authored OCC companion arming (OMN-17922).

Root cause (measured live 2026-09-04T17:05-17:55Z): every arming step in
``auto-merge.yml`` was gated on ``steps.resolve.outputs.actor ==
'jonahgabriel'``. OCC evidence companions are authored by the
``onexbot-occ-writer`` GitHub App, so a CLEAN companion with zero red checks
sat open with ``autoMergeRequest: null`` until a human armed it by hand
(occ#8207, occ#8208, occ#8203 on that date), and the product PR behind each one
stayed BLOCKED on its ``OCC Companion Merged Gate (OMN-15214)`` check.

Fix shape pinned here:

1. The ``Resolve PR and author`` step computes an ``arm`` output. It is
   ``true`` for ``jonahgabriel`` (unchanged) and for the OCC writer App — in
   BOTH login spellings GitHub uses (``app/onexbot-occ-writer`` from ``gh pr
   view``, ``onexbot-occ-writer[bot]`` from the event payload) — only when the
   title starts with ``evidence(``. Everything else is ``false``. The tests run
   the step's own bash, so the pinned behaviour is the deployed behaviour, not
   a parallel re-implementation.
2. Every gated step keys on ``steps.resolve.outputs.arm == 'true'``; no ``if:``
   still hard-codes the human login.
3. The two merge-state-mutating steps arm with ``secrets.CROSS_REPO_PAT``
   (OMN-17875): a GITHUB_TOKEN- or App-token-authored merge fires no push /
   pull_request:closed event on ``dev``, which would starve
   validate-validator-requirements.yml, validator-g2-ip-family.yml and
   todo-audit-on-merge.yml on every companion merge.
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
GATED_STEP_NAMES: tuple[str, ...] = (
    "Check OCC eligibility preflight status",
    "Check governance-path exclusion",
    "Enable auto-merge",
    "Enqueue armed PR and verify it entered the queue",
)
MUTATING_STEP_NAMES: tuple[str, ...] = (
    "Enable auto-merge",
    "Enqueue armed PR and verify it entered the queue",
)
ARM_GATE_EXPR = "steps.resolve.outputs.arm == 'true'"
LEGACY_ACTOR_GATE_EXPR = "steps.resolve.outputs.actor == 'jonahgabriel'"
REQUIRED_TOKEN_EXPR = "${{ secrets.CROSS_REPO_PAT }}"  # noqa: S105 -- a GitHub expression, not a value
APP_TOKEN_MARKERS: tuple[str, ...] = (
    "create-github-app-token",
    "ONEXBOT_OCC_APP_ID",
    "ONEXBOT_OCC_PRIVATE_KEY",
)


def _load_workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(AUTO_MERGE_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _steps() -> list[dict[str, Any]]:
    job = _load_workflow()["jobs"]["auto-merge"]
    steps = job["steps"]
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def _step_by_name(name: str) -> dict[str, Any]:
    matches = [step for step in _steps() if step.get("name") == name]
    assert len(matches) == 1, (
        f"expected exactly one step named {name!r}, got {len(matches)}"
    )
    return matches[0]


def _run_resolve_step(
    tmp_path: Path,
    env_overrides: dict[str, str],
    gh_shim: str | None = None,
) -> tuple[dict[str, str], subprocess.CompletedProcess[str]]:
    """Execute the resolve step's ``run:`` script and return its GITHUB_OUTPUT.

    ``gh_shim`` is an optional bash body for a fake ``gh`` placed first on
    PATH, for the event paths that resolve author/title via ``gh pr view``.
    """
    script = _step_by_name(RESOLVE_STEP_NAME)["run"]
    assert isinstance(script, str)
    output_file = tmp_path / "github_output"
    output_file.write_text("", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if gh_shim is not None:
        gh_path = bin_dir / "gh"
        gh_path.write_text("#!/usr/bin/env bash\n" + gh_shim, encoding="utf-8")
        gh_path.chmod(gh_path.stat().st_mode | stat.S_IXUSR)

    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output_file),
        "GH_TOKEN": "unused",
        "GH_REPO": "OmniNode-ai/onex_change_control",
        "EVENT_NAME": "",
        "PR_FROM_PAYLOAD": "",
        "PR_FROM_DISPATCH": "",
        "CHECK_SUITE_PRS": "",
        "PR_AUTHOR_FROM_PAYLOAD": "",
        "PR_TITLE_FROM_PAYLOAD": "",
    }
    env.update(env_overrides)
    proc = subprocess.run(
        ["bash", "-c", script],
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
    return outputs, proc


# ---------------------------------------------------------------------------
# 1. The arming decision, executed from the workflow's own bash.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actor", "title", "expected_arm"),
    [
        # The human/agent identity arms regardless of title (unchanged).
        ("jonahgabriel", "fix(OMN-17922): anything", "true"),
        ("jonahgabriel", "evidence(OMN-17922): anything", "true"),
        # The OCC writer App, payload spelling, evidence title -> arms.
        (
            "onexbot-occ-writer[bot]",
            "evidence(OMN-17876): OCC companion for OmniNode-ai/omnibase_infra#3184",
            "true",
        ),
        # The App with a non-evidence title does NOT arm.
        ("onexbot-occ-writer[bot]", "fix(occ): supersede merged #8075", "false"),
        ("onexbot-occ-writer[bot]", " evidence(OMN-1): leading space", "false"),
        ("onexbot-occ-writer[bot]", "Evidence(OMN-1): wrong case", "false"),
        # Other bots never arm, even with an evidence-shaped title.
        ("dependabot[bot]", "evidence(OMN-1): forged shape", "false"),
        ("github-actions[bot]", "evidence(OMN-1): forged shape", "false"),
        ("app/github-actions", "evidence(OMN-1): forged shape", "false"),
        # Other humans never arm.
        ("someone-else", "evidence(OMN-1): forged shape", "false"),
        ("someone-else", "fix(OMN-1): ordinary", "false"),
        ("", "evidence(OMN-1): empty actor", "false"),
    ],
)
def test_pull_request_event_arming_decision(
    tmp_path: Path, actor: str, title: str, expected_arm: str
) -> None:
    outputs, _ = _run_resolve_step(
        tmp_path,
        {
            "EVENT_NAME": "pull_request",
            "PR_FROM_PAYLOAD": "8207",
            "PR_AUTHOR_FROM_PAYLOAD": actor,
            "PR_TITLE_FROM_PAYLOAD": title,
        },
    )
    assert outputs["skip"] == "false"
    assert outputs["pr"] == "8207"
    assert outputs["actor"] == actor
    assert outputs["arm"] == expected_arm, outputs


_GH_SHIM_OCC_WRITER = """
# Fake `gh pr view <n> --json <field> --jq <expr>` for the check_suite /
# workflow_dispatch resolve path. Author is rendered the way the real CLI
# renders a GitHub App author: `app/<slug>`.
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  case "$*" in
    *"--json author"*) echo "app/onexbot-occ-writer" ;;
    *"--json title"*) echo "${SHIM_TITLE}" ;;
    *) echo "unexpected gh args: $*" >&2; exit 1 ;;
  esac
  exit 0
fi
echo "unexpected gh args: $*" >&2
exit 1
"""


@pytest.mark.parametrize(
    ("title", "expected_arm"),
    [
        (
            "evidence(OMN-17861): OCC companion for OmniNode-ai/omnibase_infra#3185",
            "true",
        ),
        ("evidence(OMN-14888): OCC observation append (x.yaml)", "true"),
        ("chore(occ): not a companion", "false"),
    ],
)
def test_check_suite_event_resolves_app_author_via_gh_and_decides(
    tmp_path: Path, title: str, expected_arm: str
) -> None:
    """The check_suite path resolves the App as ``app/onexbot-occ-writer``.

    This is the path a companion actually arms through: the workflow re-fires
    on ``check_suite: completed`` once the companion's CI finishes.
    """
    outputs, _ = _run_resolve_step(
        tmp_path,
        {
            "EVENT_NAME": "check_suite",
            "CHECK_SUITE_PRS": '[{"number": 8208}]',
            "SHIM_TITLE": title,
        },
        gh_shim=_GH_SHIM_OCC_WRITER,
    )
    assert outputs["skip"] == "false"
    assert outputs["pr"] == "8208"
    assert outputs["actor"] == "app/onexbot-occ-writer"
    assert outputs["arm"] == expected_arm, outputs


def test_workflow_dispatch_event_resolves_app_author_via_gh(tmp_path: Path) -> None:
    outputs, _ = _run_resolve_step(
        tmp_path,
        {
            "EVENT_NAME": "workflow_dispatch",
            "PR_FROM_DISPATCH": "8203",
            "SHIM_TITLE": "evidence(OMN-14888): OCC observation append",
        },
        gh_shim=_GH_SHIM_OCC_WRITER,
    )
    assert outputs["pr"] == "8203"
    assert outputs["actor"] == "app/onexbot-occ-writer"
    assert outputs["arm"] == "true"


def test_check_suite_without_prs_still_skips(tmp_path: Path) -> None:
    """The pre-existing no-PR short-circuit is untouched by the widening."""
    outputs, _ = _run_resolve_step(
        tmp_path,
        {"EVENT_NAME": "check_suite", "CHECK_SUITE_PRS": "[]"},
        gh_shim=_GH_SHIM_OCC_WRITER,
    )
    assert outputs == {"skip": "true"}


# ---------------------------------------------------------------------------
# 2. Every gated step keys on the resolve step's decision, not on the login.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("step_name", GATED_STEP_NAMES)
def test_gated_steps_key_on_arm_output(step_name: str) -> None:
    condition = _step_by_name(step_name).get("if", "")
    assert isinstance(condition, str)
    assert ARM_GATE_EXPR in condition, (
        f"{step_name!r} must gate on {ARM_GATE_EXPR!r}; found {condition!r}"
    )
    assert LEGACY_ACTOR_GATE_EXPR not in condition, (
        f"{step_name!r} still hard-codes the human login in its if:; "
        "bot-authored evidence companions would never arm (OMN-17922)"
    )


def test_no_step_condition_hard_codes_the_human_login() -> None:
    for step in _steps():
        condition = step.get("if", "")
        assert LEGACY_ACTOR_GATE_EXPR not in str(condition), step.get("name")


@pytest.mark.parametrize("step_name", MUTATING_STEP_NAMES)
def test_mutating_steps_keep_the_existing_safety_gates(step_name: str) -> None:
    """Widening WHO may arm must not loosen WHAT may arm."""
    condition = _step_by_name(step_name).get("if", "")
    assert "steps.occ_gate.outputs.defer != 'true'" in condition
    assert "steps.governance_gate.outputs.exclude != 'true'" in condition
    assert "steps.resolve.outputs.skip != 'true'" in condition


# ---------------------------------------------------------------------------
# 3. Arming credential (OMN-17875 port).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("step_name", MUTATING_STEP_NAMES)
def test_merge_state_mutating_steps_arm_with_cross_repo_pat(step_name: str) -> None:
    token = _step_by_name(step_name).get("env", {}).get("GH_TOKEN")
    assert token == REQUIRED_TOKEN_EXPR, (
        f"{step_name!r} must arm with {REQUIRED_TOKEN_EXPR}; found {token!r}. "
        "GITHUB_TOKEN- and App-token-authored merges suppress push-triggered "
        "runs on dev (OMN-17875 / OMN-16373)."
    )
    assert "GITHUB_TOKEN" not in str(token)


def test_read_only_resolve_step_stays_on_default_token() -> None:
    step = _step_by_name(RESOLVE_STEP_NAME)
    assert step.get("env", {}).get("GH_TOKEN") == "${{ secrets.GITHUB_TOKEN }}"
    run = step.get("run", "")
    for mutating_verb in (
        "gh pr merge",
        "gh pr update-branch",
        "enqueuePullRequest",
        "git push",
    ):
        assert mutating_verb not in run, (
            f"{RESOLVE_STEP_NAME!r} performs {mutating_verb!r} under GITHUB_TOKEN"
        )


def test_no_app_token_mint_is_introduced() -> None:
    body = AUTO_MERGE_WORKFLOW.read_text(encoding="utf-8").split("name: Auto-Merge", 1)[
        1
    ]
    for marker in APP_TOKEN_MARKERS:
        assert marker not in body, marker


def test_header_documents_the_defect_and_cites_the_tickets() -> None:
    header = AUTO_MERGE_WORKFLOW.read_text(encoding="utf-8").split(
        "name: Auto-Merge", 1
    )[0]
    for citation in (
        "OMN-17922",
        "OMN-17875",
        "OMN-16373",
        "CROSS_REPO_PAT",
        "onexbot-occ-writer",
        "evidence(",
    ):
        assert citation in header, f"auto-merge.yml header must cite {citation}"
