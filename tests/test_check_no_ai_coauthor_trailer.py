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
import os
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

# Derive the fixture entry from the same maintained vocabulary the branch gate
# reads.  The hook entry is still checked against this repo's shipped config
# below, so a list change cannot silently leave the parity fixture stale.
MACHINE_IDENTITIES = checker.MACHINE_IDENTITIES
_PERL_IDENTITIES = "|".join(
    identity.replace("@", r"\@").replace(".", r"\.") for identity in MACHINE_IDENTITIES
)
HOOK_ENTRY = rf"print unless /^co-authored-by:.*<(?:{_PERL_IDENTITIES})>/i"

STRIPPED = [
    "Co-authored-by: Claude <noreply@anthropic.com>",
    "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>",
    "CO-AUTHORED-BY: CLAUDE <NOREPLY@ANTHROPIC.COM>",
    "co-authored-by: claude sonnet 4.6 <noreply@anthropic.com>",
    "Co-authored-by: Claude (AI Assistant) <claude@omninode.ai>",
    "Co-authored-by: Cursor <cursoragent@cursor.com>",
    "Co-authored-by: Codex Merge Controller <codex@omninode.ai>",
]
KEPT = [
    "Co-Authored-By: Lakshman Patel <lp141015@gmail.com>",
    "Co-authored-by: jonahgabriel <jonah@omninode.ai>",
    # A person named Claude at an address outside the maintained machine list
    # remains a human co-author.
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


# ---------------------------------------------------------------------------
# Whitespace and case variants. Every one of these was measured against the
# rule before being asserted here; all six already worked and none was covered,
# so nothing stopped them regressing.
# ---------------------------------------------------------------------------

_ANTHROPIC = "<noreply@anthropic.com>"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label", "line"),
    [
        ("crlf", f"Co-authored-by: Claude {_ANTHROPIC}\r"),
        ("trailing space", f"Co-authored-by: Claude {_ANTHROPIC}   "),
        ("multi space after colon", f"Co-authored-by:   Claude {_ANTHROPIC}"),
        ("tab after colon", f"Co-authored-by:\tClaude {_ANTHROPIC}"),
        ("no space after colon", f"Co-authored-by:Claude {_ANTHROPIC}"),
        ("uppercase domain", "Co-authored-by: Claude <NOREPLY@ANTHROPIC.COM>"),
    ],
)
def test_whitespace_and_case_variants_are_still_caught(label: str, line: str) -> None:
    # Compared by count and prefix rather than by identity: the checker splits on
    # splitlines(), which drops a trailing \r, so the CRLF case is matched but the
    # returned string is not byte-identical to the input.
    hits = checker.offending_lines(f"subject\n\n{line}\n")
    assert len(hits) == 1, f"{label}: expected exactly one hit, got {hits!r}"
    assert hits[0].lower().startswith("co-authored-by:"), f"{label}: {hits[0]!r}"


def _hermetic_git_env() -> dict[str, str]:
    """An environment with every ``GIT_*`` variable removed.

    Git exports GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE / GIT_COMMON_DIR into
    every hook environment and those OVERRIDE both ``cwd=`` and ``git -C``
    (OMN-14891), so a test shelling out to git under a pre-push hook retargets
    the real invoking worktree. This call only parses stdin and reads no
    repository, but it is scrubbed anyway: the guard is about the shape of the
    call, not about whether this one happens to be harmless. Same pattern as
    tests/test_git_env_isolation_omn18434.py.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    # Named explicitly as well as stripped by prefix: the OMN-14891 guard reads
    # this statically and requires each location variable to be visibly removed.
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    env.pop("GIT_COMMON_DIR", None)
    env.pop("GIT_OBJECT_DIRECTORY", None)
    env.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
    return env


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_indented_trailer_is_not_a_trailer_git_folds_it() -> None:
    """An indented line is kept, and that is correct rather than a miss.

    The rule anchors at line start, so `  Co-authored-by: ...` survives. That is
    not a hole: git folds an indented line into the PRECEDING trailer's value
    rather than reading it as its own trailer, so there is nothing for the rule
    to remove. Asserted with git's own parser rather than by reasoning about it.
    """
    folded = (
        "subject\n\nCo-Authored-By: Human <h@example.com>\n"
        f"  Co-authored-by: Claude {_ANTHROPIC}\n"
    )
    flat = (
        "subject\n\nCo-Authored-By: Human <h@example.com>\n"
        f"Co-authored-by: Claude {_ANTHROPIC}\n"
    )

    def trailers(text: str) -> list[str]:
        out = subprocess.run(
            ["git", "interpret-trailers", "--parse"],
            input=text,
            capture_output=True,
            text=True,
            check=False,
            env=_hermetic_git_env(),
        )
        return [ln for ln in out.stdout.splitlines() if ln.strip()]

    assert len(trailers(folded)) == 1, (
        "git should fold the indented line into the previous trailer"
    )
    assert len(trailers(flat)) == 2, "control: unindented, git sees two trailers"
    assert checker.offending_lines(folded) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "identity",
    [
        "Claude (AI Assistant) <claude@omninode.ai>",
        "Cursor <cursoragent@cursor.com>",
        "codex <codex@omninode.ai>",
    ],
)
def test_maintained_machine_identities_are_caught(identity: str) -> None:
    """The re-keyed rule catches every maintained machine identity."""
    line = f"Co-authored-by: {identity}"
    assert checker.offending_lines(f"subject\n\n{line}\n") == [line]


@pytest.mark.unit
def test_machine_email_is_caught_independent_of_display_name() -> None:
    line = "Co-authored-by: Clàudé <noreply@anthropic.com>"
    assert checker.offending_lines(f"subject\n\n{line}\n") == [line]


def test_unlisted_anthropic_domain_is_kept() -> None:
    line = "Co-authored-by: Claude <noreply@mail.anthropic.com>"
    assert checker.offending_lines(f"subject\n\n{line}\n") == []
