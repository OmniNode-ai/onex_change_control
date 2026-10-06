# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_governance_paths (OMN-16117).

`onex_change_control`'s `auto-merge.yml` arms squash auto-merge on every PR
authored by `jonahgabriel`, gated only on `occ-preflight / eligibility`
success. Agent sessions share that GitHub identity, so an agent-opened PR
touching `grants/prod_promotion_grants.yaml` (the OMN-13418 prod-promotion
trust anchor) could be armed and merged unattended.

This module isolates the pure governance-path decision logic from the
workflow's `gh api` changed-file lookup, and separately proves the CLI
`check` subcommand end-to-end. The load-bearing property is fail-closed
behavior: an undetermined changed-file set (API error, no PR number) must be
treated identically to "touches a governance file" -- never as "safe to
arm". A genuinely empty changed-file set from a *successful* query (a PR
that touches nothing) is the opposite case and must NOT be excluded.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci.check_governance_paths import (
    _EXIT_EXCLUDE,
    _EXIT_SAFE_TO_ARM,
    GOVERNANCE_PATHS,
    is_governance_path,
    main,
    touches_governance_path,
)

pytestmark = pytest.mark.unit

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "ci" / "check_governance_paths.py"
)


# ---------------------------------------------------------------------------
# Pure path predicate
# ---------------------------------------------------------------------------


class TestIsGovernancePath:
    @pytest.mark.parametrize("path", list(GOVERNANCE_PATHS))
    def test_exact_governance_paths(self, path: str) -> None:
        assert is_governance_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "./grants/prod_promotion_grants.yaml",
            "grants/prod_promotion_grants.yaml",
        ],
    )
    def test_normalizes_leading_dot_slash(self, path: str) -> None:
        assert is_governance_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "contracts/OMN-16117.yaml",
            "drift/dod_receipts/OMN-16117/x/command.yaml",
            "grants/other_grant.yaml",  # under grants/ but not the guarded file
            "allowlists/omnimarket.yaml",  # under allowlists/ but not the guarded file
            "scripts/ci/check_governance_paths.py",
            "README.md",
        ],
    )
    def test_non_governance_paths(self, path: str) -> None:
        assert is_governance_path(path) is False


# ---------------------------------------------------------------------------
# Pure decision logic — the four required cases from the ticket
# ---------------------------------------------------------------------------


class TestTouchesGovernancePath:
    def test_governance_file_present_is_excluded(self) -> None:
        """Governance file among changed files -> exclude (skip arming)."""
        assert (
            touches_governance_path(
                ["src/onex_change_control/foo.py", "grants/prod_promotion_grants.yaml"]
            )
            is True
        )

    def test_ordinary_files_only_not_excluded(self) -> None:
        """No governance file touched -> not excluded."""
        assert (
            touches_governance_path(
                ["contracts/OMN-16117.yaml", "src/onex_change_control/foo.py"]
            )
            is False
        )

    def test_empty_list_from_successful_query_not_excluded(self) -> None:
        """A genuinely empty changed-file set (successful query, zero files)
        is a real clean diff -- distinct from the undetermined case -- and
        must NOT be excluded."""
        assert touches_governance_path([]) is False

    def test_none_sentinel_is_excluded_fail_closed(self) -> None:
        """None means the changed-file set could not be determined (API
        error / missing PR number). Fail closed: treat as touching a
        governance file."""
        assert touches_governance_path(None) is True

    def test_skip_token_allowlist_is_governance(self) -> None:
        assert touches_governance_path(["allowlists/skip_token_approvals.yaml"]) is True


# ---------------------------------------------------------------------------
# CLI surface — the `check` subcommand the workflow shells out to
# ---------------------------------------------------------------------------


class TestMainCheckSubcommand:
    def test_governance_file_excludes_exit_code_and_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            [
                "check",
                "--changed-files-json",
                json.dumps(["grants/prod_promotion_grants.yaml"]),
            ]
        )
        assert code == _EXIT_EXCLUDE
        assert capsys.readouterr().out.strip() == "exclude"

    def test_ordinary_files_safe_to_arm(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            [
                "check",
                "--changed-files-json",
                json.dumps(["contracts/OMN-16117.yaml"]),
            ]
        )
        assert code == _EXIT_SAFE_TO_ARM
        assert capsys.readouterr().out.strip() == "safe_to_arm"

    def test_empty_array_safe_to_arm(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["check", "--changed-files-json", "[]"])
        assert code == _EXIT_SAFE_TO_ARM
        assert capsys.readouterr().out.strip() == "safe_to_arm"

    def test_literal_null_excludes_fail_closed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["check", "--changed-files-json", "null"])
        assert code == _EXIT_EXCLUDE
        assert capsys.readouterr().out.strip() == "exclude"

    def test_missing_flag_excludes_fail_closed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["check"])
        assert code == _EXIT_EXCLUDE
        assert capsys.readouterr().out.strip() == "exclude"

    def test_malformed_json_excludes_fail_closed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["check", "--changed-files-json", "{not valid json"])
        assert code == _EXIT_EXCLUDE
        assert capsys.readouterr().out.strip() == "exclude"

    def test_non_array_json_excludes_fail_closed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["check", "--changed-files-json", '{"filename": "grants/x.yaml"}'])
        assert code == _EXIT_EXCLUDE
        assert capsys.readouterr().out.strip() == "exclude"


# ---------------------------------------------------------------------------
# End-to-end subprocess proof — exact invocation shape the workflow uses.
# ---------------------------------------------------------------------------


class TestSubprocessInvocation:
    def test_governance_file_subprocess_exit_nonzero(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT),
                "check",
                "--changed-files-json",
                json.dumps(["allowlists/skip_token_approvals.yaml"]),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == _EXIT_EXCLUDE
        assert result.stdout.strip() == "exclude"

    def test_safe_pr_subprocess_exit_zero(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT),
                "check",
                "--changed-files-json",
                json.dumps(["src/onex_change_control/foo.py"]),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == _EXIT_SAFE_TO_ARM
        assert result.stdout.strip() == "safe_to_arm"
