# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-15479: yamlfmt contamination ratchet for the evidence control plane.

What this gate exists to stop
-----------------------------
``yamlfmt`` (pinned ``google/yamlfmt v0.21.0`` in ``.pre-commit-config.yaml``,
running on every commit) **rewrites the parsed value** of a folded (``>``) block
scalar that carries an internal newline, and reports ``Passed``. Every internal
newline is replaced by the literal 19-character token ``#magic___^_^___line``
and the newline itself is destroyed. The output is still valid, parseable YAML,
which is what makes it worse than an unparseable-output bug: it lands in a
merged evidence receipt looking correct.

``contracts/`` and ``drift/dod_receipts/`` are the evidence control plane.
``actual_output``, ``probe_stdout`` and ``check_value`` are the durable record of
what was probed and what came back. A formatter that silently rewrites the
parsed text of those fields produces evidence that says something the verifier
never observed -- the false-attestation failure the receipt surface exists to
prevent, arriving through the enforcement chain itself.

The two rules
-------------
**Rule S (sentinel present).** The marker ``#magic___^_^___line`` appearing in a
parsed YAML *value* under ``contracts/`` or ``drift/dod_receipts/`` is proof of
this corruption, past or present. Detection is over parsed values rather than
raw bytes deliberately: a comment that *discusses* the marker (this file, the
baselines, the tests, a ticket contract describing the defect) is not evidence
corruption, but a marker reaching a value always is. Measured at authoring time,
the two detections agree exactly on the live corpus -- 510 files / 784
occurrences either way, zero raw-only hits -- so nothing is lost by choosing the
precise one. A file whose marker-bearing text will not parse is counted from raw
bytes instead and still fails: unparseable never means clean.

**Rule F (corruption precondition).** A folded (``>``/``>-``/``>+``) block
scalar whose parsed value contains an internal newline is the input shape that
Rule S's corruption is produced *from*. Rejecting it loudly, with a pointer to
literal (``|``/``|-``) block style, is what stops the corpus growing. Literal
scalars and single-paragraph folded scalars are both proven-safe controls: they
survive yamlfmt byte-identical, including a line over ``max_line_length``.

Both rules are **shrink-only set-equality ratchets**, not flat assertions. The
live corpus already carries 510 contaminated files and 14 precondition files;
a flat assertion would be permanently red and therefore unmergeable. The
baselines pin ``path -> occurrence count``, so the ratchet fails on a NEW path,
on a NEW occurrence inside an already-baselined path, and on a stale entry that
a live scan no longer reproduces. All three directions are enforced.

This gate never mutates a file. Formatter mutation is the disease, not the cure.

Exit codes: ``0`` clean, ``1`` violation / unreadable input / broken wiring.
Never exits 0 on a missing path or an unparseable file -- an absent gate must be
byte-indistinguishable from a failing one (the OMN-14666/14668 lesson).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

# The yamlfmt v0.21.0 internal line-marker sentinel. Its presence in a parsed
# YAML value is never legitimate authored content.
SENTINEL = "#magic___^_^___line"

# The two trees that make up the evidence control plane.
SCOPED_PREFIXES = ("contracts/", "drift/dod_receipts/")
YAML_SUFFIXES = (".yaml", ".yml")

_TICKET = "OMN-15479"
_SELF_SCRIPT_NAME = "check_yamlfmt_contamination.py"
_JOB_ID = "yamlfmt-contamination-ratchet"
_SUMMARY_JOB_ID = "ci-summary"
_RATCHET_TEST_MODULE = "tests/unit/scripts/test_yamlfmt_contamination_gate.py"

SENTINEL_BASELINE_REL = ".onex_ratchets/omn_15479_yamlfmt_sentinel_baseline.yaml"
FOLDED_BASELINE_REL = ".onex_ratchets/omn_15479_folded_scalar_baseline.yaml"

_LITERAL_FIX_HINT = (
    "Use a literal block scalar (`|` or `|-`) instead of folded (`>`). Literal "
    "scalars survive yamlfmt byte-identical, including lines longer than "
    "`max_line_length`. Folded scalars carrying an internal newline do not: "
    f"yamlfmt replaces every internal newline with `{SENTINEL}` and destroys "
    "the newline, silently changing what the evidence says."
)


class CorpusUnreadableError(Exception):
    """The repository tree could not be enumerated.

    A distinct type so callers cannot conflate "gate found violations" with
    "gate could not read the corpus". Both are non-zero; neither is a silent
    pass.
    """


