# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Mint the structural OCC self-bind a hand-authored companion owes (OMN-18921).

WHAT THIS REMOVES. A hand-authored evidence companion on this repository needs
TWO artifacts, not one: the receipt under ``drift/dod_receipts/<ticket>/`` that
the author came here to write, and a SECOND, structural receipt at
``drift/occ_bindings/<ticket>/occ-self-bind-pr-<n>/command.yaml`` that binds the
ticket to this OCC pull request. Nothing tells the author about the second one
until the eligibility check has already run and failed with
``missing_occ_self_bind``, which costs a full CI cycle -- roughly fifty checks --
per occurrence. Five occurrences since 2026-08-25: OCC#10260, #10341, #10356,
#10368 and #10554.

WHY THE DIAGNOSTIC WAS NOT ENOUGH, stated plainly because a previous ticket
already tried it. OMN-16353 shipped the distinct ``missing_occ_self_bind``
reason carrying exact remediation text, and that fix WORKS -- lanes following it
are right first time. But it fires AFTER the push, so it converts an
unexplained round trip into a well-explained one. It removes diagnosis time, not
the cycle. Two of the five lanes above are named ``occ-companion-repair-*``,
which is the clearest available signal that the remedy did not remove the work.

WHY THIS CANNOT BE A PRE-PUSH HOOK. The artifact names the OCC pull request
number, and that number does not exist until the pull request is opened, which
happens after the first push. A pre-push hook could only ever print the same
advice OMN-16353 already prints. The mint therefore has to run on a
``pull_request`` event, write the artifact, and push it -- the same shape
``omnimarket``'s behaviour-proof backfill already uses for its own post-create
self-bind step, and for the same reason.

THE TRIGGER IS THE GATE'S OWN VERDICT, NOT A SECOND DERIVATION. This script does
not re-implement "does this companion owe a self-bind". It runs
:func:`validate_occ_merge_eligibility` -- the exact function whose refusal costs
the round -- and mints only when that function returns
``MISSING_OCC_SELF_BIND``. Every other verdict, eligible or not, exits 0 having
written nothing. Reaching that reason means every contract resolved and every
declared receipt is PASS and hash-bound, and the ONLY defect is that nothing
binds to this pull request, so the remedy is unambiguous.

THE RECEIPT IS EARNED, NOT ASSERTED. The declared check is
``gh pr view <n> --json number,state,headRefName`` and this script RUNS it, in
CI, where ``gh`` is authenticated, and records the real command, the real
stdout and the real exit code. ``status: PASS`` is written only when that
command actually succeeded. A fabricated PASS here would be the false-evidence
class the receipt honesty gate exists to catch, on the one surface whose whole
job is proving a binding.

WHICH OF THE TWO SELF-BIND PATHS THIS WRITES, AND WHY. Both of these exist on
this repository today and BOTH satisfy the gate, because the validator reads
them on two different branches:

* ``drift/dod_receipts/<ticket>/occ-self-bind-pr-<n>/command.yaml`` -- the
  DECLARED shape. Its evidence id IS listed in the contract's ``dod_evidence``,
  so the validator's ordinary declared-evidence loop resolves it. This is what
  the autobind producer mints for its own companions since OMN-18304, and it is
  the large population (thousands of files).
* ``drift/occ_bindings/<ticket>/occ-self-bind-pr-<n>/command.yaml`` -- the
  UNDECLARED structural shape (OMN-18075). Its id is deliberately ABSENT from
  ``dod_evidence``, so it is resolved only by the structural fallback branch,
  which runs when declared evidence has not already bound the ticket. Small
  population, and the shape the gate's own remediation text instructs.

They are therefore two SHAPES with two readers, not one artifact forked across
two directories -- sampled and measured: declared-tree self-binds are declared
in their contract, structural-tree ones are not.

**This script writes the STRUCTURAL shape**, for one reason that is not
preference: the declared shape requires APPENDING a ``dod_evidence`` item to the
contract, which changes the contract bytes and therefore restales the
``contract_sha256`` of every receipt already bound to it. Doing that correctly
needs the producer's rebind pass. The structural shape needs no contract
mutation at all, so a mint is purely additive and cannot invalidate a merged
receipt. It is also exactly what a hand-authoring lane is told to write.

