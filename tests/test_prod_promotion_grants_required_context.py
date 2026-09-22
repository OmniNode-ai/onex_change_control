# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Make `Validate Prod Promotion Grants` safe to REQUIRE on `main` (OMN-18950).

A required status check has one property this workflow did not have: it must
reach a conclusion on every pull request that branch protection gates. A
path-filtered workflow does not. GitHub creates no check run at all for a pull
request whose files miss the filter, and branch protection then reports the
context as permanently `expected` — the pull request is blocked with no job to
rerun and nothing to click. Requiring the path-filtered form would have bricked
`main`, which is the defect class the 2026-09-20 silent-gates review catalogues
as "required contexts named after entrypoints they do not run".

So the trigger now has no path filter, and the decision the filter used to make
is made by the `scope` step INSIDE the job, after the check run exists:

  * the anchor is in this pull request  -> enforce; a finding is a red check,
  * the anchor is untouched             -> advisory; the SAME finding is still
                                           computed and printed as an
                                           annotation, and the check is green,
  * scope could not be determined       -> enforce. Indeterminate is not clean.

The advisory arm is not a softening of the gate. Every way a bad grant can
enter the anchor is a change to the anchor, and every such change enforces. The
arm exists because expiry happens with the passage of time rather than with a
change, and a required context must not block unrelated work on a state no one
in that pull request touched.

WHAT THIS WORKFLOW DOES NOT CHECK, pinned below as a negative control: its
inline schema validator does not compare `approved_by` to the requester, and
never did. `_check_self_approval` (OMN-17157) lives only in
`src/onex_change_control/scripts/validate_prod_promotion_grants.py` on `dev`,
and grant pull requests target `main`. OMN-18945, in flight as OCC#10632,
closes that on `main` — as a SEPARATE module and a separate `ci.yml` job held
by `CI Summary`, not in this heredoc, so the pin below stays true after it
lands. It exists so a green `validate-prod-promotion-grants` is never read as
proof of an approval property this context does not check.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).parent.parent
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "validate-prod-promotion-grants.yml"

#: The exact string branch protection must require. Not a guess: it is the
#: job's own `name:`, this workflow is not reusable, and no other workflow
#: calls it, so there is no `<caller-job> / ` prefix.
_REQUIRED_CONTEXT = "validate-prod-promotion-grants"

_GRANT_FILE = "grants/prod_promotion_grants.yaml"

#: A schema-valid image digest. Built rather than written out because 64 hex
#: characters plus indentation does not fit on one line.
_VALID_DIGEST = "sha256:" + "1" * 64


def _workflow() -> dict[Any, Any]:
    """The parsed workflow.

    `on:` is read back by PyYAML as the boolean `True`, not the string `"on"`,
    because YAML 1.1 says so. Both spellings are looked up rather than one, so
    a loader change cannot make these tests read an empty trigger and pass.
    """
    parsed: dict[Any, Any] = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return parsed


def _triggers() -> dict[str, Any]:
    parsed: dict[Any, Any] = _workflow()
    for key in (True, "on"):
        if key in parsed:
            triggers: dict[str, Any] = parsed[key]
            return triggers
    message = "The workflow declares no `on:` block at all"
    raise AssertionError(message)


def _job() -> dict[str, Any]:
    job: dict[str, Any] = _workflow()["jobs"][_REQUIRED_CONTEXT]
    return job


def _validator_source() -> str:
    """The inline Python the job runs, read out of its heredoc."""
    for step in _job()["steps"]:
        if "run" not in step:
            continue
        match = re.search(r"<<'PYEOF'\n(.*?)\n\s*PYEOF", step["run"], re.DOTALL)
        if match:
            return textwrap.dedent(match.group(1))
    message = "No PYEOF heredoc in the workflow; the behaviour tests cannot run nothing"
    raise AssertionError(message)


