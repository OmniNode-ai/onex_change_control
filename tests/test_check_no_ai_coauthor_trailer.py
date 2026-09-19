# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for the AI co-author trailer gate (OMN-18426).

The gate exists because the commit-msg hook cannot reach a squash merge: the
trailer is re-emitted server-side from the branch's commits. The property that
matters most is not "the gate catches trailers" but "the gate and the hook
agree" — a gate stricter than the hook would fail a pull request whose author
has no way to satisfy it. test_parity_with_the_shipped_hook asserts that by
running the hook's real perl entry.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT
    / "src"
    / "onex_change_control"
    / "scripts"
    / "check_no_ai_coauthor_trailer.py"
)

_spec = importlib.util.spec_from_file_location("check_no_ai_coauthor_trailer", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

# The entry shipped in all fifteen .pre-commit-config.yaml files, byte for byte.
HOOK_ENTRY = r"print unless /^co-authored-by:.*claude.*noreply\@anthropic\.com/i"

STRIPPED = [
    "Co-authored-by: Claude <noreply@anthropic.com>",
    "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>",
    "CO-AUTHORED-BY: CLAUDE <NOREPLY@ANTHROPIC.COM>",
    "co-authored-by: claude sonnet 4.6 <noreply@anthropic.com>",
]
KEPT = [
    "Co-Authored-By: Lakshman Patel <lp141015@gmail.com>",
    "Co-authored-by: jonahgabriel <jonah@omninode.ai>",
    "Co-Authored-By: Claude Dubois <claude@example.com>",
    "Body quoting Co-Authored-By: Claude <noreply@anthropic.com> mid-sentence.",
    "  Co-Authored-By: Claude <noreply@anthropic.com>",
    (
        "Co-authored-by: dependabot[bot] "
        "<49699333+dependabot[bot]@users.noreply.github.com>"
    ),
]


def _run(payload: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.unit
@pytest.mark.parametrize("line", STRIPPED)
def test_flags_every_trailer_the_hook_strips(line: str) -> None:
    assert checker.offending_lines(f"subject\n\n{line}\n") == [line]


@pytest.mark.unit
@pytest.mark.parametrize("line", KEPT)
def test_leaves_every_line_the_hook_keeps(line: str) -> None:
    assert checker.offending_lines(f"subject\n\n{line}\n") == []


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("perl") is None, reason="perl not available")
def test_parity_with_the_shipped_hook(tmp_path: Path) -> None:
    """The gate must flag exactly the lines the hook's own entry removes."""
    message = "subject\n\n" + "\n".join(STRIPPED + KEPT) + "\n"
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(message)

    result = subprocess.run(
        ["perl", "-i", "-ne", HOOK_ENTRY, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    survived = path.read_text().splitlines()
    removed_by_hook = [line for line in message.splitlines() if line not in survived]
    flagged_by_gate = checker.offending_lines(message)

    assert removed_by_hook, "the hook removed nothing — fixture or entry is wrong"
    assert flagged_by_gate == removed_by_hook


@pytest.mark.unit
def test_cli_passes_on_clean_commits() -> None:
    payload = json.dumps(
        [
            {
                "sha": "a" * 40,
                "message": (
                    "fix(OMN-1): subject\n\nCo-authored-by: Human <h@example.com>\n"
                ),
            }
        ]
    )
    result = _run(payload)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[PASS] 1 commit(s) checked" in result.stdout


@pytest.mark.unit
def test_cli_fails_and_names_the_commit() -> None:
    sha = "b" * 40
    payload = json.dumps(
        [
            {"sha": "c" * 40, "message": "clean commit\n"},
            {"sha": sha, "message": f"subject\n\n{STRIPPED[1]}\n"},
        ]
    )
    result = _run(payload)
    assert result.returncode == 1, result.stdout + result.stderr
    assert sha[:12] in result.stdout
    assert "pre-commit install" in result.stdout


@pytest.mark.unit
def test_empty_payload_fails_closed() -> None:
    """An empty fetch is not a clean pull request."""
    result = _run("")
    assert result.returncode == 2
    assert "refusing to report a clean result" in result.stderr


@pytest.mark.unit
def test_malformed_payload_fails_closed() -> None:
    result = _run("{not json")
    assert result.returncode == 2
    assert "not valid JSON" in result.stderr


@pytest.mark.unit
def test_non_array_payload_fails_closed() -> None:
    result = _run('{"sha": "x"}')
    assert result.returncode == 2
    assert "must be a JSON array" in result.stderr


@pytest.mark.unit
def test_message_file_mode(tmp_path: Path) -> None:
    path = tmp_path / "msg.txt"
    path.write_text(f"subject\n\n{STRIPPED[0]}\n")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--message-file", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.unit
def test_accepts_raw_github_commit_objects() -> None:
    """What `gh api .../commits` actually returns: {sha, commit: {message}}."""
    payload = json.dumps(
        [{"sha": "d" * 40, "commit": {"message": f"subject\n\n{STRIPPED[0]}\n"}}]
    )
    result = _run(payload)
    assert result.returncode == 1, result.stdout + result.stderr
    assert ("d" * 12) in result.stdout


@pytest.mark.unit
def test_accepts_the_page_array_slurp_produces() -> None:
    """`--paginate --slurp` yields a list of pages, each a list of commits."""
    page_one = [{"sha": "e" * 40, "commit": {"message": "clean\n"}}]
    page_two = [{"sha": "f" * 40, "commit": {"message": f"subject\n\n{STRIPPED[2]}\n"}}]
    result = _run(json.dumps([page_one, page_two]))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "2 commit(s)" in result.stdout
    assert ("f" * 12) in result.stdout


@pytest.mark.unit
def test_page_array_of_clean_commits_passes() -> None:
    pages = [
        [{"sha": "0" * 40, "commit": {"message": "clean one\n"}}],
        [{"sha": "1" * 40, "commit": {"message": f"clean two\n\n{KEPT[0]}\n"}}],
    ]
    result = _run(json.dumps(pages))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[PASS] 2 commit(s) checked" in result.stdout


@pytest.mark.unit
def test_entry_in_tests_matches_the_shipped_hook() -> None:
    """If this repo's own hook entry drifts, the parity fixture above is stale."""
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text())
    hooks = [
        h
        for r in config["repos"]
        for h in (r.get("hooks") or [])
        if h.get("id") == "strip-ai-coauthor-trailer"
    ]
    assert len(hooks) == 1, hooks
    assert HOOK_ENTRY in hooks[0]["entry"]