class CiWorkflowUnreadableError(Exception):
    """ci.yml is missing or does not parse into the expected shape."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Rule S -- yamlfmt sentinel in a parsed value
# ---------------------------------------------------------------------------


def _iter_scalar_values(node: Any) -> list[str]:
    """Every string that appears as a key or value anywhere in a parsed doc."""
    found: list[str] = []
    if isinstance(node, str):
        found.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            found.extend(_iter_scalar_values(key))
            found.extend(_iter_scalar_values(value))
    elif isinstance(node, (list, tuple)):
        for item in node:
            found.extend(_iter_scalar_values(item))
    return found


def count_sentinel_in_values(text: str) -> int:
    """Count ``SENTINEL`` occurrences inside parsed YAML values.

    Falls back to a raw-byte count when the text carries the marker but will not
    parse -- fail-closed, because "I could not parse it" must never read as
    "it is clean". Text without the marker short-circuits before parsing, which
    keeps the corpus scan cheap (only ~510 of ~23,500 files are ever parsed).
    """
    if SENTINEL not in text:
        return 0
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return text.count(SENTINEL)
    return sum(
        value.count(SENTINEL)
        for document in documents
        for value in _iter_scalar_values(document)
    )


# ---------------------------------------------------------------------------
# Rule F -- folded block scalar carrying an internal newline
# ---------------------------------------------------------------------------


def find_folded_internal_newline_scalars(text: str) -> list[tuple[int, str]]:
    """Locate folded scalars whose parsed value contains an internal newline.

    Returns ``(1-indexed line, preview)`` per finding. This is the exact input
    shape yamlfmt corrupts: a folded scalar resolves a paragraph break (or a
    more-indented line) to ``\\n`` in its parsed value, and that ``\\n`` is what
    the formatter replaces with the sentinel.

    Uses the YAML *scanner* rather than the loader because only the scanner
    exposes the authored scalar ``style``; ``yaml.safe_load`` has already thrown
    that away. Leading/trailing newlines are stripped before the check so that
    chomping indicators (``>``/``>-``/``>+``) do not by themselves count as
    internal structure -- a single-paragraph folded scalar is a proven-safe
    control and must not be flagged.
    """
    if ">" not in text:
        return []
    findings: list[tuple[int, str]] = []
    try:
        for token in yaml.scan(text):
            if type(token).__name__ != "ScalarToken":
                continue
            if getattr(token, "style", None) != ">":
                continue
            value = token.value
            if "\n" not in value.strip("\n"):
                continue
            preview = " ".join(value.split())[:80]
            findings.append((token.start_mark.line + 1, preview))
    except yaml.YAMLError:
        # Unparseable input is not this rule's business -- validate-contract-yaml
        # and the honesty/hardening gates reject it loudly on their own surfaces.
        # Rule S still counts such a file from raw bytes, so nothing is skipped
        # silently here.
        return findings
    return findings


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def is_in_scope(rel_path: str) -> bool:
    return rel_path.startswith(SCOPED_PREFIXES) and rel_path.endswith(YAML_SUFFIXES)


def scan_file(path: Path) -> tuple[int, list[tuple[int, str]]]:
    """Return ``(sentinel_count, folded_findings)`` for one file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return count_sentinel_in_values(text), find_folded_internal_newline_scalars(text)


def _enumerate_corpus(repo_root: Path) -> list[str]:
    """Repo-relative paths of every in-scope YAML file, tracked or newly added.

    ``--cached --others --exclude-standard`` deliberately includes untracked,
    non-ignored files: a file that is about to be committed must be visible to
    the ratchet before it lands, not after. Enumeration failure raises rather
    than degrading to an empty list, which would report a vacuous PASS.
    """
    try:
        completed = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "contracts",
                "drift/dod_receipts",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        msg = f"could not enumerate the corpus under {repo_root}: {exc}"
        raise CorpusUnreadableError(msg) from exc
    return sorted({p for p in completed.stdout.splitlines() if is_in_scope(p)})


