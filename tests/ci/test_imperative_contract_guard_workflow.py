# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Regression coverage for the reusable imperative contract guard workflow.

OMN-14780: the guard wraps ``uv sync --locked --all-extras`` in a retry loop.
A ``--locked`` lockfile mismatch is a *deterministic* resolution-time failure
(uv resolves cleanly, then reports the lockfile is stale) -- not a transient
network flake. The previous ``until uv sync ...`` loop retried on *any* nonzero
exit, so a stale lockfile burned the whole attempt budget and surfaced as if it
were a transport error ("second failure of the same check is a bug, not a
flake"). These tests pin both the structure of the fixed loop and its runtime
behaviour by executing the workflow's *actual* ``run`` script against a stubbed
``uv`` binary.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "imperative-contract-guard.yml"

# Exact stale-lock message uv emits for `uv sync --locked` when uv.lock is out of
# date versus pyproject.toml (captured from uv 0.8.3, the pinned CI version):
#   Resolved 63 packages in 814ms
#   The lockfile at `uv.lock` needs to be updated, but `--locked` was provided.
#   To update the lockfile, run `uv lock`.
_STALE_LOCK_MESSAGE = (
    "Resolved 63 packages in 814ms\n"
    "The lockfile at `uv.lock` needs to be updated, but `--locked` was "
    "provided. To update the lockfile, run `uv lock`."
)


def _load_yaml(path: Path) -> dict[Any, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _guard_run_script() -> str:
    workflow = _load_yaml(WORKFLOW)
    guard_step = next(
        step
        for step in workflow["jobs"]["imperative-contract-guard"]["steps"]
        if step.get("name") == "Run imperative contract guard"
    )
    run_script = guard_step["run"]
    assert isinstance(run_script, str)
    return run_script


def test_imperative_contract_guard_pins_uv_and_retries_sync() -> None:
    workflow = _load_yaml(WORKFLOW)
    inputs = workflow[True]["workflow_call"]["inputs"]

    assert inputs["uv-version"]["default"] == "0.8.3"

    steps = workflow["jobs"]["imperative-contract-guard"]["steps"]
    setup_uv_step = next(
        step for step in steps if step.get("uses") == "astral-sh/setup-uv@v7"
    )
    assert setup_uv_step["with"]["version"] == "${{ inputs['uv-version'] }}"

    guard_step = next(
        step for step in steps if step.get("name") == "Run imperative contract guard"
    )
    assert guard_step["env"]["UV_HTTP_TIMEOUT"] == "600"
    assert guard_step["env"]["UV_SYNC_ATTEMPTS"] == "3"
    assert guard_step["env"]["UV_SYNC_RETRY_DELAY_SECONDS"] == "10"

    run_script = guard_step["run"]
    assert "git config --global http.version HTTP/1.1" in run_script
    assert "uv sync --locked --all-extras" in run_script
    assert "--scan-freestanding" in run_script
    assert (
        'echo "::warning::uv sync attempt ${attempt}/${UV_SYNC_ATTEMPTS} failed'
        in run_script
    )
    assert 'echo "::error::uv sync failed after ${attempt} attempt(s)' in run_script


def test_guard_fails_fast_on_locked_drift_signature() -> None:
    """The fixed loop must classify the deterministic `--locked` drift and fail
    fast with an actionable `uv lock` message instead of retrying it as a flake."""
    run_script = _guard_run_script()

    # It must NOT use the old blanket `until uv sync ...; do` loop that retried
    # on any nonzero exit.
    assert "until uv sync --locked --all-extras; do" not in run_script

    # It must detect uv's stale-lock signature and short-circuit the retry loop.
    assert "needs to be updated" in run_script
    assert "grep -qE" in run_script
    # Actionable remediation the operator can act on immediately.
    assert "uv lock" in run_script
    assert "OMN-14780" in run_script


# ---------------------------------------------------------------------------
# Behavioural tests: execute the workflow's real `run` script against a stubbed
# `uv` binary, under `bash -e` (GitHub Actions' default `run:` shell), so the
# artifact that actually runs in CI is what we assert on.
# ---------------------------------------------------------------------------

_STUB_UV = """#!/usr/bin/env bash
# Minimal `uv` stub for exercising the guard retry loop.
if [ "$1" = "sync" ]; then
  echo "sync $*" >> "$UV_SYNC_CALL_LOG"
  case "$STUB_MODE" in
    stale)
      printf '%s\\n' "$STUB_STALE_MESSAGE"
      exit 2
      ;;
    transport)
      echo "error: Failed to fetch: request timed out (transport)"
      exit 1
      ;;
    ok)
      echo "Resolved 63 packages, installed 63"
      exit 0
      ;;
    *)
      echo "unknown STUB_MODE: ${STUB_MODE}" >&2
      exit 99
      ;;
  esac
elif [ "$1" = "run" ]; then
  echo "STUB_RAN_CHECK: $*"
  exit 0
fi
exit 0
"""


def _run_guard_script(
    tmp_path: Path, mode: str
) -> tuple[subprocess.CompletedProcess[str], int]:
    if shutil.which("bash") is None:  # pragma: no cover - CI always has bash
        pytest.skip("bash is required to exercise the guard run script")

    run_script = _guard_run_script()
    script_path = tmp_path / "guard_run.sh"
    script_path.write_text(run_script, encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv_stub = bin_dir / "uv"
    uv_stub.write_text(_STUB_UV, encoding="utf-8")
    uv_stub.chmod(0o755)

    call_log = tmp_path / "uv_sync_calls.log"
    call_log.write_text("", encoding="utf-8")

    home = tmp_path / "home"  # isolate `git config --global` writes
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "HOME": str(home),
        "GITHUB_WORKSPACE": str(workspace),
        "UV_SYNC_ATTEMPTS": "3",
        "UV_SYNC_RETRY_DELAY_SECONDS": "0",  # keep the test fast
        "UV_HTTP_TIMEOUT": "600",
        "STUB_MODE": mode,
        "STUB_STALE_MESSAGE": _STALE_LOCK_MESSAGE,
        "UV_SYNC_CALL_LOG": str(call_log),
    }

    # `bash -e` mirrors GitHub Actions' default `run:` shell (`bash -e {0}`).
    proc = subprocess.run(
        ["bash", "-e", str(script_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    sync_calls = [
        line for line in call_log.read_text(encoding="utf-8").splitlines() if line
    ]
    return proc, len(sync_calls)


def test_locked_drift_is_not_retried(tmp_path: Path) -> None:
    """A stale-lock (`--locked`) failure must fail fast on the FIRST attempt and
    emit an actionable `uv lock` message -- never consume the retry budget."""
    proc, sync_calls = _run_guard_script(tmp_path, mode="stale")

    assert proc.returncode != 0
    # Fail fast: exactly one `uv sync` invocation, no retries.
    assert sync_calls == 1, proc.stdout + proc.stderr
    combined = proc.stdout + proc.stderr
    assert "uv.lock is stale" in combined
    assert "uv lock" in combined
    # The transient-retry warning must NOT be emitted for a deterministic failure.
    assert "retrying in" not in combined


def test_transient_failure_is_retried_to_budget(tmp_path: Path) -> None:
    """A genuine transport error must still be retried up to UV_SYNC_ATTEMPTS."""
    proc, sync_calls = _run_guard_script(tmp_path, mode="transport")

    assert proc.returncode != 0
    # Retries exhaust the budget: UV_SYNC_ATTEMPTS invocations.
    assert sync_calls == 3, proc.stdout + proc.stderr
    combined = proc.stdout + proc.stderr
    assert "retrying in" in combined
    assert "uv sync failed after" in combined
    # It must NOT misclassify a transport error as a stale lockfile.
    assert "uv.lock is stale" not in combined


def test_successful_sync_runs_the_guard(tmp_path: Path) -> None:
    """A clean sync proceeds to the imperative-contract check on the first try."""
    proc, sync_calls = _run_guard_script(tmp_path, mode="ok")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert sync_calls == 1
    combined = proc.stdout + proc.stderr
    assert "STUB_RAN_CHECK" in combined
    assert "--scan-freestanding" in combined
