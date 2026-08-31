# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the explicit public-index pin in pyproject.toml (OMN-16518).

docs/plans/2026-08-23-cloud-ci-offload-plan.md Stage 1, S1-3. Before this
change, `pyproject.toml` had no `[tool.uv]` index configuration at all --
the only `[tool.uv]`-adjacent line was a comment (:208) recording an
OMN-13878 *removal*. Scope, stated as narrowly as the plan itself states it:
per uv's documented precedence, an env-supplied `UV_INDEX`/`UV_DEFAULT_INDEX`
always outranks this project-level pin -- this test asserts the pin exists
and names the public index, not that it is sufficient on its own (the
generator-env sanitization in OMN-16517 and the structural allowlist gate in
OMN-16516 are the mechanisms that actually close the leak channel).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def _load_pyproject() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT_PATH.read_text())


@pytest.mark.unit
class TestUvIndexPin:
    def test_tool_uv_index_is_declared(self) -> None:
        data = _load_pyproject()
        tool = data.get("tool", {})
        assert "uv" in tool, "no [tool.uv] table in pyproject.toml"
        assert "index" in tool["uv"], (
            "no [[tool.uv.index]] array in pyproject.toml -- the repo carries "
            "no explicit public-index pin (OMN-16518)"
        )

    def test_default_index_is_the_public_pypi_index(self) -> None:
        data = _load_pyproject()
        indexes = data["tool"]["uv"]["index"]
        assert isinstance(indexes, list), indexes
        assert indexes, "no [tool.uv.index] entries"

        default_indexes = [idx for idx in indexes if idx.get("default") is True]
        assert len(default_indexes) == 1, (
            f"expected exactly one default=true index, found {len(default_indexes)}: "
            f"{indexes}"
        )
        default_index = default_indexes[0]
        assert default_index["url"] == "https://pypi.org/simple", (
            f"the default index must be the public PyPI simple index, got "
            f"{default_index['url']!r} -- a private/tailnet mirror here would "
            "reproduce the exact OMN-16162 leak shape"
        )

    def test_no_private_or_tailnet_host_anywhere_in_tool_uv(self) -> None:
        """Belt-and-suspenders: no index entry (default or additional) may
        name a non-public host. An *additional* index is still a resolution
        source uv can pick packages from."""
        data = _load_pyproject()
        indexes = data.get("tool", {}).get("uv", {}).get("index", [])
        for idx in indexes:
            url = idx.get("url", "")
            host = urlparse(url).hostname
            assert host == "pypi.org", (
                f"[tool.uv.index] entries must use the public PyPI host, got "
                f"{host!r}: {idx}"
            )
            assert "tail" not in url, f"tailnet-shaped host in [tool.uv.index]: {idx}"
            assert ".ts.net" not in url, (
                f"tailnet-shaped host in [tool.uv.index]: {idx}"
            )

    def test_uv_lock_dry_run_resolves_with_no_changes(self) -> None:
        """The pin must not perturb resolution -- uv lock --dry-run against
        the committed lockfile must report zero changes."""
        import subprocess

        result = subprocess.run(
            ["uv", "lock", "--dry-run"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, (
            f"uv lock --dry-run failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        # uv writes its resolution summary to stderr, not stdout.
        assert "No lockfile changes detected" in result.stderr, result.stderr
