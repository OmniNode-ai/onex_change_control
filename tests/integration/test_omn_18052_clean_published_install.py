# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Live clean-install acceptance proof for OMN-18052.

The normal suite collects this test but skips the package-index operation. The
OCC contract opts in explicitly, so the Done verifier executes the published
package behavior without making every unrelated pull request depend on PyPI.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

_LIVE_PYPI_ENV = "OMN_18052_LIVE_PYPI"
_VERSION_RE = re.compile(r"^onex version (?P<version>\d+\.\d+\.\d+)$")


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=55,
    )
    assert result.returncode == 0, (
        f"command failed ({result.returncode}): {command!r}\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )
    return result.stdout


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(
    os.environ.get(_LIVE_PYPI_ENV) != "1",
    reason=f"live PyPI proof requires {_LIVE_PYPI_ENV}=1",
)
def test_clean_machine_installs_published_package_and_runs_onex(
    tmp_path: Path,
) -> None:
    """AC1: a fresh tool environment installs and executes published ONEX."""
    assert not any(
        (parent / ".git").exists() for parent in (tmp_path, *tmp_path.parents)
    )

    tool_dir = tmp_path / "tools"
    bin_dir = tmp_path / "bin"
    assert not tool_dir.exists()
    assert not bin_dir.exists()

    uv = shutil.which("uv")
    assert uv is not None, "uv is required for the documented install command"
    clean_env = os.environ.copy()
    for name in ("CONDA_PREFIX", "OMNI_HOME", "PYTHONPATH", "VIRTUAL_ENV"):
        clean_env.pop(name, None)
    clean_env.update(
        {
            "NO_COLOR": "1",
            "UV_NO_CONFIG": "1",
            "UV_TOOL_BIN_DIR": str(bin_dir),
            "UV_TOOL_DIR": str(tool_dir),
        }
    )

    _run(
        [
            uv,
            "tool",
            "install",
            "--python",
            "3.13",
            "--upgrade",
            "--with",
            "omnimarket",
            "omnibase-core",
        ],
        cwd=tmp_path,
        env=clean_env,
    )

    onex = bin_dir / "onex"
    assert onex.is_file(), "the published install did not expose the onex CLI"
    reported = _run([str(onex), "--version"], cwd=tmp_path, env=clean_env).strip()
    match = _VERSION_RE.fullmatch(reported)
    assert match is not None, f"unexpected onex version output: {reported!r}"

    installed_version = match.group("version")
    tool_list = _run([uv, "tool", "list"], cwd=tmp_path, env=clean_env)
    assert f"omnibase-core v{installed_version}\n" in tool_list

    help_text = _run([str(onex), "cloud", "--help"], cwd=tmp_path, env=clean_env)
    assert "Delegate to the OmniNode platform" in help_text