**Which shape is canonical by design is an OPEN QUESTION and is not settled
here.** The decision lives in two places that currently point different ways:
OMN-18304 moved the autobind producer onto the declared shape and
``omnimarket/scripts/ci/check_self_bind_entry_binding.py`` forbids new SOURCE
writes to the structural tree, which makes the declared shape the direction of
travel for producers; OMN-18075 defined the structural shape and the
remediation string still instructs it. Reconciling them is a follow-up, not
this change.

OMN-18075 SHAPE, ALL FOUR PROPERTIES. The artifact lives under
``drift/occ_bindings/``, is NOT declared in the contract's ``dod_evidence``,
carries no ``binds_ac``, and carries no ``contract_entry_sha256`` -- an
undeclared item has no contract entry to hash. It carries
``contract_sha256`` (the whole-file hash) and ``pr_number`` instead, which is
what the structural branch of the validator binds on. The receipt-hardening gate
governs ``drift/dod_receipts/`` and ``contracts/`` only, so this tree is outside
its scope by design and this script must not write anywhere else.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from omnibase_core.enums.enum_occ_eligibility_reason import EnumOccEligibilityReason
from omnibase_core.models.validation.model_occ_eligibility_input import (
    ModelOccEligibilityInput,
)
from omnibase_core.validation.validator_occ_merge_eligibility import (
    validate_occ_merge_eligibility,
)
from omnibase_core.validation.validator_receipt_gate import compute_contract_sha256

#: The structural tree. Deliberately NOT ``drift/dod_receipts`` -- that prefix
#: is the receipt-hardening gate's scope and a declared item's home, and an
#: undeclared self-bind written there is the OMN-18075 defect.
STRUCTURAL_DIR = Path("drift") / "occ_bindings"

#: The check type the validator's key triple requires, so the file is
#: ``command.yaml``.
SELF_BIND_CHECK_TYPE = "command"

#: Pulled out of the remediation string the validator itself renders, so the
#: set of tickets this script mints for is the set that function named. A
#: second derivation of "which tickets are unbound" is a second thing to drift;
#: ``test_the_extraction_round_trips_against_the_real_remediation`` fails if the
#: wording moves, rather than letting the mint silently no-op.
_REMEDIATION_TICKET_RE = re.compile(
    r"drift/occ_bindings/(?P<ticket>[A-Z]+-\d+)/occ-self-bind-pr-\d+/command\.yaml"
)

_RUNNER = "occ-self-bind-mint"
_VERIFIER = "github-pr-readback"


def self_bind_evidence_id(pr_number: int) -> str:
    """Return the evidence item id the validator's key triple requires."""
    return f"occ-self-bind-pr-{pr_number}"


def self_bind_path(repo_root: Path, ticket_id: str, pr_number: int) -> Path:
    """Return the structural receipt path for ``ticket_id`` on this PR."""
    return (
        repo_root
        / STRUCTURAL_DIR
        / ticket_id
        / self_bind_evidence_id(pr_number)
        / f"{SELF_BIND_CHECK_TYPE}.yaml"
    )


def unbound_tickets_from_detail(detail: str) -> tuple[str, ...]:
    """Return the ticket ids the validator's remediation names, in order.

    Deduplicated while preserving first-seen order so the mint is deterministic
    for a given verdict.
    """
    seen: list[str] = []
    for match in _REMEDIATION_TICKET_RE.finditer(detail):
        ticket = match.group("ticket")
        if ticket not in seen:
            seen.append(ticket)
    return tuple(seen)


def build_check_value(*, repo: str, pr_number: int) -> str:
    """Return the declared check: a live readback of THIS pull request."""
    return f"gh pr view {pr_number} --repo {repo} --json number,state,headRefName"


