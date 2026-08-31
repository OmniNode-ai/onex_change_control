# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15484: assert the Merge Hold Gate is wired into a REQUIRED context here.

Why this exists
---------------
OMN-15483 built a merge-hold gate so the merge sweep cannot land a PR while
adversarial verification is running against it. It shipped in ``omnimarket``
only — while **every incident in that ticket's own table happened in THIS
repository** (OCC#5588, OCC#5586, OCC#5530/#5531, OCC#5584) or in
``omnibase_infra`` (#2560). Neither repo had the gate.

OMN-15484 fans it out. This repository declares no vocabulary and no gate logic
of its own: ``ci.yml``'s ``merge-hold-gate`` job calls the shared reusable
workflow in omnimarket, which reads the one canonical vocabulary at run time.
Vendoring a copy is explicitly rejected by AC1 — that is how OMN-15483 round 1
found two divergent ``_DO_NOT_MERGE_RE`` definitions inside a single repo.

Why the JOB EXISTING is not the gate
------------------------------------
``CI Summary`` is one of only two required contexts on OCC ``dev`` (the other is
``required-check-skip-guard / check-skip-vectors``). It is a ``needs``-based
aggregator whose generic rollup is
``contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled')``
— which **passes on a SKIPPED need**. So membership in ``needs:`` alone is not
enforcement. Measured against the real evaluator on omnimarket#1973:

    hold job result | registered strictly | not registered
    --------------- | ------------------- | --------------
    failure         | FAILURE             | FAILURE
    skipped         | FAILURE             | SUCCESS   <- bypass
    cancelled       | FAILURE             | FAILURE
    absent          | PENDING             | SUCCESS   <- bypass

An unregistered job looks like enforcement and enforces nothing. The wiring is
therefore three things that must all hold, and any one of them can be deleted
in isolation without a single test noticing:

1. ``ci.yml`` declares the ``merge-hold-gate`` job, calling the shared reusable
   workflow (not a local re-implementation);
2. the job is unconditional — no ``needs:``, no ``if:`` (AC4). On omnimarket
   ``a56e3819`` a failing ``occ-preflight`` cascade-skipped Tests, typecheck,
   Contract Compliance and the whole E2E lane; a hold gate an unrelated upstream
   can skip is not a gate;
3. ``ci-summary`` both lists it in ``needs:`` AND carries an explicit
   success-only check for it.

This validator is the anti-removal anchor for all three, in the shape OMN-15411
already established in this repo
(``scripts/validation/check_corpus_ratchet_wiring.py``). It is wired as a
pre-commit hook scoped to ``.github/workflows/ci.yml``, so ANY edit to that file
re-asserts the wiring both locally and in the required CI ``Pre-commit`` context
(which on a dev PR runs ``pre-commit run --files <changed>``).

It also checks the AC5 seam that no single-repo test can: ``context_name`` is a
string this repo hands to a workflow in ANOTHER repo, where it is validated
against the canonical vocabulary. If it does not equal the context GitHub will
actually mint (``<job id> / evaluate``), the remote guard validates a name that
does not exist and the real one goes unchecked.

Exit codes: ``0`` intact, ``1`` wiring broken. Never exits 0 on a missing or
unparseable ``ci.yml`` — an absent gate must be indistinguishable from a failing
one (the OMN-14666/14668 lesson).
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

import yaml

_GATE_MODULE_RELPATH = Path("scripts") / "ci" / "ci_summary_gate.py"


def _extract_strict_gate_jobs(gate_module_path: Path) -> tuple[str, ...] | None:
    """Statically extract ``STRICT_GATE_JOBS`` from ci_summary_gate.py.

    AST-parsed (not imported) so this never executes the target file and so a
    test can point it at an isolated mutated copy. Returns ``None`` if the
    file is missing/unparseable or the tuple assignment cannot be found.
    """
    if not gate_module_path.is_file():
        return None
    try:
        tree = ast.parse(gate_module_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        name: str | None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value_node = node.value
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            name = names[0] if names else None
            value_node = node.value
        else:
            continue
        if name != "STRICT_GATE_JOBS" or value_node is None:
            continue
        try:
            value = ast.literal_eval(value_node)
        except (ValueError, TypeError):
            return None
        if isinstance(value, tuple) and all(isinstance(v, str) for v in value):
            return value
    return None

_JOB_ID = "merge-hold-gate"
_SUMMARY_JOB_ID = "ci-summary"
_INNER_JOB_ID = "evaluate"
_REUSABLE_PATH = "OmniNode-ai/omnimarket/.github/workflows/merge-hold-gate-reusable.yml"
_TICKET = "OMN-15484"
_SELF_SCRIPT_NAME = "check_merge_hold_gate_wiring.py"

# The composed check-run context a reusable call produces.
_EXPECTED_CONTEXT = f"{_JOB_ID} / {_INNER_JOB_ID}"

# `dev` (the vocabulary must be current fleet-wide) or an immutable 40-hex SHA.
# A personal/feature branch ref is refused outright: pinning a feature-branch
# head makes the gate's behaviour depend on a branch that can be force-pushed,
# rewritten or deleted after a squash merge.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_MUTABLE_REFS = frozenset({"dev", "main"})


class CiWorkflowUnreadableError(Exception):
    """ci.yml is missing or does not parse into the expected shape.

    A distinct type so callers cannot conflate "gate says the wiring is broken"
    with "gate could not read the file" — both must be non-zero, never a silent
    pass.
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


def _check_hold_job(job: dict[str, Any]) -> list[str]:
    """The job must be unconditional, shared-sourced, and honestly named."""
    failures: list[str] = []

    if "needs" in job:
        failures.append(
            f"job `{_JOB_ID}` declares `needs:` ({job['needs']!r}). It must be "
            "unconditional (AC4) — a needs-chain lets an unrelated upstream "
            "failure cascade-skip the hold gate, and a skipped gate is a silent "
            "hole. This is not hypothetical: omnimarket a56e3819 lost Tests, "
            "typecheck, Contract Compliance and the whole E2E lane that way."
        )
    if "if" in job:
        failures.append(
            f"job `{_JOB_ID}` declares `if:` ({job['if']!r}). It must be "
            "unconditional (AC4) — any condition is a skip path, and a skipped "
            "hold gate cannot refuse a held PR."
        )

    uses = job.get("uses")
    if not isinstance(uses, str) or not uses.startswith(f"{_REUSABLE_PATH}@"):
        failures.append(
            f"job `{_JOB_ID}` does not call the shared gate. Expected "
            f"`uses: {_REUSABLE_PATH}@<ref>`, found {uses!r}. A local "
            "re-implementation would be a SECOND hold vocabulary, which is the "
            "divergence OMN-15484 AC1 exists to prevent — the shared reusable "
            "workflow is what makes one definition serve every repo."
        )
    else:
        ref = uses.split("@", 1)[1]
        if ref not in _ALLOWED_MUTABLE_REFS and not _SHA_RE.match(ref):
            failures.append(
                f"job `{_JOB_ID}` pins the shared gate at ref {ref!r}. Allowed: "
                f"a 40-hex commit SHA, or one of {sorted(_ALLOWED_MUTABLE_REFS)}. "
                "A feature-branch ref makes this repository's merge gate depend "
                "on a branch that can be force-pushed, rewritten, or deleted "
                "after its squash merge — at which point the gate stops "
                "resolving and every PR here wedges."
            )
        vocabulary_ref = (job.get("with") or {}).get("vocabulary_ref")
        if vocabulary_ref != ref:
            failures.append(
                f"job `{_JOB_ID}` loads the gate workflow at {ref!r} but reads "
                f"the vocabulary at {vocabulary_ref!r}. They must be the same "
                "ref. `uses:` selects the workflow FILE and `vocabulary_ref` "
                "selects the SOURCE it reads; drifting them runs one vintage of "
                "the gate logic against another vintage of the tokens, which is "
                "a split-brain vocabulary — the exact failure class this fan-out "
                "exists to prevent, arrived at by a different road."
            )

    declared = (job.get("with") or {}).get("context_name")
    if declared != _EXPECTED_CONTEXT:
        failures.append(
            f"job `{_JOB_ID}` declares `context_name: {declared!r}` but the "
            f"context GitHub mints for this call is {_EXPECTED_CONTEXT!r} "
            f"(`<caller job id> / <inner job id>`). That string is validated "
            "against the canonical hold vocabulary in the OTHER repository "
            "(AC5); if it is wrong, the guard checks a name that does not exist "
            "and the real context goes unchecked."
        )

    return failures


def _check_summary_job(
    summary: dict[str, Any], gate_module_path: Path
) -> list[str]:
    """ci-summary must be the OMN-15768 no-needs poller AND assert the hold
    gate STRICTLY via ``scripts/ci/ci_summary_gate.py``'s ``STRICT_GATE_JOBS``.

    OMN-15768 replaced the needs-gated aggregator this anchor used to grep
    for with a no-needs poller that checks membership in a Python tuple at
    runtime. A `needs:` on ci-summary is now a REGRESSION to the needs-graph-
    omission bug class (OCC#6346), not a wiring requirement.
    """
    failures: list[str] = []

    if "needs" in summary:
        failures.append(
            f"`{_SUMMARY_JOB_ID}` declares `needs:` ({summary['needs']!r}). "
            "OMN-15768 replaced the needs-gated aggregator with a no-needs "
            "poller; a `needs:` here is a regression to the needs-graph-"
            "omission bug class (OCC#6346), where a job absent from `needs:` "
            "was invisible to the old gate — which is exactly long enough for "
            "the merge sweep to land a held PR."
        )
    summary_run_blob = _collect_run_script(summary)
    if "ci_summary_gate.py" not in summary_run_blob:
        failures.append(
            f"`{_SUMMARY_JOB_ID}` does not invoke scripts/ci/ci_summary_gate.py "
            "-- it no longer looks like the OMN-15768 poller."
        )

    strict_gate_jobs = _extract_strict_gate_jobs(gate_module_path)
    if strict_gate_jobs is None:
        failures.append(
            f"could not read STRICT_GATE_JOBS from {gate_module_path} to "
            f"verify `{_EXPECTED_CONTEXT}` is registered."
        )
        return failures

    if _EXPECTED_CONTEXT not in strict_gate_jobs:
        failures.append(
            f"`{_EXPECTED_CONTEXT}` is not in scripts/ci/ci_summary_gate.py's "
            "STRICT_GATE_JOBS. An unregistered hold gate looks like "
            "enforcement and enforces nothing — the default-deny sweep alone "
            "is not sufficient proof of intent."
        )
    return failures


def check_wiring(
    ci_yaml_path: Path, gate_module_path: Path | None = None
) -> list[str]:
    """Return a list of wiring failures. Empty list means the wiring is intact.

    ``gate_module_path`` defaults to ``<repo_root>/scripts/ci/ci_summary_gate.py``;
    tests override it to point at an isolated mutated copy.
    """
    jobs: dict[str, Any] = _load_ci_yaml(ci_yaml_path)["jobs"]

    job = jobs.get(_JOB_ID)
    if not isinstance(job, dict):
        return [
            f"job `{_JOB_ID}` is absent from {ci_yaml_path.name}. It is this "
            "repository's ONLY protection against the merge sweep landing a PR "
            f"that is explicitly held ({_TICKET}); every incident in "
            "OMN-15483's table that happened here did so with all required "
            "contexts green."
        ]

    failures = _check_hold_job(job)

    summary = jobs.get(_SUMMARY_JOB_ID)
    if not isinstance(summary, dict):
        failures.append(
            f"job `{_SUMMARY_JOB_ID}` is absent — `CI Summary` is the required "
            "context on OCC dev; without it nothing is enforced."
        )
        return failures

    resolved_gate_path = gate_module_path or (_repo_root() / _GATE_MODULE_RELPATH)
    return failures + _check_summary_job(summary, resolved_gate_path)


_USAGE = f"""\
usage: check_merge_hold_gate_wiring.py [-h] [CI_YAML ...]

{_TICKET}: assert the Merge Hold Gate is wired into a REQUIRED CI context.
Checks that ci.yml declares the `{_JOB_ID}` job, that it is unconditional (no
`needs:`, no `if:`), that it calls the SHARED reusable workflow at an immutable
or mainline ref rather than re-implementing the vocabulary locally, that its
declared `context_name` equals the context GitHub actually mints
(`{_EXPECTED_CONTEXT}`), and that `{_SUMMARY_JOB_ID}` both lists it in `needs:`
AND carries a strict success-only check for it (the generic
`contains(needs.*.result, 'failure')` rollup passes on a SKIPPED need).

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
    # usage is rejected as broken.
    if "-h" in args or "--help" in args:
        print(_USAGE, end="")
        return 0
    # pre-commit passes the matched filenames (its `files:` regex already
    # restricts them to ci.yml). Every supplied path is checked AS GIVEN: an
    # `endswith` filter with a fallback to the canonical path makes a
    # missing/renamed target exit 0 while printing PASSED for a file the caller
    # never named, which is the exit-0-on-missing shape the fail-loud pre-commit
    # meta-gate forbids (OMN-14666/14668). Only a bare invocation resolves the
    # canonical path.
    paths = [Path(a) for a in args] or [
        _repo_root() / ".github" / "workflows" / "ci.yml"
    ]

    rc = 0
    for path in paths:
        try:
            failures = check_wiring(path)
        except (CiWorkflowUnreadableError, yaml.YAMLError) as exc:
            print(f"MERGE-HOLD-GATE WIRING GATE FAILED ({path}): {exc}")
            rc = 1
            continue
        if failures:
            rc = 1
            print(f"MERGE-HOLD-GATE WIRING GATE FAILED ({path}) [{_TICKET}]:")
            for failure in failures:
                print(f"  - {failure}")
        else:
            print(f"MERGE-HOLD-GATE WIRING GATE PASSED ({path})")
    return rc


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