def scan_corpus(repo_root: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Return ``(sentinel_counts, folded_counts)`` keyed by repo-relative path.

    Only violating files appear; a clean file contributes no key.
    """
    sentinel_counts: dict[str, int] = {}
    folded_counts: dict[str, int] = {}
    for rel in _enumerate_corpus(repo_root):
        path = repo_root / rel
        if not path.is_file():
            continue
        try:
            sentinel_count, folded = scan_file(path)
        except OSError as exc:
            msg = f"could not read {rel}: {exc}"
            raise CorpusUnreadableError(msg) from exc
        if sentinel_count:
            sentinel_counts[rel] = sentinel_count
        if folded:
            folded_counts[rel] = len(folded)
    return sentinel_counts, folded_counts


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


def load_baseline(path: Path) -> dict[str, int]:
    """Load a ``path -> occurrence count`` baseline.

    A missing or malformed baseline raises. An absent baseline must never be
    read as "nothing is baselined, therefore everything is new" *or* as
    "nothing is baselined, therefore everything passes" -- both are silent
    failure modes.
    """
    if not path.is_file():
        msg = f"baseline file {path} does not exist"
        raise CorpusUnreadableError(msg)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"baseline file {path} did not parse to a mapping"
        raise CorpusUnreadableError(msg)
    entries = data.get("baseline")
    if not isinstance(entries, dict):
        msg = f"baseline file {path} has no `baseline:` mapping"
        raise CorpusUnreadableError(msg)
    out: dict[str, int] = {}
    for key, value in entries.items():
        if not isinstance(key, str) or not isinstance(value, int) or value < 1:
            msg = f"baseline file {path} has a malformed entry: {key!r}: {value!r}"
            raise CorpusUnreadableError(msg)
        out[key] = value
    return out


def diff_against_baseline(
    live: dict[str, int], baseline: dict[str, int]
) -> tuple[dict[str, int], dict[str, tuple[int, int]], dict[str, int]]:
    """Return ``(new_paths, grown_paths, stale_entries)``.

    * ``new_paths`` -- violating and absent from the baseline. The ratchet's
      primary direction: a NEW contaminated or precondition-carrying file.
    * ``grown_paths`` -- baselined, but carrying MORE occurrences than frozen.
      Path-set equality alone would let an author append a second corrupted
      value to an already-listed file for free.
    * ``stale_entries`` -- baselined but no longer reproduced live. Removing the
      debt without shrinking the baseline defeats the ratchet exactly as much as
      padding it.
    """
    new_paths = {p: c for p, c in live.items() if p not in baseline}
    grown_paths = {
        p: (baseline[p], c)
        for p, c in live.items()
        if p in baseline and c > baseline[p]
    }
    stale_entries = {p: c for p, c in baseline.items() if live.get(p, 0) < c}
    return new_paths, grown_paths, stale_entries


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def check_paths(repo_root: Path, paths: list[Path]) -> list[str]:
    """Per-file mode (pre-commit). Return a list of failure messages."""
    sentinel_baseline = load_baseline(repo_root / SENTINEL_BASELINE_REL)
    folded_baseline = load_baseline(repo_root / FOLDED_BASELINE_REL)
    failures: list[str] = []

    for path in paths:
        try:
            rel = str(path.resolve().relative_to(repo_root))
        except ValueError:
            rel = str(path)
        if not is_in_scope(rel):
            print(
                f"  NOTE: {rel} is outside contracts/ + drift/dod_receipts/ - skipped"
            )
            continue
        if not path.is_file():
            failures.append(f"{rel}: named on the command line but is not a file")
            continue

        sentinel_count, folded = scan_file(path)

        allowed_sentinel = sentinel_baseline.get(rel, 0)
        if sentinel_count > allowed_sentinel:
            failures.append(
                f"{rel}: {sentinel_count} yamlfmt sentinel occurrence(s) in parsed "
                f"values, baseline allows {allowed_sentinel}. The literal "
                f"`{SENTINEL}` is a yamlfmt internal marker -- its presence in a "
                "committed evidence value is proof the formatter rewrote content "
                "that a verifier authored. Restore the original text and re-author "
                "the field as a literal block scalar. Do NOT add this file to "
                f"{SENTINEL_BASELINE_REL}; that baseline is frozen debt and may "
                "only shrink."
            )

        allowed_folded = folded_baseline.get(rel, 0)
        if len(folded) > allowed_folded:
            locations = ", ".join(
                f"line {line} ({preview!r})" for line, preview in folded
            )
            failures.append(
                f"{rel}: {len(folded)} folded (`>`) block scalar(s) carrying an "
                f"internal newline, baseline allows {allowed_folded}: {locations}. "
                f"{_LITERAL_FIX_HINT} This is the corruption precondition, not the "
                "corruption -- rejecting it here is what keeps the sentinel out of "
                "the evidence corpus."
            )
    return failures


def check_corpus(repo_root: Path) -> list[str]:
    """Corpus mode (CI). Full shrink-only set-equality ratchet, both rules."""
    live_sentinel, live_folded = scan_corpus(repo_root)
    failures: list[str] = []

    for label, live, baseline_rel, remedy in (
        (
            "Rule S (yamlfmt sentinel in a parsed evidence value)",
            live_sentinel,
            SENTINEL_BASELINE_REL,
            "Restore the original text and re-author the field as a literal "
            "block scalar (`|`/`|-`).",
        ),
        (
            "Rule F (folded `>` scalar carrying an internal newline)",
            live_folded,
            FOLDED_BASELINE_REL,
            _LITERAL_FIX_HINT,
        ),
    ):
        baseline = load_baseline(repo_root / baseline_rel)
        new_paths, grown_paths, stale_entries = diff_against_baseline(live, baseline)

        if new_paths:
            failures.append(
                f"{label}: {len(new_paths)} NEW file(s) not in the frozen "
                f"shrink-only baseline ({baseline_rel}): "
                f"{sorted(new_paths)[:20]}. {remedy}"
            )
        if grown_paths:
            detail = sorted(
                f"{p} ({was} -> {now})" for p, (was, now) in grown_paths.items()
            )
            failures.append(
                f"{label}: {len(grown_paths)} baselined file(s) gained NEW "
                f"occurrences: {detail[:20]}. A baselined path is frozen debt at "
                "a frozen count, not a licence to add more. " + remedy
            )
        if stale_entries:
            failures.append(
                f"{label}: {len(stale_entries)} baseline entr"
                f"{'y is' if len(stale_entries) == 1 else 'ies are'} no longer "
                f"reproduced by a live scan, but {baseline_rel} was not shrunk to "
                f"match: {sorted(stale_entries)[:20]}. Shrink the baseline in the "
                "same commit that repairs the file -- a stale entry hides the next "
                "real violation."
            )

    print(
        f"  corpus scan: {len(live_sentinel)} file(s) carry the sentinel, "
        f"{len(live_folded)} file(s) carry a folded scalar with an internal newline"
    )
    return failures


# ---------------------------------------------------------------------------
# Anti-removal anchor -- the gate must stay wired into a REQUIRED context
# ---------------------------------------------------------------------------


def _load_ci_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        msg = f"{path} does not exist"
        raise CiWorkflowUnreadableError(msg)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"{path} did not parse to a mapping"
        raise CiWorkflowUnreadableError(msg)
    if not isinstance(data.get("jobs"), dict):
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


def check_wiring(ci_yaml_path: Path) -> list[str]:
    """Assert the ratchet job exists, is unconditional, and gates CI Summary.

    Detection that is not a merge gate gets ignored (root CLAUDE.md rule 5), and
    a gate whose two halves can be deleted in one edit is not a gate. This runs
    both as a pre-commit hook scoped to ci.yml and as a step inside the job
    itself, so the wiring is re-asserted on every PR rather than only on PRs
    that happen to touch the workflow.
    """
    jobs: dict[str, Any] = _load_ci_yaml(ci_yaml_path)["jobs"]

    job = jobs.get(_JOB_ID)
    if not isinstance(job, dict):
        return [
            f"job `{_JOB_ID}` is absent from {ci_yaml_path.name}. It is the only "
            f"required-path surface that scans the whole evidence corpus for "
            f"yamlfmt contamination ({_TICKET}); removing it re-opens the gap "
            "where a newly corrupted receipt merges to dev with every required "
            "context green."
        ]

    failures: list[str] = []
    if "needs" in job:
        failures.append(
            f"job `{_JOB_ID}` declares `needs:` ({job['needs']!r}). It must be "
            "unconditional -- a needs-chain lets an upstream skip silently skip "
            "the ratchet."
        )
    if "if" in job:
        failures.append(
            f"job `{_JOB_ID}` declares `if:` ({job['if']!r}). It must be "
            "unconditional -- ci.yml's `test` job is skipped on dev PRs by "
            "exactly such a condition, which is the defect this job avoids."
        )

    run_blob = _collect_run_script(job)
    if f"{_SELF_SCRIPT_NAME} --corpus" not in run_blob:
        failures.append(
            f"job `{_JOB_ID}` does not run `{_SELF_SCRIPT_NAME} --corpus`. The "
            "job name is not the gate; executing the corpus ratchet is."
        )
    if _RATCHET_TEST_MODULE not in run_blob:
        failures.append(
            f"job `{_JOB_ID}` does not run `{_RATCHET_TEST_MODULE}`, which holds "
            "the detector RED/GREEN controls and the hook-fires-on-a-fixture "
            "assertions."
        )
    if "--check-wiring" not in run_blob:
        failures.append(
            f"job `{_JOB_ID}` does not re-run `{_SELF_SCRIPT_NAME} "
            "--check-wiring`. The job must re-assert its own wiring on every PR, "
            "not only on PRs that edit ci.yml."
        )

    summary = jobs.get(_SUMMARY_JOB_ID)
    if not isinstance(summary, dict):
        failures.append(
            f"job `{_SUMMARY_JOB_ID}` is absent -- `CI Summary` is the required "
            "context on OCC dev; without it nothing is enforced."
        )
        return failures

    needs = summary.get("needs")
    needs_list = [needs] if isinstance(needs, str) else list(needs or [])
    if _JOB_ID not in needs_list:
        failures.append(
            f"`{_SUMMARY_JOB_ID}` does not list `{_JOB_ID}` in `needs:`, so the "
            "required CI Summary context does not wait for the ratchet and can "
            "report green before/without it."
        )
    strict_expr = f'needs.{_JOB_ID}.result }}}}" != "success"'
    if strict_expr not in _collect_run_script(summary):
        failures.append(
            f"`{_SUMMARY_JOB_ID}` has no strict success-only check for "
            f"`{_JOB_ID}`. Expected a line containing `{strict_expr}`. The "
            "generic rollup only tests for 'failure'/'cancelled', so a SKIPPED "
            "ratchet job would pass CI Summary."
        )
    return failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_USAGE = f"""\
usage: check_yamlfmt_contamination.py [-h] [--corpus | --check-wiring] [PATH ...]

{_TICKET}: yamlfmt contamination ratchet over contracts/ + drift/dod_receipts/.

Rule S  a parsed YAML value containing the yamlfmt sentinel `{SENTINEL}`
        -- proof the formatter rewrote authored evidence content.
Rule F  a folded (`>`/`>-`/`>+`) block scalar whose parsed value carries an
        internal newline -- the input shape Rule S's corruption is produced
        from. Literal (`|`/`|-`) block scalars are the safe alternative.

Both rules are shrink-only set-equality ratchets against frozen baselines
({SENTINEL_BASELINE_REL},
{FOLDED_BASELINE_REL}).
A NEW path fails, a NEW occurrence in a baselined path fails, and a stale
baseline entry no longer reproduced by a live scan fails. This gate never
modifies a file.

modes:
  PATH ...        per-file mode (pre-commit `pass_filenames: true`)
  --corpus        full-tree shrink-only ratchet, both rules (the ci.yml job).
                  Also the default when no PATH is given, so a bare invocation
                  can never produce a vacuous pass.
  --check-wiring  assert the ci.yml ratchet job exists, is unconditional, runs
                  the corpus mode + ratchet tests, and is gated by a strict
                  success-only check in ci-summary. Defaults to
                  .github/workflows/ci.yml when no PATH is given.

options:
  -h, --help      show this help message and exit

exit codes: 0 clean, 1 violation / unreadable input / broken wiring.
"""


def _run_wiring_mode(repo_root: Path, paths: list[Path]) -> int:
    # Every supplied path is checked AS GIVEN. Falling back to the canonical
    # path when a named file is missing would print PASSED for a file the
    # caller never named -- the exit-0-on-missing shape the fail-loud
    # pre-commit meta-gate forbids.
    targets = paths or [repo_root / ".github" / "workflows" / "ci.yml"]
    rc = 0
    for target in targets:
        try:
            failures = check_wiring(target)
        except (CiWorkflowUnreadableError, yaml.YAMLError) as exc:
            print(f"YAMLFMT CONTAMINATION WIRING GATE FAILED ({target}): {exc}")
            rc = 1
            continue
        if failures:
            rc = 1
            print(f"YAMLFMT CONTAMINATION WIRING GATE FAILED ({target}) [{_TICKET}]:")
            for failure in failures:
                print(f"  - {failure}")
        else:
            print(f"YAMLFMT CONTAMINATION WIRING GATE PASSED ({target})")
    return rc


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # The Pre-commit job's changed-validator step executes every modified
    # validator with `--help` before merging, so a gate that cannot print usage
    # is rejected as broken.
    if "-h" in args or "--help" in args:
        print(_USAGE, end="")
        return 0

    corpus_mode = "--corpus" in args
    wiring_mode = "--check-wiring" in args
    paths = [Path(a) for a in args if not a.startswith("-")]
    repo_root = _repo_root()

    if corpus_mode and wiring_mode:
        print("ERROR: --corpus and --check-wiring are mutually exclusive")
        return 1

    if wiring_mode:
        return _run_wiring_mode(repo_root, paths)

    try:
        if corpus_mode or not paths:
            failures = check_corpus(repo_root)
        else:
            failures = check_paths(repo_root, paths)
    except (CorpusUnreadableError, yaml.YAMLError) as exc:
        print(f"YAMLFMT CONTAMINATION GATE FAILED: {exc}")
        return 1

    if failures:
        print(f"YAMLFMT CONTAMINATION GATE FAILED [{_TICKET}]:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("YAMLFMT CONTAMINATION GATE PASSED")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
