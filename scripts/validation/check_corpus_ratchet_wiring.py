# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15411: assert the contract corpus ratchets are wired into a REQUIRED context.

Why this exists
---------------
``tests/unit/scripts/test_lint_contract_check_values_corpus_baseline.py`` holds
the shrink-only Rule A/B/C/D/E ratchets, and is the only surface that scans
*every* ``contracts/*.yaml``. Its own docstring used to claim the tests "run
under pytest over EVERY contract, on every PR". They did not:

* ``ci.yml``'s ``test`` job is skipped on every PR targeting ``dev``
  (``if: ... (github.event_name != 'pull_request' || github.base_ref != 'dev')``),
  and ``ci-summary``'s generic ``contains(needs.*.result, 'failure')`` rollup
  passes on a *skipped* need.
* The only other runner was ``tests+coverage (shadow)`` in
  ``product-readiness-shadow.yml``, which that file states is deliberately kept
  out of ``required_status_checks``.
* OCC ``dev``'s required contexts are exactly
  ``["CI Summary", "required-check-skip-guard / check-skip-vectors"]``.

The repair is the ``contract-corpus-ratchets`` job plus a strict success-only
check in ``ci-summary``. That repair can be silently undone by deleting both
halves in one edit, and the ratchet tests themselves cannot catch that -- they
would simply stop running.

This validator is that anti-removal anchor. It is wired as a pre-commit hook
scoped to ``.github/workflows/ci.yml``, so ANY edit to that file re-asserts the
wiring, both locally and in the required CI ``Pre-commit`` context (which on a
dev PR runs ``pre-commit run --files <changed>``). It also runs as the last step
of the ratchet job itself, so the wiring is proven on every PR, not just on PRs
that touch ``ci.yml``.

Exit codes: ``0`` intact, ``1`` wiring broken. Never exits 0 on a missing or
unparseable ``ci.yml`` -- an absent gate must be indistinguishable from a
failing one (the OMN-14666/14668 lesson).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

_JOB_ID = "contract-corpus-ratchets"
_SUMMARY_JOB_ID = "ci-summary"
_CORPUS_TEST_MODULE = (
    "tests/unit/scripts/test_lint_contract_check_values_corpus_baseline.py"
)
_SELF_SCRIPT_NAME = "check_corpus_ratchet_wiring.py"
_TICKET = "OMN-15411"


class CiWorkflowUnreadableError(Exception):
    """ci.yml is missing or does not parse into the expected shape.

    A distinct type so callers cannot conflate "gate says the wiring is broken"
    with "gate could not read the file" -- both must be non-zero, never a
    silent pass.
    """


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_ci_yaml(path: Path) -> dict[str, Any]:
    """Parse ci.yml, hard-failing (never exit 0) on missing/unparseable input."""
    if not path.is_file():
        msg = f"{path} does not exist"
        raise CiWorkflowUnreadableError(msg)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"{path} did not parse to a mapping"
        raise CiWorkflowUnreadableError(msg)
    jobs = data.get("jobs")
    if not isinstance(jobs, dict):
        msg = f"{path} has no `jobs:` mapping"
        raise CiWorkflowUnreadableError(msg)
    return data


def _collect_run_script(job: dict[str, Any]) -> str:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return ""
    return "\n".join(
        step["run"]
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    )


def _check_ratchet_job(job: dict[str, Any]) -> list[str]:
    """The job must be unconditional AND actually run the ratchets."""
    failures: list[str] = []

    # Unconditional: a `needs:` or `if:` can make the job skip, and a skipped
    # job is a silent hole. Both halves are enforced -- unconditional here,
    # strict success-only in ci-summary.
    if "needs" in job:
        failures.append(
            f"job `{_JOB_ID}` declares `needs:` ({job['needs']!r}). It must be "
            "unconditional -- a needs-chain lets an upstream skip silently skip "
            "the ratchet (the omnimarket#1783 vacuous-green window)."
        )
    if "if" in job:
        failures.append(
            f"job `{_JOB_ID}` declares `if:` ({job['if']!r}). It must be "
            "unconditional -- ci.yml's `test` job is skipped on dev PRs by "
            "exactly such a condition, which is the defect this job repairs."
        )

    run_blob = _collect_run_script(job)
    if _CORPUS_TEST_MODULE not in run_blob:
        failures.append(
            f"job `{_JOB_ID}` does not run `{_CORPUS_TEST_MODULE}`. The job name "
            "is not the gate; executing the corpus ratchets is."
        )
    if _SELF_SCRIPT_NAME not in run_blob:
        failures.append(
            f"job `{_JOB_ID}` does not run `scripts/{_SELF_SCRIPT_NAME}`. The job "
            "must re-assert its own wiring on every PR, not only on PRs that "
            "edit ci.yml."
        )
    return failures


def _check_summary_job(summary: dict[str, Any]) -> list[str]:
    """ci-summary must WAIT for the ratchets and assert success-only."""
    failures: list[str] = []

    needs = summary.get("needs")
    needs_list = [needs] if isinstance(needs, str) else list(needs or [])
    if _JOB_ID not in needs_list:
        failures.append(
            f"`{_SUMMARY_JOB_ID}` does not list `{_JOB_ID}` in `needs:`, so the "
            "required CI Summary context does not wait for the ratchets and can "
            "report green before/without them."
        )

    # The generic `contains(needs.*.result, 'failure')` rollup passes on a
    # SKIPPED need, so membership in `needs:` alone is not enforcement.
    strict_expr = f'needs.{_JOB_ID}.result }}}}" != "success"'
    if strict_expr not in _collect_run_script(summary):
        failures.append(
            f"`{_SUMMARY_JOB_ID}` has no strict success-only check for "
            f"`{_JOB_ID}`. Expected a line containing `{strict_expr}`. The "
            "generic rollup only tests for 'failure'/'cancelled', so a SKIPPED "
            "ratchet job would pass CI Summary -- exactly the hole this ticket "
            "closes."
        )
    return failures


def check_wiring(ci_yaml_path: Path) -> list[str]:
    """Return a list of wiring failures. Empty list means the wiring is intact."""
    jobs: dict[str, Any] = _load_ci_yaml(ci_yaml_path)["jobs"]

    job = jobs.get(_JOB_ID)
    if not isinstance(job, dict):
        return [
            f"job `{_JOB_ID}` is absent from {ci_yaml_path.name}. It is the only "
            "required-path surface that scans every contracts/*.yaml "
            f"({_TICKET}); removing it re-opens the gap where a new Rule "
            "A/B/C/D/E violation merges to dev with all required contexts green."
        ]

    failures = _check_ratchet_job(job)

    summary = jobs.get(_SUMMARY_JOB_ID)
    if not isinstance(summary, dict):
        failures.append(
            f"job `{_SUMMARY_JOB_ID}` is absent -- `CI Summary` is the required "
            "context on OCC dev; without it nothing is enforced."
        )
        return failures

    return failures + _check_summary_job(summary)


_USAGE = f"""\
usage: check_corpus_ratchet_wiring.py [-h] [CI_YAML ...]

{_TICKET}: assert the Rule A/B/C/D/E contract corpus ratchets are wired into a
REQUIRED CI context. Checks that ci.yml declares the `{_JOB_ID}` job, that the
job is unconditional (no `needs:`, no `if:`), that it runs
`{_CORPUS_TEST_MODULE}` and re-runs this validator, and that `{_SUMMARY_JOB_ID}`
both lists it in `needs:` AND carries a strict success-only check for it (the
generic `contains(needs.*.result, 'failure')` rollup passes on a SKIPPED need).

positional arguments:
  CI_YAML     workflow file(s) to check; defaults to .github/workflows/ci.yml

options:
  -h, --help  show this help message and exit

exit codes: 0 wiring intact, 1 wiring broken / file missing / file unparseable.
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # The Pre-commit job's changed-validator step executes every modified
    # validator with `--help` before merging, so a gate that cannot even print
    # usage is rejected as broken. Without this branch `--help` was treated as a
    # path and the gate failed itself.
    if "-h" in args or "--help" in args:
        print(_USAGE, end="")
        return 0
    # pre-commit passes the matched filenames (its `files:` regex already
    # restricts them to ci.yml). Every supplied path is checked AS GIVEN -- an
    # earlier draft filtered on `endswith("ci.yml")` and fell back to the
    # canonical path when nothing matched, which made a missing/renamed target
    # exit 0 while printing PASSED for a file the caller never named. That is
    # the exit-0-on-missing shape the fail-loud pre-commit meta-gate forbids
    # (OMN-14666/14668). Only a bare invocation resolves the canonical path.
    paths = [Path(a) for a in args] or [
        _repo_root() / ".github" / "workflows" / "ci.yml"
    ]

    rc = 0
    for path in paths:
        try:
            failures = check_wiring(path)
        except (CiWorkflowUnreadableError, yaml.YAMLError) as exc:
            print(f"CORPUS-RATCHET WIRING GATE FAILED ({path}): {exc}")
            rc = 1
            continue
        if failures:
            rc = 1
            print(f"CORPUS-RATCHET WIRING GATE FAILED ({path}) [{_TICKET}]:")
            for failure in failures:
                print(f"  - {failure}")
        else:
            print(f"CORPUS-RATCHET WIRING GATE PASSED ({path})")
    return rc


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
