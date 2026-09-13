# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Regression coverage for the blocking skip-token pre-commit hook."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".pre-commit-hooks" / "reject-deploy-gate-skip-token.sh"
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
EXCLUSION_REGEX = (
    r"^(tests/.*fixtures/|contracts/(OMN-10414|OMN-10417|OMN-10967)\.yaml|"
    r"drift/dod_receipts/OMN-10414/dod-004/command\.yaml)$"
)


def _configured_hook_arguments() -> tuple[str, ...]:
    """Return the configured hook flags, exactly as pre-commit will parse them."""
    config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    hook = next(
        item
        for repository in config["repos"]
        for item in repository["hooks"]
        if item["id"] == "reject-deploy-gate-skip-token"
    )
    entry = shlex.split(hook["entry"])
    assert entry[0] == ".pre-commit-hooks/reject-deploy-gate-skip-token.sh"
    return tuple(entry[1:])


def _run_hook(
    working_directory: Path,
    *arguments: str | Path,
    environment: dict[str, str] | None = None,
    interpreter: str = "bash",
) -> subprocess.CompletedProcess[str]:
    """Run the hook with the supplied direct or configured arguments."""
    run_environment = os.environ.copy()
    if environment is not None:
        run_environment.update(environment)
    return subprocess.run(
        [interpreter, str(HOOK), *(str(argument) for argument in arguments)],
        capture_output=True,
        check=False,
        cwd=working_directory,
        env=run_environment,
        text=True,
    )


def _run_normal_hook(
    working_directory: Path,
    *,
    environment: dict[str, str] | None = None,
    interpreter: str = "bash",
) -> subprocess.CompletedProcess[str]:
    """Run the zero-filename production path with its configured exemptions."""
    return _run_hook(
        working_directory,
        *_configured_hook_arguments(),
        environment=environment,
        interpreter=interpreter,
    )


def _git(path: Path, *arguments: str) -> None:
    """Run a checked Git command in an isolated test repository."""
    subprocess.run(["git", *arguments], check=True, cwd=path)


def _git_output(path: Path, *arguments: str) -> str:
    """Return a checked Git command's stripped stdout in an isolated repository."""
    return subprocess.check_output(["git", *arguments], cwd=path, text=True).strip()


def _init_git_repository(
    path: Path,
    *,
    divergent_main_and_dev: bool = False,
    dev_baseline_files: dict[str, str] | None = None,
) -> None:
    """Initialize a feature branch tracking origin/dev in an isolated repository."""
    remote = path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    # Each synthetic origin receives many short fixture pushes. Disable its
    # opportunistic auto-GC so a shared-machine maintenance run cannot make a
    # deterministic hook regression appear to hang inside git-receive-pack.
    subprocess.run(["git", "-C", str(remote), "config", "gc.auto", "0"], check=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "OMN-17496 test")
    # The test repositories commit and push synthetic fixtures. Never allow a
    # caller's global hooksPath to execute ambient hooks in those subprocesses.
    _git(path, "config", "core.hooksPath", os.devnull)
    _git(path, "commit", "--allow-empty", "-qm", "initial")
    _git(path, "branch", "-M", "main")
    _git(path, "remote", "add", "origin", str(remote))
    _git(path, "push", "-qu", "origin", "main")
    _git(path, "checkout", "-qb", "dev", "origin/main")
    for relative_path, content in (dev_baseline_files or {}).items():
        file_path = path / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        _git(path, "add", "--", relative_path)
    if dev_baseline_files:
        _git(path, "commit", "-qm", "seed dev baseline")
    _git(path, "push", "-qu", "origin", "dev")

    if divergent_main_and_dev:
        _git(path, "checkout", "-q", "main")
        (path / "main-only.txt").write_text("main diverged\n")
        _git(path, "add", "main-only.txt")
        _git(path, "commit", "-qm", "main diverges")
        _git(path, "push", "-q", "origin", "main")

        _git(path, "checkout", "-q", "dev")
        (path / "dev-only.md").write_text("[skip-deploy-gate: dev evidence]\n")
        _git(path, "add", "dev-only.md")
        _git(path, "commit", "-qm", "dev diverges")
        _git(path, "push", "-q", "origin", "dev")

    _git(path, "checkout", "-qb", "feature", "--track", "origin/dev")
    # Explicit/event bases that equal HEAD are deliberately rejected: they
    # prove no committed branch surface. Keep ordinary regressions on a real
    # feature commit so their selected origin/dev base is meaningful.
    (path / "feature-context.txt").write_text("ordinary committed feature context\n")
    _git(path, "add", "feature-context.txt")
    _git(path, "commit", "-qm", "seed feature context")


