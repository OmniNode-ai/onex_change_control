# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18945 — the `main`-side half of the prod-promotion self-approval control.

Two things are proved here, and they are different kinds of fact.

BEHAVIOUR (`TestTheRefusalBehaves`) runs the REAL validator over synthetic
grant files. Every refusal assertion is paired with a POSITIVE CONTROL: the
identical entry approved by a different identity must NOT be refused. Without
that pairing a validator that refuses everything would look exactly like a
working one, and "it refused" would be evidence of nothing.

PLACEMENT (`TestTheRefusalIsWiredOnThisBranch`) parses `.github/workflows/ci.yml`
and proves the refusal is reachable and blocking ON THIS BRANCH. That is the
whole point of the ticket: the OMN-17157 control already behaved correctly on
`dev`, and prod-promotion grants are not authored on `dev`. A refusal nothing
runs, or one held by a context branch protection does not require, is the
silent gate this replaces — so the placement facts are asserted as
mechanically as the behavioural ones, and each has its own positive control
against a vacuous read.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from onex_change_control.scripts.validate_prod_promotion_grant_self_approval import (
    SELF_GRANTED_REASON,
    UNREADABLE_GRANTS_REASON,
    evaluate,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: The job this ticket adds, and the rollup that must hold it. Both are
#: spelled once so a rename shows up as one edit in one place.
SELF_APPROVAL_JOB = "prod-promotion-grant-self-approval"
CI_SUMMARY_JOB = "ci-summary"
REQUESTER_FLAG = "--requester"

REQUESTER = "tripwire-requester"
OTHER_APPROVER = "tripwire-other-approver"
GRANT_ID = "grant-11111111-2222-4333-8444-555555555555"


def _entry(
    *,
    approved_by: Any = OTHER_APPROVER,
    grant_id: str | None = GRANT_ID,
) -> dict[str, Any]:
    """A schema-shaped image grant. Shape is the schema validator's job; this
    module only varies the two fields the self-approval check reads.
    """
    entry: dict[str, Any] = {
        "runtime_lane": "prod",
        "promotion_batch_id": "batch-omn18945-fixture",
        "approved_by": approved_by,
        "image_digest": "sha256:" + "a" * 64,
        "expires_at": "2099-01-01T00:00:00Z",
        "created_at": "2026-01-01T00:00:00Z",
        "reason": "synthetic fixture; authorizes nothing",
    }
    if grant_id is not None:
        entry["grant_id"] = grant_id
    return entry


def _write(path: Path, entries: list[Any]) -> Path:
    path.write_text(yaml.safe_dump({"entries": entries}), encoding="utf-8")
    return path


def _refusals(result: Any) -> list[str]:
    return [err for err in result.errors if SELF_GRANTED_REASON in err]


class TestTheRefusalBehaves:
    """The validator refuses a self-approval, and ONLY a self-approval."""

    def test_a_new_self_approved_entry_is_refused(self, tmp_path: Path) -> None:
        grants = _write(tmp_path / "grants.yaml", [_entry(approved_by=REQUESTER)])
        base = _write(tmp_path / "base.yaml", [])
        result = evaluate(grants, requester=REQUESTER, base_file=base)
        assert not result.passed
        assert _refusals(result), result.errors

    def test_positive_control_a_peer_approved_entry_is_not_refused(
        self, tmp_path: Path
    ) -> None:
        """The control that makes the test above mean something.

        An identical entry differing only in `approved_by` must pass. Without
        this, a validator that refused every grant would satisfy the refusal
        test and prove nothing.
        """
        grants = _write(tmp_path / "grants.yaml", [_entry(approved_by=OTHER_APPROVER)])
        base = _write(tmp_path / "base.yaml", [])
        result = evaluate(grants, requester=REQUESTER, base_file=base)
        assert result.passed, result.errors
        assert result.checked_entry_count == 1, (
            "the entry must have been CONSIDERED and cleared, not skipped — a "
            "pass over zero considered entries is not a positive control"
        )

    @pytest.mark.parametrize(
        "approved_by",
        ["Tripwire-Requester", "TRIPWIRE-REQUESTER", "tripwire-REQUESTER"],
    )
    def test_capitalisation_does_not_launder_a_self_approval(
        self, tmp_path: Path, approved_by: str
    ) -> None:
        """GitHub logins are case-insensitive.

        A comparison that was not casefolded would let a change of
        capitalisation past this check while resolving to the same account
        everywhere else in GitHub.
        """
        grants = _write(tmp_path / "grants.yaml", [_entry(approved_by=approved_by)])
        base = _write(tmp_path / "base.yaml", [])
        result = evaluate(grants, requester=REQUESTER, base_file=base)
        assert not result.passed
        assert _refusals(result), result.errors

    def test_a_preexisting_entry_is_not_refused(self, tmp_path: Path) -> None:
        """Diff-scoped: an unrelated PR opened by someone who shares a login
        with the approver of an UNTOUCHED entry must not be refused.
        """
        entry = _entry(approved_by=REQUESTER)
        grants = _write(tmp_path / "grants.yaml", [entry])
        base = _write(tmp_path / "base.yaml", [entry])
        result = evaluate(grants, requester=REQUESTER, base_file=base)
        assert result.passed, result.errors
        assert result.checked_entry_count == 0

    def test_an_added_entry_beside_a_preexisting_one_is_still_refused(
        self, tmp_path: Path
    ) -> None:
        """The control against a too-broad reading of diff scoping: carrying a
        pre-existing entry forward must not exempt a NEW self-approved one in
        the same file.
        """
        old = _entry(approved_by=OTHER_APPROVER, grant_id=GRANT_ID)
        added = _entry(
            approved_by=REQUESTER,
            grant_id="grant-99999999-8888-4777-8666-555555555555",
        )
        grants = _write(tmp_path / "grants.yaml", [old, added])
        base = _write(tmp_path / "base.yaml", [old])
        result = evaluate(grants, requester=REQUESTER, base_file=base)
        assert not result.passed
        assert len(_refusals(result)) == 1, result.errors
        assert "Entry[1]" in _refusals(result)[0]

    @pytest.mark.parametrize(
        "kind", ["absent", "missing", "unparseable", "wrong-shape"]
    )
    def test_an_unusable_base_fails_closed(self, tmp_path: Path, kind: str) -> None:
        """An unreadable base is not evidence that an entry is pre-existing.

        In all four cases the entry is treated as NEW and refused — the same
        entry that `test_a_preexisting_entry_is_not_refused` clears against a
        readable base, which is what makes this a failure-direction test and
        not a restatement.
        """
        grants = _write(tmp_path / "grants.yaml", [_entry(approved_by=REQUESTER)])
        if kind == "absent":
            base: Path | None = None
        elif kind == "missing":
            base = tmp_path / "does-not-exist.yaml"
        elif kind == "unparseable":
            base = tmp_path / "bad.yaml"
            base.write_text("entries: [unclosed\n", encoding="utf-8")
        else:
            base = tmp_path / "shape.yaml"
            base.write_text("entries: not-a-list\n", encoding="utf-8")
        result = evaluate(grants, requester=REQUESTER, base_file=base)
        assert not result.passed, kind
        assert _refusals(result), (kind, result.errors)

    def test_an_empty_anchor_passes(self, tmp_path: Path) -> None:
        """`entries: []` is the at-rest state and must not be a refusal."""
        grants = _write(tmp_path / "grants.yaml", [])
        result = evaluate(grants, requester=REQUESTER, base_file=None)
        assert result.passed, result.errors
        assert result.checked_entry_count == 0

    def test_a_non_string_approved_by_is_left_to_the_schema_validator(
        self, tmp_path: Path
    ) -> None:
        """One defect must not be reported by two checks.

        Field shape belongs to the schema validator in
        `.github/workflows/validate-prod-promotion-grants.yml` and its mirror;
        this check reports only self-approval.
        """
        grants = _write(tmp_path / "grants.yaml", [_entry(approved_by=None)])
        result = evaluate(grants, requester=REQUESTER, base_file=None)
        assert result.passed, result.errors
        assert not _refusals(result)

    @pytest.mark.parametrize("content", ["", "entries: not-a-list\n", ": :\n"])
    def test_an_unreadable_anchor_is_a_refusal_not_a_skip(
        self, tmp_path: Path, content: str
    ) -> None:
        """ "I could not parse the trust anchor" must never read as "the trust
        anchor is fine". The reason token is distinct from the self-approval
        one so the two are distinguishable in a log.
        """
        grants = tmp_path / "grants.yaml"
        grants.write_text(content, encoding="utf-8")
        result = evaluate(grants, requester=REQUESTER, base_file=None)
        assert not result.passed
        assert any(UNREADABLE_GRANTS_REASON in err for err in result.errors)

    def test_a_missing_anchor_is_a_refusal(self, tmp_path: Path) -> None:
        result = evaluate(tmp_path / "nope.yaml", requester=REQUESTER, base_file=None)
        assert not result.passed
        assert any(UNREADABLE_GRANTS_REASON in err for err in result.errors)


class TestTheCommandLineCannotRunWithoutARequester:
    """`--requester` is REQUIRED here, unlike on `dev`.

    A validator invoked without one runs the self-approval check over nothing
    and exits 0. On `dev` a separate behavioural tripwire fact exists purely
    to prove CI remembers to pass the flag. Requiring it makes that failure
    mode structurally unavailable instead of separately policed — so this
    test is the proof the flag cannot be forgotten, not a restatement of the
    wiring test below.
    """

    def test_omitting_the_requester_is_a_usage_error(self, tmp_path: Path) -> None:
        grants = _write(tmp_path / "grants.yaml", [_entry(approved_by=REQUESTER)])
        with pytest.raises(SystemExit) as excinfo:
            main(["--file", str(grants)])
        assert excinfo.value.code == 2

    def test_the_exit_codes_are_one_on_refusal_and_zero_on_a_clean_file(
        self, tmp_path: Path
    ) -> None:
        """Exercised through `main` so the CI step's own contract — a non-zero
        exit fails the job — is what is proved, not just the library call.
        """
        refused = _write(tmp_path / "bad.yaml", [_entry(approved_by=REQUESTER)])
        clean = _write(tmp_path / "ok.yaml", [_entry(approved_by=OTHER_APPROVER)])
        assert main(["--file", str(refused), "--requester", REQUESTER]) == 1
        assert main(["--file", str(clean), "--requester", REQUESTER]) == 0

    def test_the_installed_console_script_is_the_one_ci_invokes(
        self, tmp_path: Path
    ) -> None:
        """The CI step runs `uv run validate-prod-promotion-grant-self-approval`.

        A module that works only via `import` would leave that step red on the
        first grant PR, so the entry point registered in `pyproject.toml` is
        exercised as a real process. Invoked through `-m` rather than the
        console script itself so the test does not depend on the package
        having been reinstalled after the entry point was added.
        """
        refused = _write(tmp_path / "bad.yaml", [_entry(approved_by=REQUESTER)])
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "onex_change_control.scripts.validate_prod_promotion_grant_self_approval",
                "--file",
                str(refused),
                "--requester",
                REQUESTER,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 1, completed.stderr
        assert SELF_GRANTED_REASON in completed.stdout


class TestTheRefusalIsWiredOnThisBranch:
    """Placement facts, parsed out of `ci.yml` rather than grepped.

    A `--requester` inside a comment, or in some other job, is not the same
    fact as one in this job's own steps.
    """

    @pytest.fixture(scope="class")
    def workflow(self) -> dict[str, Any]:
        loaded = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
        assert isinstance(loaded, dict), f"{CI_WORKFLOW} did not parse as a mapping"
        return loaded

    @pytest.fixture(scope="class")
    def job(self, workflow: dict[str, Any]) -> dict[str, Any]:
        jobs = workflow.get("jobs")
        assert isinstance(jobs, dict), "ci.yml declares no jobs mapping"
        found = jobs.get(SELF_APPROVAL_JOB)
        assert isinstance(found, dict), (
            f"ci.yml declares no `{SELF_APPROVAL_JOB}` job, so the "
            "authoring-time self-approval refusal runs nowhere on the branch "
            "prod-promotion grants are authored on"
        )
        return found

    def test_positive_control_the_reader_found_a_real_job(
        self, job: dict[str, Any]
    ) -> None:
        """Guards every assertion below against a vacuous read.

        If the parse silently produced an empty job, the `if:`/`needs:`
        absence checks would all pass by accident.
        """
        steps = job.get("steps")
        assert isinstance(steps, list), "the job declares no steps list"
        assert steps, "the job declares no steps"
        assert any(isinstance(step, dict) and step.get("run") for step in steps), (
            "the job declares no step that runs anything"
        )

    def test_the_job_passes_a_requester(self, job: dict[str, Any]) -> None:
        run_text = "\n".join(
            str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict)
        )
        assert REQUESTER_FLAG in run_text, (
            f"the `{SELF_APPROVAL_JOB}` job never passes `{REQUESTER_FLAG}`, so "
            "it has nothing to compare approved_by against"
        )

    def test_the_job_is_unconditional(self, job: dict[str, Any]) -> None:
        """No `if:` and no `needs:`.

        `ci-summary` fails on `failure` and `cancelled` only, so a SKIPPED job
        is indistinguishable from a passing one. A gate that can be skipped
        into silence is the class this ticket removes.
        """
        assert "if" not in job, (
            f"`{SELF_APPROVAL_JOB}` carries an `if:`; a skip would read as a pass "
            "in the ci-summary rollup"
        )
        assert "needs" not in job, (
            f"`{SELF_APPROVAL_JOB}` carries a `needs:`; a skipped or failed "
            "dependency would skip the refusal and the rollup would not notice"
        )

    def test_the_required_rollup_holds_the_job(self, workflow: dict[str, Any]) -> None:
        """`CI Summary` is a required context on this branch and the
        standalone grants workflow is not. Being in this `needs:` list is what
        makes the refusal BLOCK rather than merely report.
        """
        summary = workflow["jobs"][CI_SUMMARY_JOB]
        needs = summary.get("needs")
        assert isinstance(needs, list), "ci-summary declares no needs list"
        assert SELF_APPROVAL_JOB in needs, (
            f"`{CI_SUMMARY_JOB}` does not declare `{SELF_APPROVAL_JOB}` in "
            "`needs:`, so the refusal runs but cannot block a grant PR — it "
            "would report red beside a green required context"
        )

    def test_positive_control_the_rollup_read_is_not_vacuous(
        self, workflow: dict[str, Any]
    ) -> None:
        """A `needs:` list that parsed as empty would make the test above
        fail loudly rather than pass quietly, but a list that parsed as some
        OTHER job's would not. Anchor on a job that has been in this rollup
        since long before this ticket.
        """
        needs = workflow["jobs"][CI_SUMMARY_JOB]["needs"]
        anchor = (
            "the ci-summary needs list does not contain the long-standing "
            f"jobs it should, so this reader is not looking at {CI_SUMMARY_JOB}"
        )
        assert "test" in needs, anchor
        assert "pre-commit" in needs, anchor

    def test_the_pull_request_trigger_covers_this_branch(
        self, workflow: dict[str, Any]
    ) -> None:
        """The refusal is only reachable if `ci.yml` runs on PRs targeting the
        branch grants are authored against.
        """
        triggers = workflow.get(True, workflow.get("on"))
        assert isinstance(triggers, dict), "ci.yml declares no trigger mapping"
        branches = triggers["pull_request"]["branches"]
        assert "main" in branches, (
            "ci.yml does not run on pull requests targeting main, so nothing "
            "here gates a grant PR"
        )
