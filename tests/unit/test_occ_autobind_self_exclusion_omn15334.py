# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""OCC-autobind self-companion exclusion — regression + seam coverage (OMN-15334).

Three layers, all driven by REAL metadata from the two self-recursive cascades of
2026-07-28 (``tests/fixtures/occ_autobind_cascade_omn15334.json``: OMN-15332's
``OCC#5312 -> #5354`` and OMN-15338's ``#5355 -> #5362``):

1. **RED proof.** The legacy trigger expression — the literal string that shipped
   on ``onex_change_control`` ``dev`` at ``c05b7aa9a`` — is re-evaluated over the
   fixture and MUST fire on every cascade link. Without this the GREEN assertions
   below would be unfalsifiable: a fixture that the old rule already suppressed
   would prove nothing.
2. **GREEN proof.** The new predicate MUST suppress every cascade link and MUST
   still publish for every real product PR.
3. **Seam coverage (OMN-14208).** The committed workflow YAML and the predicate
   module are driven together across the actual boundary: the step's ``env:``
   keys are matched field-by-field against the module's ``REQUIRED_ENV_VARS``,
   the workflow's own ``run:`` command is executed as a subprocess with an env
   built from a real cascade PR, and the ``GITHUB_OUTPUT`` key it writes is
   matched against the key the publish job's ``if:`` reads. Two independent unit
   suites either side of this boundary would not have caught a rename.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from onex_change_control.scripts.check_occ_autobind_trigger import (
    REQUIRED_ENV_VARS,
    SHOULD_PUBLISH_OUTPUT_NAME,
    EnumOccAutobindTriggerVerdict,
    decide_occ_autobind_trigger,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "tests/fixtures/occ_autobind_cascade_omn15334.json"
PREDICATE_PATH = (
    REPO_ROOT / "src/onex_change_control/scripts/check_occ_autobind_trigger.py"
)
# OMN-16260: both callers now live in the same consolidated guards.yml, each
# under its own renamed job id (self-companion-guard was a collision between
# the two original files' identical job id; the check-run NAME each produces
# is unaffected — see guards.yml's own header comment). CALLERS pairs each
# (path, job_id) so every parametrized seam test below still independently
# drives both guards, rather than assuming one shared job id.
CALLER_PATHS = (
    REPO_ROOT / ".github/workflows/guards.yml",
    REPO_ROOT / ".github/workflows/guards.yml",
)
CALLER_JOB_IDS = (
    "self-companion-guard-autobind",
    "self-companion-guard-companion-effect",
)
CALLERS = tuple(zip(CALLER_PATHS, CALLER_JOB_IDS, strict=True))
GUARD_STEP_ID = "decide"

# The exact `if:` expression both OCC callers shipped with on `dev` at
# c05b7aa9a630464181c09b34a38bfba4eb6b798d — the state that produced the
# cascades. Recorded verbatim so the RED reconstruction below is auditable
# against git history rather than against a paraphrase.
LEGACY_TRIGGER_IF_EXPRESSION = (
    "github.event_name == 'pull_request' && "
    "github.actor != 'dependabot[bot]' && "
    "github.actor != 'renovate[bot]' && "
    "(contains(github.event.pull_request.title, 'OMN-') || "
    "contains(github.event.pull_request.head.ref, 'OMN-'))"
)

# Maps a GitHub Actions expression, as written in the caller's `env:` block, to
# the fixture field that supplies its live value. A workflow env entry whose
# expression is absent here fails `test_seam_env_expressions_are_all_mapped`,
# so the seam cannot silently gain an unmodelled input.
EXPRESSION_TO_FIXTURE_FIELD: dict[str, str] = {
    "github.event_name": "_event_name",
    "github.actor": "actor",
    "github.event.pull_request.title": "title",
    "github.event.pull_request.head.ref": "head_ref",
    "toJSON(github.event.pull_request.labels.*.name)": "_labels_json",
}

SELF_COMPANION_ROLES = frozenset(
    {"cascade_link", "cascade_root_companion", "hand_authored_companion"}
)


def _load_fixture() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _cases() -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", _load_fixture()["cases"])


def _case_id(case: dict[str, Any]) -> str:
    return f"{case['role']}-{case['repo'].split('/')[-1]}#{case['pr_number']}"


ALL_CASES = _cases()
CASE_IDS = [_case_id(case) for case in ALL_CASES]


def _load_workflow(path: Path) -> dict[str, Any]:
    loaded = cast("dict[Any, Any]", yaml.safe_load(path.read_text(encoding="utf-8")))
    if "on" not in loaded and True in loaded:
        loaded["on"] = loaded[True]
    return cast("dict[str, Any]", loaded)


def _publish_job(
    workflow: dict[str, Any], guard_job_id: str
) -> tuple[str, dict[str, Any]]:
    """The single job that `needs:` this specific guard and calls a reusable.

    OMN-16260: guards.yml now hosts several `uses:` jobs side by side (both
    OCC-autobind publishers plus pr-title/required-check-skip-guard), so
    "the one uses: job in the file" no longer identifies a single job —
    filtering by dependency on THIS guard's own job id is what makes the
    seam test still ask "does this specific guard's own publish job depend
    on it", not "is there exactly one uses: job anywhere in the file".
    """
    jobs = workflow["jobs"]

    def _needs(job: dict[str, Any]) -> list[str]:
        needs = job.get("needs")
        if needs is None:
            return []
        return [needs] if isinstance(needs, str) else list(needs)

    calling = {
        name: job
        for name, job in jobs.items()
        if "uses" in job and guard_job_id in _needs(job)
    }
    assert len(calling) == 1, (
        f"expected exactly one reusable-calling job depending on {guard_job_id!r}, "
        f"got {sorted(calling)}"
    )
    name, job = next(iter(calling.items()))
    return name, cast("dict[str, Any]", job)


def _guard_step(workflow: dict[str, Any], job_id: str) -> dict[str, Any]:
    steps = workflow["jobs"][job_id]["steps"]
    matches = [step for step in steps if step.get("id") == GUARD_STEP_ID]
    assert len(matches) == 1, f"expected exactly one step id={GUARD_STEP_ID!r}"
    return cast("dict[str, Any]", matches[0])


def _strip_expression(raw: str) -> str:
    """``${{ github.actor }}`` -> ``github.actor``."""
    match = re.fullmatch(r"\$\{\{\s*(?P<expr>.+?)\s*\}\}", raw.strip(), flags=re.DOTALL)
    assert match is not None, (
        f"env value {raw!r} is not a single GitHub Actions expression"
    )
    return match.group("expr")


# ---------------------------------------------------------------------------
# 1. RED — the legacy expression fires on every cascade link
# ---------------------------------------------------------------------------


def _legacy_trigger_fires(
    case: dict[str, Any], *, event_name: str = "pull_request"
) -> bool:
    """Faithful evaluation of :data:`LEGACY_TRIGGER_IF_EXPRESSION` for one PR.

    Every operand of the recorded expression, in the same order and with the
    same semantics GitHub applies (``contains`` on a string is a substring test;
    ``&&`` binds tighter than ``||``; the parenthesised ``||`` is the last term).
    Each of the four operands below is one term of the recorded string.
    """
    return (
        event_name == "pull_request"
        and case["actor"] != "dependabot[bot]"
        and case["actor"] != "renovate[bot]"
        and ("OMN-" in case["title"] or "OMN-" in case["head_ref"])
    )


@pytest.mark.unit
@pytest.mark.parametrize("case", ALL_CASES, ids=CASE_IDS)
def test_red_legacy_trigger_fired_on_every_fixture_pr(case: dict[str, Any]) -> None:
    """RED: the shipped expression published for EVERY PR in the fixture.

    Including all 46 recursive cascade links. This is the defect, reproduced
    from live metadata; it is what makes the GREEN assertions falsifiable.
    """
    assert _legacy_trigger_fires(case) is True, (
        f"fixture case {_case_id(case)} does not reproduce the legacy behaviour; "
        f"the RED baseline is wrong, not the fix"
    )


@pytest.mark.unit
def test_red_recorded_legacy_expression_matches_the_fixture_provenance() -> None:
    """The RED baseline is the string that shipped, not a paraphrase of it.

    The fixture records the expression together with the commit it was extracted
    from (``git show <sha>:.github/workflows/call-occ-autobind.yml``), so the
    baseline this suite reconstructs stays auditable against git history.
    """
    provenance = _load_fixture()["provenance"]
    assert " ".join(provenance["legacy_trigger_if_expression"].split()) == " ".join(
        LEGACY_TRIGGER_IF_EXPRESSION.split()
    )
    assert re.fullmatch(r"[0-9a-f]{40}", provenance["legacy_trigger_source_commit"])


@pytest.mark.unit
def test_red_baseline_covers_a_real_cascade_of_meaningful_depth() -> None:
    """The fixture is a real cascade, not a hand-built toy."""
    links = [c for c in ALL_CASES if c["role"] == "cascade_link"]
    assert len(links) >= 40, f"expected the real cascade depth, got {len(links)} links"

    # Every link names its parent OCC PR, and that parent is itself in the chain
    # (or is the chain root) — i.e. this is genuinely self-recursive, not a fan-out.
    parent_re = re.compile(r"OmniNode-ai/onex_change_control#(\d+)")
    numbers = {c["pr_number"] for c in ALL_CASES}
    for link in links:
        match = parent_re.search(link["title"])
        assert match is not None, f"{_case_id(link)} does not name an OCC parent PR"
        assert int(match.group(1)) in numbers, (
            f"{_case_id(link)} names parent #{match.group(1)} "
            f"outside the captured chain"
        )


# ---------------------------------------------------------------------------
# 2. GREEN — the new predicate suppresses the cascade and keeps product PRs
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("case", ALL_CASES, ids=CASE_IDS)
def test_green_predicate_matches_expected_publish_decision(
    case: dict[str, Any],
) -> None:
    decision = decide_occ_autobind_trigger(
        event_name="pull_request",
        actor=case["actor"],
        pr_title=case["title"],
        head_ref=case["head_ref"],
        labels=case["labels"],
    )
    assert decision.should_publish is case["expect_publish"], (
        f"{_case_id(case)}: expected should_publish={case['expect_publish']}, got "
        f"{decision.should_publish} (verdict={decision.verdict.value}) "
        f"— {decision.reason}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "case",
    [c for c in ALL_CASES if c["role"] in SELF_COMPANION_ROLES],
    ids=[_case_id(c) for c in ALL_CASES if c["role"] in SELF_COMPANION_ROLES],
)
def test_green_suppressed_cases_are_reported_as_self_companions(
    case: dict[str, Any],
) -> None:
    """A skip must be attributed to a self-companion signal, not to an accident.

    Suppressing a cascade link because it happened to look like a bot PR would
    pass the publish assertion while leaving the real defect open.
    """
    decision = decide_occ_autobind_trigger(
        event_name="pull_request",
        actor=case["actor"],
        pr_title=case["title"],
        head_ref=case["head_ref"],
        labels=case["labels"],
    )
    assert decision.is_self_companion, (
        f"{_case_id(case)} was skipped as {decision.verdict.value}, which is not a "
        f"self-companion signal"
    )
    assert decision.reason.strip(), "every skip must state its reason"


@pytest.mark.unit
def test_green_head_ref_signal_alone_would_not_close_the_loop() -> None:
    """The two hand-authored companions prove the title signal is load-bearing.

    ``#5325`` (``codex-a/...``) and ``#5342`` (``jonah/...``) are companion
    evidence on human head refs, and the emitter companioned both anyway
    (``#5327``, ``#5344``). A head-ref-only guard would leave that door open.
    """
    hand_authored = [c for c in ALL_CASES if c["role"] == "hand_authored_companion"]
    assert hand_authored, "fixture lost its hand-authored companion cases"

    for case in hand_authored:
        assert not case["head_ref"].startswith("auto/"), (
            f"{_case_id(case)} is no longer a human-head-ref case"
        )
        decision = decide_occ_autobind_trigger(
            event_name="pull_request",
            actor=case["actor"],
            pr_title=case["title"],
            head_ref=case["head_ref"],
            labels=case["labels"],
        )
        assert (
            decision.verdict
            is EnumOccAutobindTriggerVerdict.SKIP_EVIDENCE_COMPANION_TITLE
        ), (
            f"{_case_id(case)} must be caught by the title signal, "
            f"got {decision.verdict.value}"
        )


@pytest.mark.unit
def test_green_observation_store_titles_do_not_seed_a_cascade() -> None:
    """OMN-15323's observation-store emissions are companion evidence too.

    ``render_occ_observation_pr_title`` emits ``evidence(<TICKET>): OCC
    observation append <path>``; before this guard every emission would seed a
    cascade, which is why the store re-arm was blocked on this ticket.
    """
    decision = decide_occ_autobind_trigger(
        event_name="pull_request",
        actor="jonahgabriel",
        pr_title=(
            "evidence(OMN-14888): OCC observation append "
            "evidence/occ/observations/x.yaml"
        ),
        head_ref="auto/occ-observation-append",
        labels=[],
    )
    assert not decision.should_publish
    assert (
        decision.verdict is EnumOccAutobindTriggerVerdict.SKIP_EVIDENCE_COMPANION_TITLE
    )


@pytest.mark.unit
def test_green_dependency_bots_and_ticketless_prs_keep_their_own_reasons() -> None:
    """The pre-existing exemptions survive the move into the module."""
    bot = decide_occ_autobind_trigger(
        event_name="pull_request",
        actor="dependabot[bot]",
        pr_title="chore(deps): bump ruff",
        head_ref="dependabot/pip/ruff-0.14.0",
        labels=[],
    )
    assert bot.verdict is EnumOccAutobindTriggerVerdict.SKIP_DEPENDENCY_BOT
    assert not bot.is_self_companion

    ticketless = decide_occ_autobind_trigger(
        event_name="pull_request",
        actor="jonahgabriel",
        pr_title="chore: tidy up",
        head_ref="jonah/tidy",
        labels=[],
    )
    assert ticketless.verdict is EnumOccAutobindTriggerVerdict.SKIP_NO_TICKET_REFERENCE


@pytest.mark.unit
def test_green_machine_minted_label_is_an_independent_belt() -> None:
    """A machine companion whose branch and title were both renamed still skips."""
    decision = decide_occ_autobind_trigger(
        event_name="pull_request",
        actor="jonahgabriel",
        pr_title="chore(OMN-15332): renamed companion shape",
        head_ref="some/other-branch-OMN-15332",
        labels=["occ:machine-minted"],
    )
    assert decision.verdict is EnumOccAutobindTriggerVerdict.SKIP_MACHINE_MINTED_LABEL


# ---------------------------------------------------------------------------
# 3. Seam coverage across the workflow <-> predicate boundary (OMN-14208)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(("path", "job_id"), CALLERS, ids=CALLER_JOB_IDS)
def test_seam_publish_job_is_gated_on_the_guard_output(path: Path, job_id: str) -> None:
    workflow = _load_workflow(path)

    assert job_id in workflow["jobs"], f"{path.name} is missing guard job {job_id!r}"

    job_name, publish_job = _publish_job(workflow, job_id)
    needs = publish_job["needs"]
    needs_list = [needs] if isinstance(needs, str) else list(needs)
    assert job_id in needs_list, f"{path.name}:{job_name} must depend on {job_id!r}"

    # The exact field the guard writes is the exact field the publish job reads.
    expected_if = f"needs.{job_id}.outputs.{SHOULD_PUBLISH_OUTPUT_NAME} == 'true'"
    assert publish_job["if"].strip() == expected_if, (
        f"{path.name}:{job_name} `if:` is {publish_job['if'].strip()!r}, "
        f"expected {expected_if!r}"
    )

    # ... and the job output is wired to the step that actually produces it.
    guard_outputs = workflow["jobs"][job_id]["outputs"]
    assert (
        guard_outputs[SHOULD_PUBLISH_OUTPUT_NAME].strip()
        == f"${{{{ steps.{GUARD_STEP_ID}.outputs.{SHOULD_PUBLISH_OUTPUT_NAME} }}}}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(("path", "job_id"), CALLERS, ids=CALLER_JOB_IDS)
def test_seam_env_block_matches_module_required_env_field_by_field(
    path: Path, job_id: str
) -> None:
    """The workflow's env keys and the module's required env vars are one set."""
    step_env = _guard_step(_load_workflow(path), job_id)["env"]
    assert set(step_env) == set(REQUIRED_ENV_VARS), (
        f"{job_id} guard env keys {sorted(step_env)} != module REQUIRED_ENV_VARS "
        f"{sorted(REQUIRED_ENV_VARS)}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(("path", "job_id"), CALLERS, ids=CALLER_JOB_IDS)
def test_seam_env_expressions_are_all_mapped(path: Path, job_id: str) -> None:
    """Every env value is a modelled GitHub expression, so the drive below is real."""
    step_env = _guard_step(_load_workflow(path), job_id)["env"]
    for key, raw in step_env.items():
        expression = _strip_expression(str(raw))
        assert expression in EXPRESSION_TO_FIXTURE_FIELD, (
            f"{job_id} env {key} reads unmodelled expression {expression!r}"
        )


@pytest.mark.parametrize(("path", "job_id"), CALLERS, ids=CALLER_JOB_IDS)
@pytest.mark.parametrize(
    "case",
    [
        next(c for c in ALL_CASES if c["role"] == "cascade_link"),
        next(c for c in ALL_CASES if c["role"] == "hand_authored_companion"),
        next(c for c in ALL_CASES if c["role"] == "occ_product_pr"),
    ],
    ids=["cascade_link", "hand_authored_companion", "occ_product_pr"],
)
@pytest.mark.integration
def test_seam_workflow_command_drives_the_predicate_end_to_end(
    path: Path, job_id: str, case: dict[str, Any], tmp_path: Path
) -> None:
    """Execute the workflow's OWN ``run:`` command over real cascade metadata.

    This is the cross-boundary regression test the seam rule requires: the
    command string, the env keys, the ``GITHUB_OUTPUT`` key and the value the
    ``if:`` compares against are all taken from the committed YAML, not restated
    here. A rename on either side fails this test.
    """
    workflow = _load_workflow(path)
    step = _guard_step(workflow, job_id)

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)  # reference_pythonpath_shadows_worktree_source
    fixture_values = {
        "_event_name": "pull_request",
        "_labels_json": json.dumps(case["labels"]),
        "actor": case["actor"],
        "title": case["title"],
        "head_ref": case["head_ref"],
    }
    for key, raw in step["env"].items():
        env[key] = str(
            fixture_values[EXPRESSION_TO_FIXTURE_FIELD[_strip_expression(str(raw))]]
        )

    github_output = tmp_path / "github_output.txt"
    github_output.touch()
    env["GITHUB_OUTPUT"] = str(github_output)

    command = str(step["run"]).strip()
    assert command.startswith("python "), f"unexpected guard command {command!r}"
    argv = [sys.executable, *command.split()[1:]]

    completed = subprocess.run(
        argv, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, (
        f"guard command exited {completed.returncode}\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
    )

    outputs = dict(
        line.split("=", 1)
        for line in github_output.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    expected = "true" if case["expect_publish"] else "false"
    assert outputs[SHOULD_PUBLISH_OUTPUT_NAME] == expected, (
        f"{_case_id(case)} via {path.name}: GITHUB_OUTPUT "
        f"{SHOULD_PUBLISH_OUTPUT_NAME}={outputs[SHOULD_PUBLISH_OUTPUT_NAME]!r}, "
        f"expected {expected!r}"
    )

    # The skip must be announced. A silent green no-op is the OMN-15022 signature.
    marker = "::notice::OCC autobind guard: " + (
        "PUBLISH" if case["expect_publish"] else "SKIP"
    )
    assert marker in completed.stdout, (
        f"guard did not announce its decision; stdout was:\n{completed.stdout}"
    )


@pytest.mark.unit
def test_seam_guard_runs_the_committed_predicate_file() -> None:
    """The command, the sparse-checkout path and the module file agree."""
    relative = PREDICATE_PATH.relative_to(REPO_ROOT).as_posix()
    for path, job_id in CALLERS:
        workflow = _load_workflow(path)
        step = _guard_step(workflow, job_id)
        assert str(step["run"]).strip() == f"python {relative}"

        checkout = next(
            s
            for s in workflow["jobs"][job_id]["steps"]
            if "actions/checkout" in str(s.get("uses", ""))
        )
        assert str(checkout["with"]["sparse-checkout"]).strip() == relative, (
            f"{job_id} sparse-checkout does not fetch the file the guard runs"
        )


@pytest.mark.unit
def test_seam_predicate_is_stdlib_only_and_package_free() -> None:
    """The guard runs on a bare interpreter, so it must import nothing local.

    ``import onex_change_control`` pulls pydantic + omnibase_core (~3.4s). A
    loop-breaker that inherits that surface can fail for reasons unrelated to
    the loop, and the workflow installs no dependencies before running it.
    """
    source = PREDICATE_PATH.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in source.splitlines()
        if re.match(r"\s*(from|import)\s+onex_change_control", line)
    ]
    assert not offenders, f"predicate imports its own package: {offenders}"

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            f"import runpy; runpy.run_path({str(PREDICATE_PATH)!r})",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # -I isolates from site-packages-adjacent path injection; a non-stdlib import
    # would raise ModuleNotFoundError, while the missing-env exit is code 1 with
    # the guard's own ::error:: text.
    assert "ModuleNotFoundError" not in completed.stderr, (
        f"predicate needs a non-stdlib dependency:\n{completed.stderr}"
    )


@pytest.mark.unit
def test_seam_both_callers_carry_identical_guard_jobs() -> None:
    """Neither caller may drift from the other; that asymmetry is how #2377 bit."""
    guards = [_load_workflow(path)["jobs"][job_id] for path, job_id in CALLERS]
    first, second = guards
    first_id, second_id = CALLER_JOB_IDS

    assert first["outputs"] == second["outputs"]
    assert first["runs-on"] == second["runs-on"]
    assert (
        _guard_step({"jobs": {first_id: first}}, first_id)["env"]
        == _guard_step({"jobs": {second_id: second}}, second_id)["env"]
    )
    assert (
        _guard_step({"jobs": {first_id: first}}, first_id)["run"]
        == _guard_step({"jobs": {second_id: second}}, second_id)["run"]
    )


@pytest.mark.unit
def test_seam_no_depth_cap_was_smuggled_in_as_the_fix() -> None:
    """A depth cap still burns N runs; self-exclusion is the invariant.

    Guards against a future 'fix' that swaps the predicate for a counter.
    """
    source = PREDICATE_PATH.read_text(encoding="utf-8").lower()
    for token in ("max_depth", "depth_limit", "depth_cap"):
        assert token not in source, (
            f"predicate grew a {token!r}; a depth cap treats the symptom, not the loop"
        )