def _stage_file(path: Path, relative_path: str, content: str) -> None:
    """Write and stage a literal path, including colon-prefixed names."""
    file_path = path / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)
    pathspec = (
        f":(literal){relative_path}" if relative_path.startswith(":") else relative_path
    )
    _git(path, "add", "--", pathspec)


def _instrument_git_calls(path: Path) -> tuple[Path, dict[str, str]]:
    """Wrap Git to record every hook subprocess and optionally force failures."""
    real_git = shutil.which("git")
    assert real_git is not None
    bin_directory = path / "bin"
    bin_directory.mkdir()
    git_log = path / "git-commands.log"
    wrapper = bin_directory / "git"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'printf \'%s\\n\' "$*" >> "$OMN17496_GIT_LOG"\n'
        'if [[ "${OMN17496_FAIL_GIT_SUBCOMMAND:-}" == "$1" ]]; then\n'
        "    exit 86\n"
        "fi\n"
        'exec "$OMN17496_REAL_GIT" "$@"\n'
    )
    wrapper.chmod(0o755)
    return git_log, {
        "OMN17496_GIT_LOG": str(git_log),
        "OMN17496_REAL_GIT": real_git,
        "PATH": f"{bin_directory}{os.pathsep}{os.environ['PATH']}",
    }


@pytest.mark.unit
def test_hook_configuration_passes_the_legacy_exclusions_explicitly() -> None:
    """No-argv execution receives the prior pre-commit exclusions in-process."""
    config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    hook = next(
        item
        for repository in config["repos"]
        for item in repository["hooks"]
        if item["id"] == "reject-deploy-gate-skip-token"
    )

    assert hook["pass_filenames"] is False
    assert hook["always_run"] is True
    assert hook["require_serial"] is True
    assert "exclude" not in hook
    assert _configured_hook_arguments() == ("--exclude-regex", EXCLUSION_REGEX)


@pytest.mark.unit
def test_manual_mode_preserves_blocking_and_allowlist_behavior(tmp_path: Path) -> None:
    """Explicit manual paths remain fail-closed and honor approval receipts."""
    blocked = tmp_path / "blocked.md"
    blocked.write_text("[skip-receipt-gate: no evidence]\n")
    allowed = tmp_path / "allowed.yaml"
    allowed.write_text(
        "[skip-receipt-gate: approved]\n# skip-token-allowed: USER-APPROVAL-1\n"
    )
    clean = tmp_path / "clean.txt"
    clean.write_text("ordinary evidence\n")

    blocked_result = _run_hook(tmp_path, blocked)
    assert blocked_result.returncode == 1
    assert (
        f"ERROR: {blocked} contains a [skip-*] bypass token." in blocked_result.stderr
    )

    allowed_result = _run_hook(tmp_path, allowed)
    assert allowed_result.returncode == 0
    assert "explicit approval receipt present" in allowed_result.stderr

    clean_result = _run_hook(tmp_path, clean)
    assert clean_result.returncode == 0


@pytest.mark.unit
def test_normal_mode_reads_the_staged_blob_not_the_working_tree(tmp_path: Path) -> None:
    """A post-stage working-tree edit cannot hide an indexed bypass token."""
    _init_git_repository(tmp_path)
    _stage_file(
        tmp_path,
        "nested/staged-authority.md",
        "[skip-deploy-gate: staged]\n",
    )
    (tmp_path / "nested/staged-authority.md").write_text("working tree is clean\n")

    result = _run_normal_hook(tmp_path, environment={"GITHUB_BASE_REF": "dev"})

    assert result.returncode == 1
    assert (
        "nested/staged-authority.md contains a [skip-*] bypass token." in result.stderr
    )


