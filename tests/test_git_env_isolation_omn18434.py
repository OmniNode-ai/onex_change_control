# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Proof for OMN-18434 — a test here cannot inherit GIT_DIR and rewrite this repo.

Git exports ``GIT_DIR`` / ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` /
``GIT_COMMON_DIR`` into the environment of every hook it runs, and those
variables OVERRIDE both ``cwd=`` and ``git -C``. A fixture doing
``subprocess.run(["git", "init"], cwd=tmp_path)`` therefore mutates the REAL
invoking repository when pytest runs under this repo's own hooks. On 2026-09-16
that re-initialized a shared canonical clone in this fleet.

WHY THIS FILE IS NOT VACUOUS
----------------------------
Asserting "``GIT_DIR`` is not in ``os.environ``" is worthless: it passes for
free whenever pytest is launched from an ordinary shell, which is exactly the
condition under which the defect is invisible. So the proof is a DISCRIMINATOR
PAIR over the SAME child program and the SAME decoy repository:

* :func:`test_negative_control_an_unscrubbed_child_corrupts_the_decoy` runs the
  child WITHOUT the scrub and asserts the decoy IS corrupted. If that ever stops
  holding, the harness has stopped reproducing the defect and its sibling has
  become meaningless -- so the suite goes red rather than quietly green.
* :func:`test_the_committed_scrub_defeats_an_inherited_git_dir` runs the
  identical child WITH the committed scrub applied and asserts the decoy is
  untouched while the intended fixture directory is the one written.

The scrub under test is the committed ``_strip_inherited_git_environment`` from
this repo's own ``tests/conftest.py`` -- not a re-typed stand-in that could
drift from it.

Every decoy descends from ``tmp_path``. No canonical clone is touched here: a
test for this hazard must not be an instance of it.

Tickets: OMN-18434 (this generalisation), OMN-14891 (the omnibase_core remedy),
OMN-14744 (the first occurrence), OMN-16584 (the real-gitconfig half).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.conftest import _strip_inherited_git_environment

pytestmark = pytest.mark.unit

# A value that cannot plausibly occur in any real git configuration.
SENTINEL_EMAIL = "hijack@omn18434.invalid"

# The child program: the incident shape verbatim -- `git init` plus a
# `git config` write, in a directory the caller names, with no env= of its own.
CHILD = textwrap.dedent(
    f"""
    import pathlib, subprocess, sys

    target = pathlib.Path(sys.argv[1])
    target.mkdir(parents=True, exist_ok=True)
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", {SENTINEL_EMAIL!r}],
    ):
        subprocess.run(argv, cwd=target, check=False, capture_output=True)
    """
)


def _hermetic() -> dict[str, str]:
    """An environment with every ``GIT_*`` variable removed."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(repo: Path, *args: str) -> str:
    env = _hermetic()
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    env.pop("GIT_COMMON_DIR", None)
    env.pop("GIT_OBJECT_DIRECTORY", None)
    env.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return proc.stdout.strip()


def _make_decoy(tmp_path: Path) -> Path:
    """A throwaway repository standing in for the canonical clone."""
    decoy = tmp_path / "decoy"
    decoy.mkdir(parents=True)
    _git(decoy, "init", "-q")
    return decoy


def _identity(decoy: Path) -> str:
    return _git(decoy, "config", "--local", "--get", "user.email")


def _run_child(target: Path) -> None:
    subprocess.run(
        [sys.executable, "-c", CHILD, str(target)],
        check=False,
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# the discriminator pair
# ---------------------------------------------------------------------------


def test_negative_control_an_unscrubbed_child_corrupts_the_decoy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NEGATIVE CONTROL: the planted GIT_DIR must be load-bearing.

    Without this, the positive test below proves nothing -- it would pass on a
    machine where the variable never mattered. Fix the harness rather than
    deleting this assertion.
    """
    decoy = _make_decoy(tmp_path)
    assert _identity(decoy) == "", "decoy must start with no local identity"

    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))

    target = tmp_path / "fixture-negative"
    _run_child(target)

    assert _identity(decoy) == SENTINEL_EMAIL, (
        "NEGATIVE CONTROL BROKEN: an unscrubbed child no longer corrupts the "
        "decoy, so this file has stopped reproducing the defect"
    )
    assert not (target / ".git").exists(), (
        "NEGATIVE CONTROL BROKEN: the unscrubbed child reached its intended "
        "directory, which means GIT_DIR was not load-bearing here"
    )


def test_the_committed_scrub_defeats_an_inherited_git_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same child, same planted GIT_DIR, with the committed scrub applied."""
    decoy = _make_decoy(tmp_path)
    assert _identity(decoy) == ""

    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))

    _strip_inherited_git_environment()

    target = tmp_path / "fixture-positive"
    _run_child(target)

    assert _identity(decoy) == "", (
        "the decoy repository was written even with the committed scrub "
        "applied -- the remedy does not hold"
    )
    assert (target / ".git").exists(), (
        "the fixture directory was never initialized, so the child's git went "
        "somewhere other than where it was told"
    )


# ---------------------------------------------------------------------------
# the scrub's own contract
# ---------------------------------------------------------------------------


def test_no_git_variable_survives_into_a_test_body() -> None:
    """Observed from inside a real test in this suite.

    The three keys the autouse fixture SETS are the isolation it adds, not
    inheritance it failed to remove, so they are named rather than counted.
    """
    deliberate = {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_EDITOR"}
    leaked = sorted(
        key for key in os.environ if key.startswith("GIT_") and key not in deliberate
    )
    assert leaked == [], f"inherited git variables reached a test body: {leaked}"


def test_git_reads_no_configuration_from_the_real_home_directory() -> None:
    """The OMN-16584 half: a personal global config cannot reach these tests."""
    assert os.environ.get("GIT_CONFIG_GLOBAL") == os.devnull
    assert os.environ.get("GIT_CONFIG_NOSYSTEM") == "1"


def test_the_strip_removes_the_whole_prefix_not_a_named_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named list goes stale the first time git adds a variable.

    ``GIT_OMN18434_PROBE`` is not a real git variable, which is the point: the
    scrub must be defined by the prefix, not by an enumeration somebody has to
    remember to extend.
    """
    monkeypatch.setenv("GIT_OMN18434_PROBE", "present")
    assert os.environ["GIT_OMN18434_PROBE"] == "present"
    _strip_inherited_git_environment()
    assert "GIT_OMN18434_PROBE" not in os.environ


# ---------------------------------------------------------------------------
# the ratchet's wiring
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_PRECOMMIT = _REPO / ".pre-commit-config.yaml"
_WORKFLOW = _REPO / ".github" / "workflows" / "git-env-scrub.yml"


def test_the_gate_is_wired_as_both_a_hook_and_a_ci_job() -> None:
    """A check that runs in only one of the two is half a gate.

    CLAUDE.md Operating Rule 5: a gate that fires only in CI is found at push
    time, after the damaging test has already been written and run locally.
    """
    assert "git-env-scrub" in _PRECOMMIT.read_text(encoding="utf-8")
    assert _WORKFLOW.is_file(), f"{_WORKFLOW} is missing"
    workflow = _WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request" in workflow
    assert "no_unguarded_git_subprocess" in workflow


def test_the_hook_and_the_ci_job_install_the_same_validator() -> None:
    """Two surfaces on two builds can return two verdicts on the same file."""
    pin = "'omnibase-core>=0.47.0,<0.48.0'"
    assert pin in _PRECOMMIT.read_text(encoding="utf-8").replace('"', "'")
    assert pin in _WORKFLOW.read_text(encoding="utf-8").replace('"', "'")


def test_the_gate_carries_no_escape_hatch() -> None:
    """An exemption surface is how this class survived three tickets.

    Comment lines are excluded: the workflow explains in prose why it does not
    use the forgiving-exit-code idiom, and a text scan that cannot tell an
    explanation from an instruction would refuse its own documentation.
    """
    lines = [
        line
        for line in _WORKFLOW.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    body = "\n".join(lines)
    for forbidden in ("continue-on-error", "allowlist", "exempt", "skip"):
        assert forbidden not in body, (
            f"{_WORKFLOW.name} carries {forbidden!r}: a gate with an escape "
            "hatch reports green while enforcing nothing"
        )
