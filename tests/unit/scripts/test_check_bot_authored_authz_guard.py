# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_bot_authored_authz_guard (OMN-14919).

The guard is the mechanical closure for the canonical machine OCC producer: a
machine writer authenticating with a repo-scoped App token could otherwise push
to grants/** or allowlists/** and mint its own authorization. These tests isolate
the pure decision logic (`evaluate`) and identity predicates from git I/O, then
separately prove the git-derivation layer end-to-end against a real throwaway repo
(a seeded bot-authored PR touching grants/** must REJECT) and prove it fails closed
when git history is unreadable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from onex_change_control.scripts.check_bot_authored_authz_guard import (
    _EXIT_INCONCLUSIVE,
    _EXIT_PASS,
    _EXIT_REJECT,
    _GitFactError,
    evaluate,
    is_bot_author_type,
    is_bot_identity,
    is_bot_login,
    is_sensitive_path,
    main,
    resolve_changed_files,
    resolve_commit_identities,
    resolve_staged_changed_files,
)

pytestmark = pytest.mark.unit

_HUMAN = [("Jonah Gray", "jonah.neugass@gmail.com")]
_BOT_COMMIT = [("omnimarket-bot", "bot@omninode.ai")]


# ---------------------------------------------------------------------------
# Identity predicates
# ---------------------------------------------------------------------------


class TestIdentityPredicates:
    @pytest.mark.parametrize(
        "login",
        [
            "omnimarket-bot",
            "onexbot",
            "OmniMarket-Bot",
            "onexbot[bot]",
            "some-app[bot]",
        ],
    )
    def test_bot_logins(self, login: str) -> None:
        assert is_bot_login(login) is True

    @pytest.mark.parametrize("login", [None, "", "jonah", "danielsomething", "  "])
    def test_human_logins(self, login: str | None) -> None:
        assert is_bot_login(login) is False

    def test_bot_author_type(self) -> None:
        assert is_bot_author_type("Bot") is True
        assert is_bot_author_type("bot") is True
        assert is_bot_author_type("User") is False
        assert is_bot_author_type(None) is False

    @pytest.mark.parametrize(
        ("name", "email"),
        [
            ("omnimarket-bot", "bot@omninode.ai"),
            ("node-occ-companion-effect", "occ-companion-effect@omninode.ai"),
            ("some-app[bot]", "x@y.z"),
            ("whoever", "12345+some-app[bot]@users.noreply.github.com"),
        ],
    )
    def test_bot_identities(self, name: str, email: str) -> None:
        assert is_bot_identity(name, email) is True

    @pytest.mark.parametrize(
        ("name", "email"),
        [
            ("Jonah Gray", "jonah.neugass@gmail.com"),
            ("Daniyal", "daniyal@example.com"),
        ],
    )
    def test_human_identities(self, name: str, email: str) -> None:
        assert is_bot_identity(name, email) is False

    @pytest.mark.parametrize(
        "path",
        [
            "grants/prod_promotion_grants.yaml",
            "allowlists/omnimarket.yaml",
            "./grants/x.yaml",
            "allowlists/nested/deep.yaml",
        ],
    )
    def test_sensitive_paths(self, path: str) -> None:
        assert is_sensitive_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "contracts/OMN-123.yaml",
            "drift/dod_receipts/OMN-1/x/command.yaml",
            "src/onex_change_control/scripts/x.py",
            "grants_notes.md",  # not under grants/
            "my_allowlists/x.yaml",  # not under allowlists/
        ],
    )
    def test_non_sensitive_paths(self, path: str) -> None:
        assert is_sensitive_path(path) is False


# ---------------------------------------------------------------------------
# Pure decision logic — the four acceptance cases + fail-closed
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_acceptance_1_bot_commit_touches_grants_rejects(self) -> None:
        """A1: bot commit identity + grants/** touched -> REJECT."""
        code, msg = evaluate(
            changed_files=["grants/prod_promotion_grants.yaml"],
            pr_author_login=None,
            pr_author_type=None,
            commit_identities=_BOT_COMMIT,
        )
        assert code == _EXIT_REJECT
        assert "REJECT" in msg
        assert "omnimarket-bot" in msg

    def test_acceptance_1b_bot_author_type_touches_allowlists_rejects(self) -> None:
        """A1: PR author type Bot + allowlists/** -> REJECT (no bot commits)."""
        code, msg = evaluate(
            changed_files=["allowlists/omnimarket.yaml"],
            pr_author_login="some-app[bot]",
            pr_author_type="Bot",
            commit_identities=_HUMAN,  # commits look human, but PR author is a bot
        )
        assert code == _EXIT_REJECT
        assert "Bot" in msg

    def test_acceptance_2_human_touches_grants_passes(self) -> None:
        """A2: human author + grants/** touched -> PASS."""
        code, msg = evaluate(
            changed_files=["grants/prod_promotion_grants.yaml"],
            pr_author_login="jonah",
            pr_author_type="User",
            commit_identities=_HUMAN,
        )
        assert code == _EXIT_PASS
        assert "PASS" in msg

    def test_acceptance_3_bot_touches_only_evidence_passes(self) -> None:
        """A3: bot touches only contracts/** + drift/** (evidence) -> PASS."""
        code, msg = evaluate(
            changed_files=[
                "contracts/OMN-123.yaml",
                "drift/dod_receipts/OMN-123/dod-x-pr-1/command.yaml",
            ],
            pr_author_login="omnimarket-bot",
            pr_author_type="Bot",
            commit_identities=_BOT_COMMIT,
        )
        assert code == _EXIT_PASS
        assert "does not apply" in msg

    def test_acceptance_4_indeterminate_on_sensitive_is_inconclusive(self) -> None:
        """A4: sensitive path, unreadable commit identities -> INCONCLUSIVE."""
        code, msg = evaluate(
            changed_files=["grants/prod_promotion_grants.yaml"],
            pr_author_login=None,
            pr_author_type=None,
            commit_identities=None,
        )
        assert code == _EXIT_INCONCLUSIVE
        assert "INCONCLUSIVE" in msg

    def test_unreadable_changed_files_is_inconclusive(self) -> None:
        code, msg = evaluate(
            changed_files=None,
            pr_author_login="jonah",
            pr_author_type="User",
            commit_identities=_HUMAN,
        )
        assert code == _EXIT_INCONCLUSIVE
        assert "INCONCLUSIVE" in msg

    def test_bot_pr_author_but_unreadable_commits_still_rejects(self) -> None:
        """Author-side bot signal alone is enough; do not soften to INCONCLUSIVE."""
        code, _ = evaluate(
            changed_files=["grants/x.yaml"],
            pr_author_login="onexbot[bot]",
            pr_author_type="Bot",
            commit_identities=None,
        )
        assert code == _EXIT_REJECT

    def test_mixed_change_touching_grants_and_evidence_is_evaluated(self) -> None:
        """A PR touching BOTH evidence and grants is still guarded on grants."""
        code, _ = evaluate(
            changed_files=["contracts/OMN-1.yaml", "grants/prod_promotion_grants.yaml"],
            pr_author_login=None,
            pr_author_type=None,
            commit_identities=_BOT_COMMIT,
        )
        assert code == _EXIT_REJECT


# ---------------------------------------------------------------------------
# Git-derivation layer — real throwaway repo (end-to-end) + fail-closed
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def seeded_repo(tmp_path: Path) -> Path:
    """A throwaway repo: a 'dev' base and a feature branch, no commits yet."""
    repo = tmp_path / "occ"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "dev")
    _git(repo, "config", "user.name", "Base Human")
    _git(repo, "config", "user.email", "base@human.dev")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    # Emulate 'origin/dev' as a local ref so the guard's origin/dev... range resolves.
    _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    return repo


def _commit(repo: Path, rel: str, name: str, email: str, msg: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n", encoding="utf-8")
    _git(repo, "add", rel)
    _git(
        repo,
        "-c",
        f"user.name={name}",
        "-c",
        f"user.email={email}",
        "commit",
        "-q",
        "-m",
        msg,
    )


def test_seeded_bot_pr_touching_grants_is_rejected(seeded_repo: Path) -> None:
    """Headline proof: bot commit touching grants/** -> exit 1 via git path."""
    _commit(
        seeded_repo,
        "grants/prod_promotion_grants.yaml",
        "omnimarket-bot",
        "bot@omninode.ai",
        "evidence: sneak a grant",
    )
    rc = main(["--repo-root", str(seeded_repo), "--base-ref", "dev"])
    assert rc == _EXIT_REJECT


def test_seeded_human_pr_touching_grants_passes(seeded_repo: Path) -> None:
    _commit(
        seeded_repo,
        "grants/prod_promotion_grants.yaml",
        "Jonah Gray",
        "jonah.neugass@gmail.com",
        "add a real grant",
    )
    rc = main(["--repo-root", str(seeded_repo), "--base-ref", "dev"])
    assert rc == _EXIT_PASS


def test_seeded_bot_pr_touching_only_evidence_passes(seeded_repo: Path) -> None:
    _commit(
        seeded_repo,
        "contracts/OMN-1.yaml",
        "omnimarket-bot",
        "bot@omninode.ai",
        "evidence companion",
    )
    rc = main(["--repo-root", str(seeded_repo), "--base-ref", "dev"])
    assert rc == _EXIT_PASS


def test_resolve_changed_files_and_identities_end_to_end(seeded_repo: Path) -> None:
    _commit(
        seeded_repo,
        "allowlists/omnimarket.yaml",
        "omnimarket-bot",
        "bot@omninode.ai",
        "sneak an allowlist entry",
    )
    files = resolve_changed_files(str(seeded_repo), "dev")
    assert "allowlists/omnimarket.yaml" in files
    identities = resolve_commit_identities(str(seeded_repo), "dev")
    assert ("omnimarket-bot", "bot@omninode.ai") in identities


def test_resolve_changed_files_raises_on_bad_base() -> None:
    """Fail-closed: an unresolvable base ref raises rather than returning []."""
    with pytest.raises(_GitFactError):
        resolve_changed_files(".", "definitely-not-a-real-branch-xyz")


def test_main_fails_closed_when_git_unreadable() -> None:
    """Git-unreadable commit identities on a sensitive path -> INCONCLUSIVE."""
    with mock.patch(
        "onex_change_control.scripts.check_bot_authored_authz_guard.resolve_commit_identities",
        side_effect=_GitFactError("boom"),
    ):
        rc = main(
            [
                "--repo-root",
                ".",
                "--changed-file",
                "grants/prod_promotion_grants.yaml",
            ]
        )
    assert rc == _EXIT_INCONCLUSIVE


def test_explicit_args_seeded_bot_rejects() -> None:
    """The seeded-proof invocation form used in the PR body."""
    rc = main(
        [
            "--changed-file",
            "grants/prod_promotion_grants.yaml",
            "--commit-identity",
            "omnimarket-bot\tbot@omninode.ai",
        ]
    )
    assert rc == _EXIT_REJECT


# ---------------------------------------------------------------------------
# --staged mode (OMN-15008): pre-commit mirror of the CI gate. Same evaluate()
# decision core; only the fact-gathering (staged diff + committer ident
# in-progress, rather than a merged PR's changed files + commit history) differs.
# ---------------------------------------------------------------------------


def _stage(repo: Path, rel: str) -> None:
    """Stage (but do not commit) a new file at ``rel`` under ``repo``."""
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n", encoding="utf-8")
    _git(repo, "add", rel)


def test_seeded_bot_staged_touching_grants_is_rejected(
    seeded_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headline proof: a BOT-identity staged change touching grants/** -> REJECT."""
    _stage(seeded_repo, "grants/prod_promotion_grants.yaml")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "omnimarket-bot")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "bot@omninode.ai")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "omnimarket-bot")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "bot@omninode.ai")
    rc = main(["--staged", "--repo-root", str(seeded_repo)])
    assert rc == _EXIT_REJECT


def test_seeded_human_staged_touching_grants_passes(
    seeded_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stage(seeded_repo, "grants/prod_promotion_grants.yaml")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Jonah Gray")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "jonah.gabriel@gmail.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Jonah Gray")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "jonah.gabriel@gmail.com")
    rc = main(["--staged", "--repo-root", str(seeded_repo)])
    assert rc == _EXIT_PASS


def test_seeded_bot_staged_touching_only_evidence_passes(
    seeded_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard not applicable: bot staged change touches only contracts/ (evidence)."""
    _stage(seeded_repo, "contracts/OMN-1.yaml")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "omnimarket-bot")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "bot@omninode.ai")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "omnimarket-bot")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "bot@omninode.ai")
    rc = main(["--staged", "--repo-root", str(seeded_repo)])
    assert rc == _EXIT_PASS


def test_resolve_staged_changed_files_preserves_tab_in_filename(
    seeded_repo: Path,
) -> None:
    """CodeRabbit finding (PR #4692): plain `--name-only` C-quotes special-char
    filenames (tabs, newlines, non-ASCII), corrupting the leading grants/
    prefix `is_sensitive_path` matches on. `-z` (NUL-delimited) output must
    preserve it verbatim.
    """
    rel = "grants/policy\toverride.yaml"
    _stage(seeded_repo, rel)
    files = resolve_staged_changed_files(str(seeded_repo))
    assert rel in files


def test_seeded_bot_staged_path_with_tab_in_filename_is_rejected(
    seeded_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end proof the tab-in-filename fix actually changes the verdict:
    a bot-authored staged change under grants/ with a tab in the filename
    must still REJECT (it would silently PASS if the prefix got corrupted).
    """
    _stage(seeded_repo, "grants/policy\toverride.yaml")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "omnimarket-bot")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "bot@omninode.ai")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "omnimarket-bot")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "bot@omninode.ai")
    rc = main(["--staged", "--repo-root", str(seeded_repo)])
    assert rc == _EXIT_REJECT


def test_staged_identity_unresolvable_is_inconclusive(tmp_path: Path) -> None:
    """Fail-closed: --staged against a non-git directory -> INCONCLUSIVE."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    rc = main(["--staged", "--repo-root", str(not_a_repo)])
    assert rc == _EXIT_INCONCLUSIVE


class TestPrecommitConfigDeclaresStagedHook:
    """Config test (RED on current tree, OMN-15008): the pre-commit mirror of
    the CI gate must exist in BOTH .pre-commit-hooks.yaml (the shippable hook
    definition) and .pre-commit-config.yaml (this repo's own local wiring of
    it), scoped to grants/**+allowlists/** and invoking --staged.
    """

    _REPO_ROOT = Path(__file__).resolve().parents[3]

    def _find_hook(self, config_path: Path) -> dict[str, object]:
        import yaml

        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        hooks: list[dict[str, object]]
        if isinstance(data, list):
            hooks = data  # .pre-commit-hooks.yaml: bare top-level list
        else:
            hooks = [
                hook
                for repo in data["repos"]
                if repo.get("repo") == "local"
                for hook in repo["hooks"]
            ]
        matches = [h for h in hooks if h.get("id") == "check-bot-authored-authz-guard"]
        assert matches, (
            f"no 'check-bot-authored-authz-guard' hook declared in {config_path}"
        )
        return matches[0]

    def test_pre_commit_hooks_yaml_declares_hook(self) -> None:
        hook = self._find_hook(self._REPO_ROOT / ".pre-commit-hooks.yaml")
        entry = hook["entry"]
        assert isinstance(entry, str)
        assert "--staged" in entry
        assert hook.get("files") == r"^(grants/|allowlists/)"
        assert hook.get("pass_filenames") is False

    def test_pre_commit_config_yaml_wires_hook(self) -> None:
        hook = self._find_hook(self._REPO_ROOT / ".pre-commit-config.yaml")
        entry = hook["entry"]
        assert isinstance(entry, str)
        assert "--staged" in entry
        assert hook.get("files") == r"^(grants/|allowlists/)"
        assert hook.get("pass_filenames") is False
