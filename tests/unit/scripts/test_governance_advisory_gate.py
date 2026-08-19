# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for governance_advisory_gate (OMN-16117, second vector).

`auto-merge.yml`'s governance-path exclusion (OMN-16117 first vector,
`scripts/ci/check_governance_paths.py`) stops THIS repo's own `auto-merge.yml`
from arming auto-merge on a PR touching a guarded governance path. It says
nothing to an external driver that reads "all checks green" as its own
signal to run `gh pr merge` directly. This module backs a standalone,
ADVISORY (never required) CI check that hard-fails whenever a PR's diff
touches a guarded governance path, so that signal is never green for such a
PR.

The load-bearing property under test here, beyond the pass/fail decision
itself (already proven for `touches_governance_path` in
`test_check_governance_paths.py`), is **no second source of truth**: this
module must derive its governance-path list from
`scripts/ci/check_governance_paths.GOVERNANCE_PATHS` by import, never define
or hardcode an independent copy that could drift out of sync.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci import check_governance_paths
from scripts.ci.governance_advisory_gate import (
    _EXIT_FAIL,
    _EXIT_PASS,
    _PASS_MESSAGE,
    _format_failure_message,
    main,
)

pytestmark = pytest.mark.unit

_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "ci"
    / "governance_advisory_gate.py"
)
_REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# No second source of truth
# ---------------------------------------------------------------------------


class TestNoSecondSourceOfTruth:
    def test_imports_the_same_governance_paths_object(self) -> None:
        """The advisory gate must import GOVERNANCE_PATHS from
        check_governance_paths, not define its own tuple. Identity (`is`),
        not just equality, proves it's the same object -- a copy-pasted
        tuple with equal values would pass an equality check but still be a
        second, independently-editable source of truth."""
        import scripts.ci.governance_advisory_gate as governance_advisory_gate_module

        assert (
            governance_advisory_gate_module.GOVERNANCE_PATHS
            is check_governance_paths.GOVERNANCE_PATHS
        )

    def test_failure_message_lists_exactly_the_helper_paths(self) -> None:
        message = _format_failure_message(["grants/prod_promotion_grants.yaml"])
        for path in check_governance_paths.GOVERNANCE_PATHS:
            assert path in message

    def test_workflow_file_does_not_hardcode_a_second_path_list(self) -> None:
        """The new workflow must invoke the shared checker rather than embed
        its own YAML-literal list of governance paths."""
        workflow_path = (
            _REPO_ROOT / ".github" / "workflows" / "governance-file-advisory-gate.yml"
        )
        text = workflow_path.read_text(encoding="utf-8")
        assert "governance_advisory_gate" in text
        for path in check_governance_paths.GOVERNANCE_PATHS:
            # The governance paths may appear in a comment/doc-string quote,
            # but must not appear inside a YAML list/array literal assigned
            # to a variable in the workflow (that would be a second list).
            assert f"- {path}" not in text
            assert f'"{path}"' not in text
            assert f"'{path}'" not in text


# ---------------------------------------------------------------------------
# Pass / fail decision + message content
# ---------------------------------------------------------------------------


class TestFormatFailureMessage:
    def test_undetermined_message_says_undetermined(self) -> None:
        message = _format_failure_message(None)
        assert "UNDETERMINED" in message

    def test_lists_the_specific_touched_governance_file(self) -> None:
        message = _format_failure_message(
            ["src/foo.py", "grants/prod_promotion_grants.yaml"]
        )
        assert "grants/prod_promotion_grants.yaml" in message

    def test_states_grants_land_only_by_human_decision(self) -> None:
        message = _format_failure_message(["grants/prod_promotion_grants.yaml"])
        assert "human decision" in message.lower()
        assert "never by" in message.lower()

    def test_states_green_ci_is_not_authorization(self) -> None:
        message = _format_failure_message(["grants/prod_promotion_grants.yaml"])
        assert (
            "not authorization" in message.lower()
            or "not an authorization" in message.lower()
        )

    def test_cites_claude_md_rules_2a_and_12(self) -> None:
        message = _format_failure_message(["grants/prod_promotion_grants.yaml"])
        assert "rule 2a" in message.lower()
        assert "rule 12" in message.lower()

    def test_states_check_is_advisory_not_a_blocker_for_humans(self) -> None:
        message = _format_failure_message(["grants/prod_promotion_grants.yaml"])
        assert "advisory" in message.lower()


class TestMainCheckSubcommand:
    def test_governance_file_fails_and_prints_message(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            [
                "check",
                "--changed-files-json",
                json.dumps(["grants/prod_promotion_grants.yaml"]),
            ]
        )
        assert code == _EXIT_FAIL
        out = capsys.readouterr().out
        assert "GOVERNANCE-FILE ADVISORY GATE -- FAILED" in out
        assert "grants/prod_promotion_grants.yaml" in out

    def test_skip_token_allowlist_fails(self) -> None:
        code = main(
            [
                "check",
                "--changed-files-json",
                json.dumps(["allowlists/skip_token_approvals.yaml"]),
            ]
        )
        assert code == _EXIT_FAIL

    def test_ordinary_files_pass(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(
            ["check", "--changed-files-json", json.dumps(["contracts/OMN-16117.yaml"])]
        )
        assert code == _EXIT_PASS
        assert capsys.readouterr().out.strip() == _PASS_MESSAGE

    def test_empty_array_from_successful_query_passes(self) -> None:
        code = main(["check", "--changed-files-json", "[]"])
        assert code == _EXIT_PASS

    def test_literal_null_fails_closed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["check", "--changed-files-json", "null"])
        assert code == _EXIT_FAIL
        assert "UNDETERMINED" in capsys.readouterr().out

    def test_missing_flag_fails_closed(self) -> None:
        code = main(["check"])
        assert code == _EXIT_FAIL

    def test_malformed_json_fails_closed(self) -> None:
        code = main(["check", "--changed-files-json", "{not valid json"])
        assert code == _EXIT_FAIL

    def test_non_array_json_fails_closed(self) -> None:
        code = main(["check", "--changed-files-json", '{"filename": "grants/x.yaml"}'])
        assert code == _EXIT_FAIL


# ---------------------------------------------------------------------------
# End-to-end subprocess proof -- exact invocation shape the workflow uses
# (module invocation from repo root, matching `python3 -m scripts.ci...`).
# ---------------------------------------------------------------------------


class TestSubprocessInvocation:
    def test_governance_file_subprocess_exit_nonzero(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.ci.governance_advisory_gate",
                "check",
                "--changed-files-json",
                json.dumps(["grants/prod_promotion_grants.yaml"]),
            ],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == _EXIT_FAIL
        assert "GOVERNANCE-FILE ADVISORY GATE -- FAILED" in result.stdout

    def test_safe_pr_subprocess_exit_zero(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.ci.governance_advisory_gate",
                "check",
                "--changed-files-json",
                json.dumps(["src/onex_change_control/foo.py"]),
            ],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == _EXIT_PASS