def _run_validator(
    tmp_path: Path, anchor: str, enforce: str | None
) -> subprocess.CompletedProcess[str]:
    """Run the workflow's own validator against a fixture anchor.

    A subprocess, never `exec`: the heredoc validates on import and exits the
    interpreter, so importing it here would end the test session.
    """
    (tmp_path / "grants").mkdir(exist_ok=True)
    (tmp_path / _GRANT_FILE).write_text(
        textwrap.dedent(anchor).lstrip(), encoding="utf-8"
    )
    script = tmp_path / "validator.py"
    script.write_text(_validator_source(), encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin"}
    if enforce is not None:
        env["GRANT_VALIDATION_ENFORCE"] = enforce
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


_MALFORMED = """
entries:
  - grant_id: not-a-grant-id
    runtime_lane: prod
    image_digest: sha256:deadbeef
    promotion_batch_id: batch-1
    approved_by: ""
    created_at: "2099-01-02T00:00:00Z"
    expires_at: "2099-01-01T00:00:00Z"
    reason: malformed fixture
"""

_EXPIRED = f"""
entries:
  - grant_id: grant-11111111-2222-3333-4444-555555555555
    runtime_lane: prod
    image_digest: {_VALID_DIGEST}
    promotion_batch_id: batch-20260101-001
    approved_by: platform-lead-github-login
    created_at: "2026-01-01T00:00:00Z"
    expires_at: "2026-01-02T00:00:00Z"
    reason: an expired grant that must be pruned
"""

_AT_REST = "entries: []\n"


class TestTheTriggerCanReachEveryGatedPullRequest:
    """The property that makes this context safe to require."""

    def test_the_pull_request_trigger_has_no_path_filter(self) -> None:
        why = (
            "A path-filtered workflow creates NO check run on a pull request "
            "that misses the filter, so a branch protection requiring "
            f"{_REQUIRED_CONTEXT!r} would report it as permanently 'expected' "
            "and block that pull request forever. The scope step inside the "
            "job makes this decision instead, after the check run exists."
        )
        pull_request = _triggers()["pull_request"]
        assert "paths" not in pull_request, why
        assert "paths-ignore" not in pull_request, why

    def test_the_pull_request_trigger_covers_both_gated_branches(self) -> None:
        branches = set(_triggers()["pull_request"].get("branches", []))
        assert {"dev", "main"} <= branches, (
            f"The trigger must cover every branch that may require "
            f"{_REQUIRED_CONTEXT!r}, got: {sorted(branches)}"
        )

    def test_the_job_is_not_conditioned_away(self) -> None:
        """A job-level `if:` would skip the job and report no conclusion."""
        assert "if" not in _job(), (
            "A skipped job reports no conclusion to branch protection. The "
            "enforce/advisory decision belongs inside the job, not on it."
        )

    def test_the_scope_step_exists_and_is_named(self) -> None:
        step_ids = [step.get("id") for step in _job()["steps"]]
        assert "scope" in step_ids, f"No `scope` step; got ids {step_ids}"


class TestTheRequiredContextStringIsWhatTheJobActuallyReports:
    """OMN-18950's own defect class: a context named after nothing."""

    def test_the_job_name_is_the_required_context(self) -> None:
        assert _job()["name"] == _REQUIRED_CONTEXT

    def test_the_workflow_is_not_reusable_so_there_is_no_caller_prefix(self) -> None:
        assert "workflow_call" not in _triggers(), (
            "A reusable workflow reports as `<caller-job> / <job name>`. This "
            "one is not reusable, which is why the required context carries no "
            "prefix. If that changes, the required context string changes too."
        )

    def test_the_header_declares_exactly_the_context_the_job_reports(self) -> None:
        """The line a human copies into branch protection must be the real one.

        Scoped to the declaration line, not the whole file: the header also
        recounts the wrong name it used to carry, and that history is worth
        keeping.
        """
        lines = _WORKFLOW.read_text(encoding="utf-8").splitlines()
        declarations = [
            ln for ln in lines if ln.startswith("# Required-status-check name:")
        ]
        assert len(declarations) == 1, (
            "Expected exactly one required-status-check declaration, got: "
            f"{declarations}"
        )
        declared = re.findall(r"`([^`]+)`", declarations[0])
        assert declared == [_REQUIRED_CONTEXT], (
            f"The header tells a human to require {declared}, but the job "
            f"reports {_REQUIRED_CONTEXT!r}. A context named after something "
            "that never reports blocks the branch permanently."
        )


class TestTheFailingPathIsIntact:
    """Under enforcement the validator is exactly as red as it ever was."""

    def test_a_malformed_grant_is_red(self, tmp_path: Path) -> None:
        result = _run_validator(tmp_path, _MALFORMED, enforce="1")
        assert result.returncode == 1, (
            f"Expected exit 1, got {result.returncode}: {result.stdout}"
        )
        assert "FAIL:" in result.stdout
        assert "grant_id must match" in result.stdout

    def test_an_expired_grant_is_red(self, tmp_path: Path) -> None:
        result = _run_validator(tmp_path, _EXPIRED, enforce="1")
        assert result.returncode == 1, (
            f"Expected exit 1, got {result.returncode}: {result.stdout}"
        )
        assert "EXPIRED" in result.stdout

    def test_an_unparseable_anchor_is_red(self, tmp_path: Path) -> None:
        result = _run_validator(tmp_path, "entries: [\n", enforce="1")
        assert result.returncode == 1, (
            f"Expected exit 1, got {result.returncode}: {result.stdout}"
        )

    def test_a_non_mapping_anchor_is_red(self, tmp_path: Path) -> None:
        result = _run_validator(tmp_path, "- just\n- a\n- list\n", enforce="1")
        assert result.returncode == 1, (
            f"Expected exit 1, got {result.returncode}: {result.stdout}"
        )

    def test_an_unexpected_top_level_key_is_red(self, tmp_path: Path) -> None:
        result = _run_validator(tmp_path, "entries: []\nsurprise: true\n", enforce="1")
        assert result.returncode == 1, (
            f"Expected exit 1, got {result.returncode}: {result.stdout}"
        )

    def test_enforcement_is_the_default_when_the_variable_is_absent(
        self, tmp_path: Path
    ) -> None:
        """Fails closed: an unset flag validates for real, it does not wave through."""
        result = _run_validator(tmp_path, _MALFORMED, enforce=None)
        assert result.returncode == 1, (
            "With GRANT_VALIDATION_ENFORCE unset the validator must enforce. A "
            f"default of advisory would make every path a silent pass. Got "
            f"exit {result.returncode}: {result.stdout}"
        )

    @pytest.mark.parametrize("enforce", ["1", "0", None])
    def test_a_valid_at_rest_anchor_passes_in_every_mode(
        self, tmp_path: Path, enforce: str | None
    ) -> None:
        """Positive control: the red tests above are not red for a silly reason."""
        result = _run_validator(tmp_path, _AT_REST, enforce=enforce)
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}: {result.stdout}"
        )
        assert "PASS:" in result.stdout


