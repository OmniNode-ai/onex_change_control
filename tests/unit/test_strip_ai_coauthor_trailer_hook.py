# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Behaviour proof for the strip-ai-coauthor-trailer commit-msg hook (OMN-18426).

The hook is run exactly as pre-commit runs it: the configured ``entry`` with the
commit-message file path appended (``pass_filenames: true``).
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
HOOK_ID = "strip-ai-coauthor-trailer"

CLAUDE_TRAILER = "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
HUMAN_TRAILER = "Co-Authored-By: Jane Human <jane@example.com>"


def _config() -> dict[str, Any]:
    loaded = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    assert isinstance(loaded, dict)
    return loaded


def _hook() -> dict[str, Any]:
    matches: list[dict[str, Any]] = [
        hook
        for repository in _config()["repos"]
        for hook in repository["hooks"]
        if hook["id"] == HOOK_ID
    ]
    assert len(matches) == 1, (
        f"expected exactly one {HOOK_ID} hook, found {len(matches)}"
    )
    return matches[0]


def _run_hook(message: str, tmp_path: Path) -> str:
    """Run the configured entry on a commit-message file and return the result."""
    message_file = tmp_path / "COMMIT_EDITMSG"
    message_file.write_text(message)
    command = [*shlex.split(str(_hook()["entry"])), str(message_file)]
    completed = subprocess.run(
        command, capture_output=True, check=False, text=True, cwd=tmp_path
    )
    assert completed.returncode == 0, completed.stderr
    return message_file.read_text()


@pytest.fixture(autouse=True)
def _require_perl() -> None:
    # The entry is a perl one-liner; a missing interpreter must fail, not skip.
    assert shutil.which("perl") is not None, "perl is required by the hook entry"


@pytest.mark.unit
def test_hook_is_wired_on_the_commit_msg_stage_and_installed_by_default() -> None:
    hook = _hook()
    assert hook["stages"] == ["commit-msg"]
    assert hook["pass_filenames"] is True
    install_types: list[str] = _config().get("default_install_hook_types") or []
    assert "commit-msg" in install_types


@pytest.mark.unit
def test_claude_trailer_is_removed_and_human_trailer_is_kept(tmp_path: Path) -> None:
    message = f"subject line\n\nbody text\n\n{CLAUDE_TRAILER}\n{HUMAN_TRAILER}\n"
    result = _run_hook(message, tmp_path)
    assert CLAUDE_TRAILER not in result
    assert result == f"subject line\n\nbody text\n\n{HUMAN_TRAILER}\n"


@pytest.mark.unit
def test_match_is_case_insensitive(tmp_path: Path) -> None:
    lowered = "co-authored-by: claude <NOREPLY@ANTHROPIC.COM>"
    result = _run_hook(f"subject\n\n{lowered}\n", tmp_path)
    assert result == "subject\n\n"


@pytest.mark.unit
@pytest.mark.parametrize(
    "line",
    [
        # A person named Claude with a non-Anthropic address is not an AI trailer.
        "Co-Authored-By: Claude Monet <claude@example.com>",
        # The trailer text quoted mid-line is prose, not a trailer.
        f"See the old footer: {CLAUDE_TRAILER}",
    ],
)
def test_non_trailer_lines_are_untouched(line: str, tmp_path: Path) -> None:
    message = f"subject\n\n{line}\n"
    assert _run_hook(message, tmp_path) == message