@pytest.mark.unit
def test_normal_mode_aggregates_multiple_matching_lines_from_one_indexed_file(
    tmp_path: Path,
) -> None:
    """Multiple grep records for one path still reach the final rejection."""
    _init_git_repository(tmp_path)
    _stage_file(
        tmp_path,
        "multiple.md",
        "[skip-deploy-gate: first]\n[skip-receipt-gate: second]\n",
    )

    result = _run_normal_hook(tmp_path, environment={"GITHUB_BASE_REF": "dev"})

    assert result.returncode == 1
    assert "multiple.md contains a [skip-*] bypass token." in result.stderr


@pytest.mark.unit
@pytest.mark.parametrize(
    "file_name",
    [
        "evidence with spaces.md",
        "-leading-dash.md",
        ":leading-colon.md",
        "evidence\nwith-newline.md",
    ],
)
def test_normal_mode_preserves_special_and_nul_delimited_paths(
    tmp_path: Path,
    file_name: str,
) -> None:
    """Special names—including a newline—stay literal through NUL Git streams."""
    _init_git_repository(tmp_path)
    _stage_file(tmp_path, file_name, "[skip-deploy-gate: special path]\n")

    result = _run_normal_hook(tmp_path, environment={"GITHUB_BASE_REF": "dev"})

    assert result.returncode == 1
    assert f"ERROR: {file_name} contains a [skip-*] bypass token." in result.stderr


@pytest.mark.unit
def test_normal_mode_preserves_allowlist_and_configured_exemptions(
    tmp_path: Path,
) -> None:
    """The single index scan keeps receipt allowlists and old exclusions exact."""
    _init_git_repository(tmp_path)
    _stage_file(
        tmp_path,
        "contracts/OMN-10414.yaml",
        "[skip-deploy-gate: historical contract]\n",
    )
    _stage_file(
        tmp_path,
        "approved.md",
        "[skip-receipt-gate: approved]\n# skip-token-allowed: APPROVAL-1\n",
    )

    exempt_result = _run_normal_hook(
        tmp_path,
        environment={"GITHUB_BASE_REF": "dev"},
    )

    assert exempt_result.returncode == 0
    assert "approved.md but explicit approval receipt present" in exempt_result.stderr

    _stage_file(tmp_path, "enforced.md", "[skip-anything: enforce this]\n")
    enforced_result = _run_normal_hook(
        tmp_path,
        environment={"GITHUB_BASE_REF": "dev"},
    )

    assert enforced_result.returncode == 1
    assert "enforced.md contains a [skip-*] bypass token." in enforced_result.stderr
    assert "OMN-10414.yaml contains" not in enforced_result.stderr


@pytest.mark.unit
def test_normal_mode_path_membership_remains_case_sensitive(tmp_path: Path) -> None:
    """A matching lowercase indexed path cannot match a clean uppercase candidate."""
    _init_git_repository(
        tmp_path,
        dev_baseline_files={"foo.md": "[skip-deploy-gate: baseline token]\n"},
    )
    _stage_file(tmp_path, "Foo.md", "ordinary staged evidence\n")

    result = _run_normal_hook(tmp_path, environment={"GITHUB_BASE_REF": "dev"})

    assert result.returncode == 0, result.stderr
    assert "foo.md contains a [skip-*] bypass token." not in result.stderr
    assert "shopt -u nocasematch" in HOOK.read_text()


@pytest.mark.unit
def test_normal_mode_uses_versioned_integration_base_without_upstream_inference(
    tmp_path: Path,
) -> None:
    """Ordinary local commits use the repository's declared integration base."""
    _init_git_repository(tmp_path)
    _stage_file(tmp_path, "upstream.md", "[skip-deploy-gate: upstream target]\n")

    result = _run_normal_hook(tmp_path)

    assert result.returncode == 1
    assert "upstream.md contains a [skip-*] bypass token." in result.stderr
    source = HOOK.read_text()
    assert 'INTEGRATION_BASE_REF="origin/dev"' in source
    assert "git fetch" not in source
    assert "refs/remotes/origin/HEAD" not in source