class TestTheAdvisoryArmReportsRatherThanHides:
    """Green, but never silent: the finding is still computed and still printed."""

    def test_a_malformed_grant_is_green_when_the_anchor_is_out_of_scope(
        self, tmp_path: Path
    ) -> None:
        result = _run_validator(tmp_path, _MALFORMED, enforce="0")
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}: {result.stdout}"
        )

    def test_the_finding_is_still_emitted_as_an_annotation(
        self, tmp_path: Path
    ) -> None:
        result = _run_validator(tmp_path, _MALFORMED, enforce="0")
        assert "::warning" in result.stdout, (
            "An advisory pass that prints nothing is a silent gate. The "
            f"finding must reach the run summary. Got: {result.stdout}"
        )
        assert "grant_id must match" in result.stdout, (
            "The advisory arm must print the SAME findings the enforcing arm "
            f"would, not a generic notice. Got: {result.stdout}"
        )

    def test_the_advisory_notice_names_why_it_did_not_block(
        self, tmp_path: Path
    ) -> None:
        result = _run_validator(tmp_path, _MALFORMED, enforce="0")
        assert "ADVISORY:" in result.stdout
        assert _GRANT_FILE in result.stdout


class TestThisContextDoesNotCheckSelfApproval:
    """Negative control for a property this context does NOT carry.

    Self-approval is refused at dispatch time by the consumer in
    `omninode_infra`, which compares `approved_by` to `github.actor`, and by
    CODEOWNERS review on this path. On `main` it is additionally refused at
    authoring time by OMN-18945 (OCC#10632), which adds its own module and its
    own `ci.yml` job under the already-required `CI Summary`. None of those is
    this workflow's inline schema validator, which is what the required context
    `validate-prod-promotion-grants` reports on.

    So this test does not pin a defect and does not go stale when OCC#10632
    lands. It pins the BOUNDARY of one required context, which is precisely
    what OMN-18950 says goes unread: a green check here is evidence about
    schema shape and expiry, and about nothing else.
    """

    def test_a_self_approved_grant_passes_the_schema_validator(
        self, tmp_path: Path
    ) -> None:
        self_approved = f"""
        entries:
          - grant_id: grant-11111111-2222-3333-4444-555555555555
            runtime_lane: prod
            image_digest: {_VALID_DIGEST}
            promotion_batch_id: batch-20260921-001
            approved_by: the-same-identity-that-opened-this-pull-request
            created_at: "2026-09-21T00:00:00Z"
            expires_at: "2099-01-01T00:00:00Z"
            reason: negative control for the absent anti-self-approval check
        """
        result = _run_validator(tmp_path, self_approved, enforce="1")
        assert result.returncode == 0, (
            "This workflow's inline schema validator is not the anti-self-"
            "approval surface; OMN-18945's separate job is. If this validator "
            "now refuses self-approval too, the boundary this test describes "
            f"has moved and the docstring above is stale. Got: {result.stdout}"
        )

    def test_the_schema_has_no_requester_field_to_compare_against(self) -> None:
        source = _validator_source()
        assert "requested_by" not in source.replace("!= requested_by", ""), (
            "A requester identity appeared in this validator. The boundary "
            "described above has moved; re-read the docstring."
        )
