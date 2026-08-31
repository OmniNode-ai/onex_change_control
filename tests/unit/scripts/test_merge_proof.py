# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for scripts/merge-proof (OMN-14776, F-20 deferred half of OMN-14462).

The wrapper resolves the environment onex_change_control local proof gates need
(OMNI_HOME + sibling repo paths) deterministically from its own on-disk
location, and -- when it cannot -- fails fast with the exact ``export`` lines the
operator must run, rather than proceeding on a silent wrong path.

Tests drive the real script via subprocess in an isolated tmp location so
derivation deterministically fails unless the environment is supplied. Git env
vars and PYTHONPATH are stripped from the child environment (worktree-safety:
GIT_* redirect git ops per OMN-14746/14744; ambient PYTHONPATH shadows the
worktree's own source with a stale clone, reference_pythonpath_shadows_worktree_source).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WRAPPER = REPO_ROOT / "scripts" / "merge-proof"

# The sentinel sibling the wrapper probes to prove a candidate dir is omni_home.
SENTINEL_SIBLING = "omnibase_infra"


def _clean_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "OMNI_HOME",
            "SIBLING_REPOS_DIR",
            "GIT_DIR",
            "GIT_INDEX_FILE",
            "GIT_WORK_TREE",
            "PYTHONPATH",
        }
    }


@pytest.fixture
def isolated_wrapper(tmp_path: Path) -> Path:
    """Copy the wrapper where env derivation cannot succeed.

    Not under ``omni_worktrees/`` and with no sibling ``omnibase_infra``.
    """
    scripts = tmp_path / "isolated" / "scripts"
    scripts.mkdir(parents=True)
    dest = scripts / "merge-proof"
    shutil.copy2(WRAPPER, dest)
    dest.chmod(0o755)
    return dest


def _run(
    wrapper: Path, *args: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(wrapper), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_wrapper_exists_and_is_executable() -> None:
    assert WRAPPER.is_file(), f"missing wrapper: {WRAPPER}"
    assert os.access(WRAPPER, os.X_OK), "wrapper must be executable"


def test_no_hardcoded_absolute_paths() -> None:
    """Rule #6: no /Users/ or /Volumes/ absolute paths in the wrapper."""
    text = WRAPPER.read_text()
    assert "/" + "Users/" not in text, "hardcoded /Users/ path in merge-proof"
    assert "/" + "Volumes/" not in text, "hardcoded /Volumes/ path in merge-proof"


def test_unresolved_env_fails_with_export_guidance(isolated_wrapper: Path) -> None:
    """Env UNSET and no derivable sibling -> non-zero exit AND exact export lines.

    Asserting on the *presence* of the guidance (not merely non-zero exit) so a
    silent wrong-path failure cannot pass this test.
    """
    result = _run(isolated_wrapper, "--check", env=_clean_env())
    assert result.returncode != 0, f"expected failure, got 0\nstdout={result.stdout}"
    combined = result.stdout + result.stderr
    assert "export OMNI_HOME=" in combined, combined
    assert "export SIBLING_REPOS_DIR=" in combined, combined


def test_resolved_env_check_passes(isolated_wrapper: Path, tmp_path: Path) -> None:
    """OMNI_HOME set to a checkout holding sibling omnibase_infra -> --check exit 0."""
    fake_home = tmp_path / "omni_home_fake"
    (fake_home / SENTINEL_SIBLING).mkdir(parents=True)
    env = _clean_env()
    env["OMNI_HOME"] = str(fake_home)
    result = _run(isolated_wrapper, "--check", env=env)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "environment OK" in result.stdout


def test_check_fails_when_sibling_missing(
    isolated_wrapper: Path, tmp_path: Path
) -> None:
    """OMNI_HOME set but no sentinel sibling -> fail-fast guidance, non-zero."""
    lonely_home = tmp_path / "lonely"
    lonely_home.mkdir()
    env = _clean_env()
    env["OMNI_HOME"] = str(lonely_home)
    result = _run(isolated_wrapper, "--check", env=env)
    # OMNI_HOME without a sibling omnibase_infra is not accepted by
    # derive_omni_home, so this hits the fail-fast guidance path.
    assert result.returncode != 0
    assert "export OMNI_HOME=" in (result.stdout + result.stderr)


def test_print_env_emits_evalable_exports(
    isolated_wrapper: Path, tmp_path: Path
) -> None:
    fake_home = tmp_path / "omni_home_fake"
    (fake_home / SENTINEL_SIBLING).mkdir(parents=True)
    env = _clean_env()
    env["OMNI_HOME"] = str(fake_home)
    result = _run(isolated_wrapper, "--print-env", env=env)
    assert result.returncode == 0
    assert f"export OMNI_HOME={fake_home}" in result.stdout
    assert f"export SIBLING_REPOS_DIR={fake_home}" in result.stdout


def test_delegated_command_does_not_inherit_leaked_env(
    isolated_wrapper: Path, tmp_path: Path
) -> None:
    """-- CMD must not inherit GIT_* worktree bindings or ambient PYTHONPATH."""
    fake_home = tmp_path / "omni_home_fake"
    (fake_home / SENTINEL_SIBLING).mkdir(parents=True)
    env = _clean_env()
    env.update(
        {
            "OMNI_HOME": str(fake_home),
            "GIT_DIR": "/tmp/wrong.git",  # noqa: S108  test literal, not a real path
            "GIT_INDEX_FILE": "/tmp/wrong.index",  # noqa: S108
            "GIT_WORK_TREE": "/tmp/wrong-worktree",  # noqa: S108
            "PYTHONPATH": "/tmp/wrong-pythonpath",  # noqa: S108
        }
    )
    result = _run(
        isolated_wrapper,
        "--",
        "bash",
        "-c",
        'printf "%s|%s|%s|%s" "${GIT_DIR-unset}" "${GIT_INDEX_FILE-unset}" '
        '"${GIT_WORK_TREE-unset}" "${PYTHONPATH-unset}"',
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "unset|unset|unset|unset"


def test_omni_worktree_layout_autoresolves_without_env(tmp_path: Path) -> None:
    """From the mandated omni_worktrees layout the wrapper derives env.

    This is the ergonomic win: a correct change proves locally without the
    operator reconstructing hidden environment. Built hermetically instead of
    relying on CI to have a sibling omnibase_infra next to the test checkout.
    """
    fake_home = tmp_path / "omni_home"
    (fake_home / SENTINEL_SIBLING).mkdir(parents=True)
    scripts = (
        fake_home / "omni_worktrees" / "OMN-14776" / "onex_change_control" / "scripts"
    )
    scripts.mkdir(parents=True)
    wrapper = scripts / "merge-proof"
    shutil.copy2(WRAPPER, wrapper)
    wrapper.chmod(0o755)

    result = _run(wrapper, "--check", env=_clean_env())
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert f"OMNI_HOME={fake_home}" in result.stdout
    assert "environment OK" in result.stdout