@pytest.mark.unit
def test_normal_mode_scans_committed_changes_after_origin_self_tracking(
    tmp_path: Path,
) -> None:
    """A pushed feature's own upstream never excludes its committed changes."""
    _init_git_repository(tmp_path)
    _stage_file(tmp_path, "self-tracking.md", "[skip-deploy-gate: enforce]\n")
    _git(tmp_path, "commit", "-qm", "committed feature token")
    _git(tmp_path, "push", "-qu", "origin", "feature")
    _git(tmp_path, "branch", "--set-upstream-to=origin/feature", "feature")
    _stage_file(tmp_path, "later-clean.md", "ordinary later developer commit\n")

    result = _run_normal_hook(tmp_path)

    assert result.returncode == 1
    assert "self-tracking.md contains a [skip-*] bypass token." in result.stderr


@pytest.mark.unit
def test_normal_mode_scans_committed_changes_after_fork_self_tracking(
    tmp_path: Path,
) -> None:
    """Self-tracking is unsafe on every remote name, not only origin."""
    _init_git_repository(tmp_path)
    _stage_file(tmp_path, "fork-self-tracking.md", "[skip-deploy-gate: enforce]\n")
    _git(tmp_path, "commit", "-qm", "committed fork feature token")
    _git(tmp_path, "remote", "add", "fork", str(tmp_path / "origin.git"))
    _git(tmp_path, "push", "-qu", "fork", "feature")
    _stage_file(tmp_path, "later-clean.md", "ordinary later developer commit\n")

    result = _run_normal_hook(tmp_path)

    assert result.returncode == 1
    assert "fork-self-tracking.md contains a [skip-*] bypass token." in result.stderr