def run_probe(*, repo: str, pr_number: int) -> tuple[str, str, int]:
    """Execute the declared check and return (command, stdout, exit code).

    Runs the real command rather than describing it. A non-zero exit is
    returned rather than raised so the caller can refuse to write a PASS.
    """
    command = build_check_value(repo=repo, pr_number=pr_number)
    # Resolved rather than relying on PATH lookup at exec time: an absent gh
    # must be a loud failure, not a probe that silently measures something
    # else. S607 is about exactly this.
    gh = shutil.which("gh")
    if gh is None:
        message = (
            "gh is not on PATH; the declared check cannot be executed and a "
            "receipt asserting it passed would be fabricated (OMN-18921)"
        )
        raise RuntimeError(message)
    completed = subprocess.run(  # noqa: S603 -- resolved path, fixed argv, no shell
        [
            gh,
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "number,state,headRefName",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return command, completed.stdout, completed.returncode


def render_self_bind(
    *,
    ticket_id: str,
    pr_number: int,
    repo: str,
    contract_sha256: str,
    commit_sha: str,
    branch: str,
    run_timestamp: str,
    probe_command: str,
    probe_stdout: str,
    exit_code: int,
) -> str:
    """Render the structural self-bind receipt YAML.

    Field order mirrors the hand-authored companions already merged on this
    repository, so a reviewer diffing a minted one against a hand-written one
    sees no gratuitous reshuffle.
    """
    indented_stdout = "".join(f"  {line}\n" for line in probe_stdout.splitlines())
    return (
        "---\n"
        'schema_version: "1.0.0"\n'
        f'ticket_id: "{ticket_id}"\n'
        f'evidence_item_id: "{self_bind_evidence_id(pr_number)}"\n'
        f'check_type: "{SELF_BIND_CHECK_TYPE}"\n'
        f'check_value: "{build_check_value(repo=repo, pr_number=pr_number)}"\n'
        f'contract_sha256: "{contract_sha256}"\n'
        "status: PASS\n"
        f'run_timestamp: "{run_timestamp}"\n'
        f'commit_sha: "{commit_sha}"\n'
        f'runner: "{_RUNNER}"\n'
        f'verifier: "{_VERIFIER}"\n'
        f'probe_command: "{probe_command}"\n'
        "probe_stdout: |\n"
        f"{indented_stdout}"
        f"exit_code: {exit_code}\n"
        f"pr_number: {pr_number}\n"
        f'branch: "{branch}"\n'
    )


def _snapshot(args: argparse.Namespace) -> ModelOccEligibilityInput:
    return ModelOccEligibilityInput(
        repo=args.repo,
        pr_number=args.pr_number,
        pr_title=args.pr_title,
        pr_body=args.pr_body,
        pr_branch=args.branch,
        pr_commit_shas=tuple(args.pr_commit_sha or ()),
        pr_commit_texts=tuple(args.pr_commit_text or ()),
        occ_commit_sha=args.commit_sha,
        contracts_dir=Path(args.repo_root) / "contracts",
        receipts_dir=Path(args.repo_root) / "drift" / "dod_receipts",
    )


def main(argv: list[str] | None = None) -> int:
    """Mint the self-bind when, and only when, the gate says one is owed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name of THIS repo")
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--commit-sha", required=True, help="OCC head sha, 40 hex")
    parser.add_argument("--branch", required=True, help="the PR head branch")
    parser.add_argument("--pr-title", default="")
    parser.add_argument("--pr-body", default="")
    # THE SNAPSHOT MUST BE THE ONE occ-preflight BUILDS, FIELD FOR FIELD.
    #
    # This script's whole safety property is that it mints on the eligibility
    # gate's own verdict rather than on a second derivation. That holds only if
    # it feeds the gate the same snapshot the gate is fed in CI. It does not
    # hold automatically: ``call-occ-preflight.yml`` loops over EVERY commit on
    # the pull request and passes both ``--pr-commit-sha`` and
    # ``--pr-commit-text`` per commit, and ``_ticket_bound_to_pr`` searches the
    # title, the branch AND those commit texts.
    #
    # Measured on a fixture whose title does not cite the ticket: a snapshot
    # without commit texts returns ``pr_ticket_mismatch`` where preflight
    # returns ``missing_occ_self_bind``. That difference is not cosmetic -- it
    # is precisely a companion bound only through its commit message, for which
    # this script would have exited 0 reporting success while the author still
    # paid the round the script exists to remove. The converse costs a pushed
    # artifact nobody needed.
    parser.add_argument(
        "--pr-commit-sha",
        action="append",
        default=[],
        help="a commit sha on this PR; repeatable, pass ALL of them",
    )
    parser.add_argument(
        "--pr-commit-text",
        action="append",
        default=[],
        help="a commit message on this PR; repeatable, pass ALL of them",
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the artifact; without it the run reports and writes nothing",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    result = validate_occ_merge_eligibility(_snapshot(args))

    if result.reason is not EnumOccEligibilityReason.MISSING_OCC_SELF_BIND:
        print(
            "mint_occ_self_bind: nothing to mint -- eligibility reason is "
            f"{result.reason.value}, not missing_occ_self_bind. This script "
            "mints only for the one verdict whose sole defect is an absent "
            "self-bind (OMN-18921)."
        )
        return 0

    tickets = unbound_tickets_from_detail(result.detail or "")
    if not tickets:
        # Fail LOUD. The verdict says a self-bind is owed and the remediation
        # names which tickets owe it; extracting none means the remediation
        # wording moved and this mint has silently stopped working, which is
        # indistinguishable from "nothing was owed" unless it is an error.
        print(
            "mint_occ_self_bind: ERROR -- the eligibility verdict is "
            "missing_occ_self_bind but no ticket id could be read out of its "
            "remediation text. The remediation wording changed and this "
            "script's extraction is stale (OMN-18921).",
            file=sys.stderr,
        )
        print(f"detail was:\n{result.detail}", file=sys.stderr)
        return 2

    return _probe_and_mint(args, repo_root, tickets)


def _refuse_indeterminate_probe(probe_stdout: str, exit_code: int) -> int | None:
    """Return an exit code when the probe cannot honestly back a PASS."""
    if exit_code != 0:
        print(
            "mint_occ_self_bind: ERROR -- the declared check did not succeed "
            f"(exit {exit_code}); refusing to write a PASS receipt for a "
            "command that failed (OMN-18921).",
            file=sys.stderr,
        )
        return 3
    if not probe_stdout.strip():
        # A zero exit with no output is indeterminate, and a receipt whose
        # probe_stdout is empty is indistinguishable from a probe that never
        # ran -- which the gate rejects as a non-PASS receipt anyway. Writing
        # one is worse than writing none: it looks handled and is not.
        print(
            "mint_occ_self_bind: ERROR -- the declared check exited 0 but "
            "produced no output, so its result is indeterminate; refusing to "
            "write a receipt that cannot be told apart from an unrun probe "
            "(OMN-18921).",
            file=sys.stderr,
        )
        return 3
    return None


def _probe_and_mint(
    args: argparse.Namespace, repo_root: Path, tickets: tuple[str, ...]
) -> int:
    """Run the declared check once, then write each owed self-bind."""
    probe_command, probe_stdout, exit_code = run_probe(
        repo=args.repo, pr_number=args.pr_number
    )
    refusal = _refuse_indeterminate_probe(probe_stdout, exit_code)
    if refusal is not None:
        return refusal

    written: list[Path] = []
    pending: list[Path] = []
    for ticket_id in tickets:
        path = self_bind_path(repo_root, ticket_id, args.pr_number)
        if path.is_file():
            print(f"mint_occ_self_bind: {path} already exists -- left untouched")
            continue
        contract_path = repo_root / "contracts" / f"{ticket_id}.yaml"
        if not contract_path.is_file():
            print(
                f"mint_occ_self_bind: ERROR -- {contract_path} does not exist, "
                "so no contract hash can bind the self-bind to it.",
                file=sys.stderr,
            )
            return 4
        rendered = render_self_bind(
            ticket_id=ticket_id,
            pr_number=args.pr_number,
            repo=args.repo,
            contract_sha256=f"sha256:{compute_contract_sha256(contract_path)}",
            commit_sha=args.commit_sha,
            branch=args.branch,
            run_timestamp=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            probe_command=probe_command,
            probe_stdout=probe_stdout,
            exit_code=exit_code,
        )
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
            written.append(path)
        else:
            pending.append(path)
        print(
            f"mint_occ_self_bind: {'wrote' if args.write else 'would write'} "
            f"{path.relative_to(repo_root)}"
        )

    if not written:
        if pending:
            print(
                f"mint_occ_self_bind: {len(pending)} self-bind(s) owed; re-run "
                "with --write to mint them"
            )
        else:
            print("mint_occ_self_bind: every owed self-bind already exists")
        return 0

    print(
        json.dumps(
            {
                "minted": [str(p.relative_to(repo_root)) for p in written],
                "tickets": list(tickets),
                "pr_number": args.pr_number,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