@pytest.mark.unit
def test_normal_mode_ignores_origin_head_when_upstream_is_unavailable(
    tmp_path: Path,
) -> None:
    """Remote default is not proof of a feature's PR target branch."""
    _init_git_repository(tmp_path, divergent_main_and_dev=True)
    _git(tmp_path, "branch", "--unset-upstream")
    _git(
        tmp_path,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/main",
    )
    _stage_file(tmp_path, "origin-head.md", "ordinary staged evidence\n")

    result = _run_normal_hook(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "dev-only.md contains" not in result.stderr


@pytest.mark.unit
def test_normal_mode_honors_documented_per_branch_base_configuration(
    tmp_path: Path,
) -> None:
    """Declared stacked or promotion bases use local branch configuration."""
    _init_git_repository(tmp_path, divergent_main_and_dev=True)
    _git(tmp_path, "config", "branch.feature.onexSkipTokenBase", "origin/main")
    _stage_file(tmp_path, "configured-base.md", "ordinary staged evidence\n")

    result = _run_normal_hook(tmp_path)

    assert result.returncode == 1
    assert "dev-only.md contains a [skip-*] bypass token." in result.stderr


@pytest.mark.unit
def test_normal_mode_rejects_a_self_referential_local_base(tmp_path: Path) -> None:
    """A local branch configuration cannot name the feature itself."""
    _init_git_repository(tmp_path)
    _git(tmp_path, "config", "branch.feature.onexSkipTokenBase", "origin/feature")
    _stage_file(tmp_path, "ordinary.md", "ordinary staged evidence\n")

    result = _run_normal_hook(tmp_path)

    assert result.returncode == 1
    assert "resolved base origin/feature is this feature branch" in result.stderr


@pytest.mark.unit
def test_normal_mode_rejects_a_self_referential_github_base(tmp_path: Path) -> None:
    """A local GITHUB_BASE_REF override cannot omit pushed feature commits."""
    _init_git_repository(tmp_path)
    _stage_file(tmp_path, "github-self-tracking.md", "[skip-deploy-gate: enforce]\n")
    _git(tmp_path, "commit", "-qm", "committed feature token")
    _git(tmp_path, "push", "-qu", "origin", "feature")

    result = _run_normal_hook(tmp_path, environment={"GITHUB_BASE_REF": "feature"})

    assert result.returncode == 1
    assert (
        "origin/feature is HEAD; refusing an empty committed-change scan"
        in result.stderr
    )


@pytest.mark.unit
def test_workflow_dispatch_rejects_a_self_referential_branch_base(
    tmp_path: Path,
) -> None:
    """A dispatch-selected branch cannot name the checked-out feature itself."""
    _init_git_repository(tmp_path)
    _stage_file(tmp_path, "dispatch-self-tracking.md", "[skip-deploy-gate: enforce]\n")
    _git(tmp_path, "commit", "-qm", "committed feature token")
    _git(tmp_path, "push", "-qu", "origin", "feature")

    result = _run_normal_hook(
        tmp_path,
        environment={
            "ONEX_SKIP_TOKEN_EVENT": "workflow_dispatch",
            "ONEX_SKIP_TOKEN_BASE": "feature",
        },
    )

    assert result.returncode == 1
    assert (
        "origin/feature is HEAD; refusing an empty committed-change scan"
        in result.stderr
    )


@pytest.mark.unit
def test_workflow_dispatch_rejects_a_detached_base_equal_to_head(
    tmp_path: Path,
) -> None:
    """Detached manual dispatches cannot select HEAD as an empty scan base."""
    _init_git_repository(tmp_path)
    _stage_file(tmp_path, "detached-self-tracking.md", "[skip-deploy-gate: enforce]\n")
    _git(tmp_path, "commit", "-qm", "committed feature token")
    _git(tmp_path, "checkout", "--detach")
    head_sha = _git_output(tmp_path, "rev-parse", "HEAD")

    result = _run_normal_hook(
        tmp_path,
        environment={
            "ONEX_SKIP_TOKEN_EVENT": "workflow_dispatch",
            "ONEX_SKIP_TOKEN_BASE": head_sha,
        },
    )

    assert result.returncode == 1
    assert "is HEAD; refusing an empty committed-change scan" in result.stderr


@pytest.mark.unit
def test_github_base_ref_rejects_an_attached_exact_head_sha(tmp_path: Path) -> None:
    """An attached explicit object ID cannot select the current feature head."""
    _init_git_repository(tmp_path)
    head_sha = _git_output(tmp_path, "rev-parse", "HEAD")

    result = _run_normal_hook(tmp_path, environment={"GITHUB_BASE_REF": head_sha})

    assert result.returncode == 1
    assert "is HEAD; refusing an empty committed-change scan" in result.stderr


@pytest.mark.unit
def test_workflow_dispatch_rejects_an_attached_exact_head_sha(tmp_path: Path) -> None:
    """A dispatch SHA is checked against HEAD even outside detached CI."""
    _init_git_repository(tmp_path)
    head_sha = _git_output(tmp_path, "rev-parse", "HEAD")

    result = _run_normal_hook(
        tmp_path,
        environment={
            "ONEX_SKIP_TOKEN_EVENT": "workflow_dispatch",
            "ONEX_SKIP_TOKEN_BASE": head_sha,
        },
    )

    assert result.returncode == 1
    assert "is HEAD; refusing an empty committed-change scan" in result.stderr


@pytest.mark.unit
@pytest.mark.parametrize("event_name", ["pull_request", "merge_group"])
def test_non_push_authoritative_exact_head_shas_fail_closed(
    tmp_path: Path, event_name: str
) -> None:
    """PR and merge-queue object bases cannot create an empty branch range."""
    _init_git_repository(tmp_path)
    head_sha = _git_output(tmp_path, "rev-parse", "HEAD")

    result = _run_normal_hook(
        tmp_path,
        environment={
            "ONEX_SKIP_TOKEN_EVENT": event_name,
            "ONEX_SKIP_TOKEN_BASE": head_sha,
        },
    )

    assert result.returncode == 1
    assert "is HEAD; refusing an empty committed-change scan" in result.stderr


@pytest.mark.unit
@pytest.mark.parametrize(
    ("environment", "detached"),
    [
        ({"GITHUB_BASE_REF": "dev"}, False),
        (
            {
                "ONEX_SKIP_TOKEN_EVENT": "workflow_dispatch",
                "ONEX_SKIP_TOKEN_BASE": "dev",
            },
            False,
        ),
        ({"GITHUB_BASE_REF": "dev"}, True),
        (
            {
                "ONEX_SKIP_TOKEN_EVENT": "workflow_dispatch",
                "ONEX_SKIP_TOKEN_BASE": "dev",
            },
            True,
        ),
    ],
)
def test_valid_attached_and_detached_external_bases_keep_five_git_calls(
    tmp_path: Path,
    environment: dict[str, str],
    detached: bool,  # noqa: FBT001 -- pytest parametrizes the execution mode.
) -> None:
    """HEAD caching keeps every valid external-base path at five Git calls."""
    _init_git_repository(tmp_path)
    if detached:
        _git(tmp_path, "checkout", "--detach")
    git_log, instrumentation = _instrument_git_calls(tmp_path)
    instrumentation.update(environment)

    result = _run_normal_hook(tmp_path, environment=instrumentation)

    assert result.returncode == 0, result.stderr
    assert [call.split()[0] for call in git_log.read_text().splitlines()] == [
        "rev-parse",
        "merge-base",
        "diff",
        "diff",
        "grep",
    ]


@pytest.mark.unit
def test_normal_mode_works_with_system_bash_and_restricted_path(tmp_path: Path) -> None:
    """The O(M + N) literal-path map works with macOS Bash 3.2 provisioning."""
    _init_git_repository(tmp_path)
    _stage_file(tmp_path, "restricted-path.md", "[skip-deploy-gate: enforce]\n")

    result = _run_normal_hook(
        tmp_path,
        environment={"GITHUB_BASE_REF": "dev", "PATH": "/usr/bin:/bin"},
        interpreter="/bin/bash",
    )

    assert result.returncode == 1
    assert "restricted-path.md contains a [skip-*] bypass token." in result.stderr


@pytest.mark.unit
def test_divergent_main_and_dev_prove_explicit_base_selection_and_fail_closed(
    tmp_path: Path,
) -> None:
    """A divergent selected base uses its merge base, never a branch fallback."""
    _init_git_repository(tmp_path, divergent_main_and_dev=True)

    dev_result = _run_normal_hook(tmp_path, environment={"GITHUB_BASE_REF": "dev"})
    main_result = _run_normal_hook(tmp_path, environment={"GITHUB_BASE_REF": "main"})

    assert dev_result.returncode == 0
    assert main_result.returncode == 1
    assert "dev-only.md contains a [skip-*] bypass token." in main_result.stderr


@pytest.mark.unit
def test_normal_mode_fails_closed_without_a_common_ancestor(tmp_path: Path) -> None:
    """A resolved but unrelated base cannot be used as scan evidence."""
    _init_git_repository(tmp_path)
    _git(tmp_path, "checkout", "--orphan", "unrelated")
    _git(tmp_path, "commit", "--allow-empty", "-qm", "unrelated initial")

    result = _run_normal_hook(tmp_path, environment={"GITHUB_BASE_REF": "dev"})

    assert result.returncode == 1
    assert "has no common ancestor with HEAD" in result.stderr


@pytest.mark.unit
def test_normal_mode_fails_closed_when_explicit_base_is_unavailable(
    tmp_path: Path,
) -> None:
    """A missing remote-tracking base cannot turn into an empty candidate set."""
    _init_git_repository(tmp_path)

    result = _run_normal_hook(tmp_path, environment={"GITHUB_BASE_REF": "missing"})

    assert result.returncode == 1
    assert "resolved PR base origin/missing is unavailable locally" in result.stderr


@pytest.mark.unit
def test_normal_mode_ignores_a_staged_deletion_that_is_not_in_the_index(
    tmp_path: Path,
) -> None:
    """Deleted candidates do not trigger working-tree or per-file blob reads."""
    _init_git_repository(tmp_path)
    _stage_file(tmp_path, "deleted.md", "[skip-deploy-gate: historical]\n")
    _git(tmp_path, "commit", "-qm", "add deleted evidence")
    _git(tmp_path, "rm", "-q", "deleted.md")

    result = _run_normal_hook(tmp_path, environment={"GITHUB_BASE_REF": "dev"})

    assert result.returncode == 0


@pytest.mark.unit
def test_normal_mode_fails_closed_on_a_git_subprocess_error(tmp_path: Path) -> None:
    """A failed candidate-discovery Git command cannot silently skip enforcement."""
    _init_git_repository(tmp_path)
    git_log, environment = _instrument_git_calls(tmp_path)
    environment.update(
        {
            "GITHUB_BASE_REF": "dev",
            "OMN17496_FAIL_GIT_SUBCOMMAND": "diff",
        }
    )

    result = _run_normal_hook(tmp_path, environment=environment)

    assert result.returncode == 1
    assert "could not read staged changed paths" in result.stderr
    assert [call.split()[0] for call in git_log.read_text().splitlines()] == [
        "rev-parse",
        "merge-base",
        "diff",
    ]


@pytest.mark.unit
def test_local_policy_base_resolution_has_a_seven_git_call_budget(
    tmp_path: Path,
) -> None:
    """Local base configuration adds only branch and config lookups."""
    _init_git_repository(tmp_path)
    _stage_file(tmp_path, "ordinary.md", "ordinary indexed evidence\n")
    git_log, environment = _instrument_git_calls(tmp_path)

    result = _run_normal_hook(tmp_path, environment=environment)

    assert result.returncode == 0, result.stderr
    assert [call.split()[0] for call in git_log.read_text().splitlines()] == [
        "symbolic-ref",
        "config",
        "rev-parse",
        "merge-base",
        "diff",
        "diff",
        "grep",
    ]


@pytest.mark.unit
def test_1001_matching_staged_paths_use_exactly_five_git_subprocesses(
    tmp_path: Path,
) -> None:
    """Many matching paths stay bounded and deduplicate their two grep records."""
    _init_git_repository(tmp_path)
    bulk_directory = tmp_path / "bulk"
    bulk_directory.mkdir()
    staged_paths = []
    for index in range(1001):
        relative_path = Path("bulk") / f"evidence-{index:04d}.md"
        (tmp_path / relative_path).write_text(
            "[skip-deploy-gate: first matching line]\n"
            "[skip-receipt-gate: second matching line]\n"
        )
        staged_paths.append(relative_path.as_posix())
    _git(tmp_path, "add", "--", *staged_paths)

    git_log, environment = _instrument_git_calls(tmp_path)
    environment["GITHUB_BASE_REF"] = "dev"
    result = _run_normal_hook(tmp_path, environment=environment)
    git_calls = git_log.read_text().splitlines()

    assert result.returncode == 1
    assert result.stderr.count("contains a [skip-*] bypass token.") == 1001
    assert [call.split()[0] for call in git_calls] == [
        "rev-parse",
        "merge-base",
        "diff",
        "diff",
        "grep",
    ]
    assert len(git_calls) == 5


@pytest.mark.unit
@pytest.mark.parametrize(
    ("event_name", "base_ref"),
    [
        ("pull_request", "dev"),
        ("push", "sha"),
        ("merge_group", "sha"),
        ("workflow_dispatch", "dev"),
    ],
)
def test_authoritative_event_bases_scan_committed_changes(
    tmp_path: Path, event_name: str, base_ref: str
) -> None:
    """Every CI event supplies an explicit base, including verified SHA forms."""
    _init_git_repository(tmp_path)
    _stage_file(tmp_path, "committed.md", "[skip-deploy-gate: enforce]\n")
    _git(tmp_path, "commit", "-qm", "committed skip token")
    resolved_base = (
        _git_output(tmp_path, "rev-parse", "origin/dev")
        if base_ref == "sha"
        else base_ref
    )

    result = _run_normal_hook(
        tmp_path,
        environment={
            "ONEX_SKIP_TOKEN_EVENT": event_name,
            "ONEX_SKIP_TOKEN_BASE": resolved_base,
            "ONEX_SKIP_TOKEN_PUSH_FORCED": "false",
        },
    )

    assert result.returncode == 1
    assert "committed.md contains a [skip-*] bypass token." in result.stderr


@pytest.mark.unit
def test_authoritative_commit_base_is_not_prefixed_as_an_origin_branch(
    tmp_path: Path,
) -> None:
    """A merge-group or push event can provide a verified base object ID."""
    _init_git_repository(tmp_path, divergent_main_and_dev=True)
    main_base = _git_output(tmp_path, "rev-parse", "origin/main")

    result = _run_normal_hook(
        tmp_path,
        environment={
            "ONEX_SKIP_TOKEN_EVENT": "merge_group",
            "ONEX_SKIP_TOKEN_BASE": main_base,
        },
    )

    assert result.returncode == 1
    assert "dev-only.md contains a [skip-*] bypass token." in result.stderr


@pytest.mark.unit
@pytest.mark.parametrize(
    "base_ref",
    [
        "HEAD",
        "origin/HEAD",
        "refs/remotes/origin/HEAD",
        "refs/heads/HEAD",
        "origin/HEAD^",
        "origin/HEAD~1",
        " origin/HEAD",
        "origin/HEAD\t",
    ],
)
def test_workflow_dispatch_rejects_mutable_or_malformed_head_bases_before_scan(
    tmp_path: Path, base_ref: str
) -> None:
    """Dispatch accepts only fixed branch names or 40-hex object IDs."""
    _init_git_repository(tmp_path)
    git_log, environment = _instrument_git_calls(tmp_path)
    environment.update(
        {
            "ONEX_SKIP_TOKEN_EVENT": "workflow_dispatch",
            "ONEX_SKIP_TOKEN_BASE": base_ref,
        }
    )

    result = _run_normal_hook(tmp_path, environment=environment)

    assert result.returncode == 1
    assert "workflow_dispatch supplied a malformed authoritative base" in result.stderr
    assert not git_log.exists()


@pytest.mark.unit
@pytest.mark.parametrize("base_ref", ["origin/HEAD", " origin/HEAD", "origin/HEAD^"])
def test_github_base_ref_rejects_mutable_or_malformed_head_aliases_before_scan(
    tmp_path: Path, base_ref: str
) -> None:
    """The local explicit GITHUB_BASE_REF route cannot select origin/HEAD."""
    _init_git_repository(tmp_path)
    git_log, environment = _instrument_git_calls(tmp_path)
    environment["GITHUB_BASE_REF"] = base_ref

    result = _run_normal_hook(tmp_path, environment=environment)

    assert result.returncode == 1
    assert "GITHUB_BASE_REF is malformed" in result.stderr
    assert not git_log.exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    "base_ref", ["origin/HEAD", "refs/heads/HEAD", "origin/HEAD~1"]
)
def test_local_config_rejects_mutable_or_malformed_head_aliases_before_scan(
    tmp_path: Path, base_ref: str
) -> None:
    """A declared local base remains a fixed branch, never a remote default."""
    _init_git_repository(tmp_path)
    _git(tmp_path, "config", "branch.feature.onexSkipTokenBase", base_ref)
    git_log, environment = _instrument_git_calls(tmp_path)

    result = _run_normal_hook(tmp_path, environment=environment)

    assert result.returncode == 1
    assert "configured local skip-token base is malformed" in result.stderr
    assert [call.split()[0] for call in git_log.read_text().splitlines()] == [
        "symbolic-ref",
        "config",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("event_name", "base_ref", "forced", "error"),
    [
        ("push", "", "false", "requires an explicit base"),
        ("push", "0" * 40, "false", "all-zero before SHA"),
        ("push", "dev", "true", "forced push"),
        ("merge_group", "not a valid ref", "false", "malformed"),
        ("workflow_dispatch", "", "false", "requires an explicit base"),
    ],
)
def test_authoritative_event_base_missing_or_unsafe_fails_closed(
    tmp_path: Path, event_name: str, base_ref: str, forced: str, error: str
) -> None:
    """CI event metadata never falls through to a detached or integration base."""
    _init_git_repository(tmp_path)

    result = _run_normal_hook(
        tmp_path,
        environment={
            "ONEX_SKIP_TOKEN_EVENT": event_name,
            "ONEX_SKIP_TOKEN_BASE": base_ref,
            "ONEX_SKIP_TOKEN_PUSH_FORCED": forced,
        },
    )

    assert result.returncode == 1
    assert error in result.stderr


@pytest.mark.unit
def test_ci_workflow_declares_an_event_base_for_every_precommit_trigger() -> None:
    """The detached CI checkout cannot rely on local branch resolution."""
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "skip_token_base:" in workflow
    assert "ONEX_SKIP_TOKEN_EVENT:" in workflow
    assert "ONEX_SKIP_TOKEN_BASE:" in workflow
    assert "github.event.pull_request.base.ref" in workflow
    assert "github.event.before" in workflow
    assert "github.event.merge_group.base_sha" in workflow
    assert "inputs.skip_token_base" in workflow
