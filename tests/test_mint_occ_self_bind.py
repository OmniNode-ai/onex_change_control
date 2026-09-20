# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-18921 — the minted self-bind must satisfy the gate that demanded it.

WHAT THIS SUITE IS FOR. ``scripts/ci/mint_occ_self_bind.py`` exists to remove a
CI round: a hand-authored evidence companion owes a SECOND, structural artifact
at ``drift/occ_bindings/<ticket>/occ-self-bind-pr-<n>/command.yaml``, nothing
says so until ``occ-preflight / eligibility`` has already failed with
``missing_occ_self_bind``, and the author pays a full check run to learn it.

THE LOAD-BEARING TEST IS THE ROUND TRIP, NOT THE FILE EXISTING. A mint that
writes a plausible YAML at the right path and does not flip the verdict removes
nothing — the author still sees a red preflight, one push later than before.
:func:`test_the_minted_self_bind_satisfies_the_gate_that_demanded_it` therefore
runs :func:`validate_occ_merge_eligibility` on the same tree BEFORE and AFTER
the mint, and the "after" assertion is the whole ticket. The "before"
assertion is its positive control: a fixture that is refused for some other
reason would make every later assertion in this module vacuously true, so the
control is a named test of its own rather than a comment.

NO HASH IN THIS FILE IS HAND-TYPED. Every ``contract_sha256`` and
``contract_entry_sha256`` in the fixture is computed with the same
``compute_contract_sha256`` / ``compute_contract_entry_sha256`` the gate
recomputes with. A transcribed digest would make the fixture a re-statement of
the gate's arithmetic rather than a test of it, and would rot silently the
first time the fixture contract's bytes changed.

NO TEST HERE TOUCHES THE NETWORK OR NEEDS ``gh``. The declared check is a real
``gh pr view`` and the script really runs it; the tests stub it. Most stub
:func:`mint_occ_self_bind.run_probe` outright, because they are about the
verdict and not about the probe. ONE —
:func:`test_the_probe_argv_is_the_declared_check_and_its_stdout_is_recorded` —
stubs at the ``subprocess.run`` level instead, so the argv the script actually
builds is exercised rather than bypassed everywhere.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from omnibase_core.enums.enum_occ_eligibility_reason import EnumOccEligibilityReason
from omnibase_core.models.contracts.ticket.model_dod_receipt import ModelDodReceipt
from omnibase_core.models.validation.model_occ_eligibility_input import (
    ModelOccEligibilityInput,
)
from omnibase_core.validation.validator_occ_merge_eligibility import (
    validate_occ_merge_eligibility,
)
from omnibase_core.validation.validator_receipt_gate import (
    compute_contract_entry_sha256,
    compute_contract_sha256,
)

from scripts.ci import mint_occ_self_bind
from scripts.ci.mint_occ_self_bind import (
    main,
    self_bind_evidence_id,
    self_bind_path,
    unbound_tickets_from_detail,
)

if TYPE_CHECKING:
    from omnibase_core.models.validation.model_occ_eligibility_result import (
        ModelOccEligibilityResult,
    )

pytestmark = pytest.mark.unit

# The canonical OCC repo name, because ``_is_occ_repo`` matches it exactly and
# the whole MISSING_OCC_SELF_BIND branch is unreachable for any other repo.
OCC_REPO = "OmniNode-ai/onex_change_control"

TICKET_A = "OMN-99001"
TICKET_B = "OMN-99002"
PR_NUMBER = 4242

# 40-hex, distinct on purpose. The declared receipt's commit_sha must NOT be one
# of the PR's commit shas: ``_receipt_bound_to_pr`` accepts either a matching
# pr_number or a matching commit sha, and a declared receipt that already binds
# to this PR owes no self-bind at all.
OCC_HEAD_SHA = "a" * 40
PR_COMMIT_SHA = "c" * 40
DECLARED_RECEIPT_SHA = "b" * 40
DECLARED_RECEIPT_PR = 1

PROBE_STDOUT = '{"headRefName":"fixture/branch","number":4242,"state":"OPEN"}\n'

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1] / ".github/workflows/occ-self-bind-mint.yml"
)


@dataclass(frozen=True)
class OccTree:
    """A temporary OCC evidence tree plus the PR snapshot that evaluates it.

    Holds the CLI arguments and the eligibility snapshot side by side so the
    test's own control call and the script's internal call cannot drift apart:
    both are derived here, from one set of values.
    """

    root: Path
    tickets: tuple[str, ...]
    pr_number: int = PR_NUMBER
    branch: str = "fixture/omn-99001-99002-companion"
    extra_argv: tuple[str, ...] = field(default=())
    #: Commit messages on the PR. occ-preflight passes one per commit and
    #: the validator searches them for the ticket token, so a snapshot that
    #: omits them can reach a DIFFERENT verdict than CI reaches.
    commit_texts: tuple[str, ...] = field(default=())
    #: Overrides the ticket-citing title, so a test can build the case where
    #: only a commit message binds the ticket.
    title_override: str | None = None

    @property
    def title(self) -> str:
        if self.title_override is not None:
            return self.title_override
        cited = ", ".join(self.tickets)
        return f"evidence({cited}): fixture companion"

    @property
    def body(self) -> str:
        return "\n".join(f"Closes: {ticket}" for ticket in self.tickets)

    def snapshot(self) -> ModelOccEligibilityInput:
        return ModelOccEligibilityInput(
            repo=OCC_REPO,
            pr_number=self.pr_number,
            pr_title=self.title,
            pr_body=self.body,
            pr_branch=self.branch,
            pr_commit_shas=(PR_COMMIT_SHA,),
            pr_commit_texts=self.commit_texts,
            occ_commit_sha=OCC_HEAD_SHA,
            contracts_dir=self.root / "contracts",
            receipts_dir=self.root / "drift" / "dod_receipts",
        )

    def verdict(self) -> ModelOccEligibilityResult:
        return validate_occ_merge_eligibility(self.snapshot())

    def argv(self, *, write: bool) -> list[str]:
        argv = [
            "--repo",
            OCC_REPO,
            "--pr-number",
            str(self.pr_number),
            "--commit-sha",
            OCC_HEAD_SHA,
            "--branch",
            self.branch,
            "--pr-title",
            self.title,
            "--pr-body",
            self.body,
            "--pr-commit-sha",
            PR_COMMIT_SHA,
            "--repo-root",
            str(self.root),
        ]
        for text in self.commit_texts:
            argv += ["--pr-commit-text", text]
        if write:
            argv.append("--write")
        return [*argv, *self.extra_argv]

    def self_bind(self, ticket: str) -> Path:
        return self_bind_path(self.root, ticket, self.pr_number)

    @property
    def structural_dir(self) -> Path:
        return self.root / "drift" / "occ_bindings"

    def minted_files(self) -> list[Path]:
        if not self.structural_dir.is_dir():
            return []
        return sorted(p for p in self.structural_dir.rglob("*") if p.is_file())


def _write_contract(root: Path, ticket: str) -> Path:
    """Write a valid ticket contract declaring exactly one dod_evidence item."""
    path = root / "contracts" / f"{ticket}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            f"""\
            ---
            schema_version: "1.0.0"
            ticket_id: "{ticket}"
            title: "evidence({ticket}): fixture contract"
            summary: >
              Fixture contract for the OMN-18921 mint suite. One declared
              dod_evidence item, satisfied by one PASS receipt, so the only
              defect the gate can find is the absent structural self-bind.
            is_seam_ticket: false
            interface_change: false
            interfaces_touched: []
            dod_evidence:
              - id: "dod-001"
                description: "The declared probe ran and passed."
                source: "manual"
                checks:
                  - check_type: "command"
                    check_value: "echo declared-probe"
            """
        ),
        encoding="utf-8",
    )
    return path


def _write_declared_receipt(root: Path, ticket: str) -> Path:
    """Write the PASS receipt for the contract's one declared evidence item.

    Both contract hashes are computed, never transcribed. ``pr_number`` and
    ``commit_sha`` deliberately point away from the PR under evaluation, which
    is what leaves the ticket unbound and the self-bind owed.
    """
    contract_path = root / "contracts" / f"{ticket}.yaml"
    contract_data = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    whole_file = f"sha256:{compute_contract_sha256(contract_path)}"
    per_entry = compute_contract_entry_sha256(contract_data, "dod-001")

    path = root / "drift" / "dod_receipts" / ticket / "dod-001" / "command.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            f"""\
            ---
            schema_version: "1.0.0"
            ticket_id: "{ticket}"
            evidence_item_id: "dod-001"
            check_type: "command"
            check_value: "echo declared-probe"
            contract_sha256: "{whole_file}"
            contract_entry_sha256: "{per_entry}"
            status: PASS
            run_timestamp: "2026-09-20T00:00:00Z"
            commit_sha: "{DECLARED_RECEIPT_SHA}"
            runner: "fixture-runner"
            verifier: "fixture-verifier"
            probe_command: "echo declared-probe"
            probe_stdout: |
              declared-probe
            exit_code: 0
            pr_number: {DECLARED_RECEIPT_PR}
            branch: "fixture/prior-branch"
            """
        ),
        encoding="utf-8",
    )
    return path


def _build_tree(tmp_path: Path, *tickets: str) -> OccTree:
    """Build an OCC tree whose sole defect is the absent structural self-bind."""
    root = tmp_path / "occ"
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    for ticket in tickets:
        _write_contract(root, ticket)
        _write_declared_receipt(root, ticket)
    return OccTree(root=root, tickets=tickets)


def _stub_probe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exit_code: int = 0,
    stdout: str = PROBE_STDOUT,
) -> list[tuple[str, int]]:
    """Replace ``run_probe`` and return the list it records its calls into."""
    calls: list[tuple[str, int]] = []

    def _fake(*, repo: str, pr_number: int) -> tuple[str, str, int]:
        calls.append((repo, pr_number))
        command = mint_occ_self_bind.build_check_value(repo=repo, pr_number=pr_number)
        return command, stdout, exit_code

    monkeypatch.setattr(mint_occ_self_bind, "run_probe", _fake)
    return calls


def _load_receipt(path: Path) -> ModelDodReceipt:
    return ModelDodReceipt.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )


# ---------------------------------------------------------------------------
# 1. The positive control, then the round trip it makes non-vacuous.
# ---------------------------------------------------------------------------


def test_the_fixture_is_refused_for_exactly_the_reason_the_mint_exists_for(
    tmp_path: Path,
) -> None:
    """POSITIVE CONTROL. The tree must be refused ``missing_occ_self_bind``.

    Every other test in this module asserts something about what the mint does
    to a tree carrying that verdict. If this fixture were refused for a
    different reason — a missing receipt, a stale hash — the mint would
    correctly write nothing and the "nothing was written" assertions elsewhere
    would pass for the wrong reason. This test is the guard against a suite
    that is green and measuring nothing.
    """
    tree = _build_tree(tmp_path, TICKET_A)
    verdict = tree.verdict()

    assert verdict.eligible is False
    assert verdict.reason is EnumOccEligibilityReason.MISSING_OCC_SELF_BIND, (
        f"fixture is refused for {verdict.reason.value}, not "
        f"missing_occ_self_bind; detail: {verdict.detail}"
    )


def test_the_minted_self_bind_satisfies_the_gate_that_demanded_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE TICKET. Mint, then re-run the gate: the refusal is gone.

    A mint that writes the artifact but does not move the verdict removes no
    CI round at all. The before/after pair around one unchanged tree is the
    only assertion that can tell those two outcomes apart.
    """
    tree = _build_tree(tmp_path, TICKET_A)
    before = tree.verdict()
    assert before.reason is EnumOccEligibilityReason.MISSING_OCC_SELF_BIND

    _stub_probe(monkeypatch)
    assert main(tree.argv(write=True)) == 0

    minted = tree.self_bind(TICKET_A)
    assert minted.is_file(), f"expected the mint at {minted}"
    assert minted == (
        tree.root
        / "drift"
        / "occ_bindings"
        / TICKET_A
        / f"occ-self-bind-pr-{PR_NUMBER}"
        / "command.yaml"
    )

    after = tree.verdict()
    assert after.reason is not EnumOccEligibilityReason.MISSING_OCC_SELF_BIND, (
        "the artifact was written but the gate still refuses for the same "
        f"reason, so the mint removed nothing; detail: {after.detail}"
    )
    # The fixture is built so that the self-bind is the ONLY outstanding
    # defect, so the post-mint verdict is fully eligible rather than merely
    # differently refused.
    assert after.reason is EnumOccEligibilityReason.ELIGIBLE, after.detail
    assert after.eligible is True

    # PATH-AGNOSTIC BY CONSTRUCTION, and then pinned against the other tree.
    #
    # The before/after pair above asks the GATE, not a directory, so it holds
    # whichever of the two accepted self-bind shapes the mint wrote. That is
    # the right falsifier -- but alone it would also pass if the mint wrote
    # BOTH, which is the one outcome that widens the split this repository
    # already carries: a DECLARED tree of thousands of occ-self-bind receipts
    # under drift/dod_receipts whose ids ARE listed in their contracts, beside
    # a structural tree of ~184 under drift/occ_bindings whose ids
    # deliberately are not. Both are accepted, on two different reader
    # branches of the validator. So assert the declared tree did not grow.
    declared_self_binds = sorted(
        (tree.root / "drift" / "dod_receipts").rglob("occ-self-bind-pr-*")
    )
    assert declared_self_binds == [], (
        "the mint wrote into the DECLARED self-bind tree as well, which widens "
        f"the two-shape split rather than using one of them: {declared_self_binds}"
    )


# ---------------------------------------------------------------------------
# 2. The OMN-18075 shape.
# ---------------------------------------------------------------------------


def test_the_minted_artifact_carries_the_omn_18075_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All four OMN-18075 properties, each asserted on its own.

    The structural binding is provenance, not product DoD evidence: it lives
    outside the receipt-hardening gate's tree, is undeclared in the contract,
    binds no acceptance criterion, and has no contract entry to hash.
    """
    tree = _build_tree(tmp_path, TICKET_A)
    _stub_probe(monkeypatch)
    assert main(tree.argv(write=True)) == 0

    minted = tree.self_bind(TICKET_A)
    relative = minted.relative_to(tree.root)
    raw = yaml.safe_load(minted.read_text(encoding="utf-8"))

    # (i) under drift/occ_bindings/ ...
    assert relative.parts[:2] == ("drift", "occ_bindings")
    # ... and NOT under drift/dod_receipts/, the hardening gate's scope.
    assert "dod_receipts" not in relative.parts

    # (ii) its evidence id is absent from the contract's dod_evidence.
    contract = yaml.safe_load(
        (tree.root / "contracts" / f"{TICKET_A}.yaml").read_text(encoding="utf-8")
    )
    declared_ids = {item["id"] for item in contract["dod_evidence"]}
    assert self_bind_evidence_id(PR_NUMBER) not in declared_ids
    assert raw["evidence_item_id"] == self_bind_evidence_id(PR_NUMBER)

    # (iii) no binds_ac.
    assert "binds_ac" not in raw

    # (iv) no contract_entry_sha256 — an undeclared item has no entry to hash —
    # and the whole-file contract_sha256 instead, which is what the validator's
    # structural branch binds on.
    assert "contract_entry_sha256" not in raw
    contract_path = tree.root / "contracts" / f"{TICKET_A}.yaml"
    assert raw["contract_sha256"] == f"sha256:{compute_contract_sha256(contract_path)}"
    assert raw["pr_number"] == PR_NUMBER

    # ModelDodReceipt is extra="forbid", so a stray key fails here rather than
    # downstream in CI.
    receipt = _load_receipt(minted)
    assert receipt.contract_entry_sha256 is None
    assert receipt.ticket_id == TICKET_A
    assert receipt.check_type == "command"
    assert receipt.status.value == "PASS"


# ---------------------------------------------------------------------------
# 3. Only the one verdict mints.
# ---------------------------------------------------------------------------


def test_an_already_bound_tree_mints_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tree whose self-bind already exists is not MISSING_OCC_SELF_BIND."""
    tree = _build_tree(tmp_path, TICKET_A)
    _stub_probe(monkeypatch)
    assert main(tree.argv(write=True)) == 0
    assert tree.verdict().reason is EnumOccEligibilityReason.ELIGIBLE
    before = tree.minted_files()

    calls = _stub_probe(monkeypatch, stdout='{"second":"run"}\n')
    assert main(tree.argv(write=True)) == 0
    assert tree.minted_files() == before
    # The short circuit is on the VERDICT, ahead of the probe: reaching the
    # probe at all would mean the script re-derived "is a self-bind owed".
    assert calls == []


def test_a_different_failure_verdict_mints_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tree refused for another reason writes nothing and exits 0.

    The declared receipt is removed, so the verdict is ``missing_receipt``.
    Minting a self-bind there would paper over a real absence.
    """
    tree = _build_tree(tmp_path, TICKET_A)
    (
        tree.root / "drift" / "dod_receipts" / TICKET_A / "dod-001" / "command.yaml"
    ).unlink()

    verdict = tree.verdict()
    assert verdict.reason is EnumOccEligibilityReason.MISSING_RECEIPT

    calls = _stub_probe(monkeypatch)
    assert main(tree.argv(write=True)) == 0
    assert tree.minted_files() == []
    assert calls == []


# ---------------------------------------------------------------------------
# 4. Idempotence.
# ---------------------------------------------------------------------------


def test_a_second_run_leaves_the_artifact_byte_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running twice does not rewrite the artifact.

    The second run's probe stub returns DIFFERENT stdout on purpose: a rewrite
    would change the bytes even inside the one-second window where the
    ``run_timestamp`` would not, so the byte comparison is load-bearing rather
    than accidentally true.
    """
    tree = _build_tree(tmp_path, TICKET_A)
    _stub_probe(monkeypatch)
    assert main(tree.argv(write=True)) == 0

    minted = tree.self_bind(TICKET_A)
    first = minted.read_bytes()

    _stub_probe(monkeypatch, stdout='{"different":"stdout"}\n')
    assert main(tree.argv(write=True)) == 0

    assert minted.read_bytes() == first
    assert tree.minted_files() == [minted]


# ---------------------------------------------------------------------------
# 5. A failing probe never produces a PASS.
# ---------------------------------------------------------------------------


def test_a_failing_probe_never_produces_a_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 3, nothing written. A PASS behind a failed command is fabricated.

    This artifact's whole job is proving a binding, so a receipt asserting a
    check succeeded when it did not is the false-evidence class the receipt
    honesty gate exists to catch, on the worst possible surface.
    """
    tree = _build_tree(tmp_path, TICKET_A)
    _stub_probe(monkeypatch, exit_code=1, stdout="")

    assert main(tree.argv(write=True)) == 3
    assert tree.minted_files() == []
    assert tree.verdict().reason is EnumOccEligibilityReason.MISSING_OCC_SELF_BIND


# ---------------------------------------------------------------------------
# 6. Extraction round-trips against the REAL remediation text.
# ---------------------------------------------------------------------------


def test_the_extraction_round_trips_against_the_real_remediation(
    tmp_path: Path,
) -> None:
    """The ticket ids come out of the validator's OWN remediation string.

    The remediation text is not reproduced here. It is obtained from a live
    ``validate_occ_merge_eligibility`` call, so this test goes red the day the
    wording moves — which is the day the mint would otherwise start silently
    extracting nothing and reporting success.
    """
    single = _build_tree(tmp_path / "one", TICKET_A)
    detail = single.verdict().detail
    assert detail is not None
    assert unbound_tickets_from_detail(detail) == (TICKET_A,)

    both = _build_tree(tmp_path / "two", TICKET_A, TICKET_B)
    assert both.verdict().reason is EnumOccEligibilityReason.MISSING_OCC_SELF_BIND
    multi_detail = both.verdict().detail
    assert multi_detail is not None
    assert unbound_tickets_from_detail(multi_detail) == (TICKET_A, TICKET_B)


def test_the_extraction_returns_nothing_for_text_without_the_path_token() -> None:
    """NEGATIVE CONTROL for the extraction.

    Prose that names the tickets but not the structural path must yield no
    tickets, so a match in the test above is a match on the path token the
    regex is actually keyed to rather than on any stray ``OMN-`` in the text.
    """
    assert unbound_tickets_from_detail("") == ()
    assert (
        unbound_tickets_from_detail(
            f"{TICKET_A} and {TICKET_B} are cited but not bound; see "
            "drift/dod_receipts/OMN-99001/dod-001/command.yaml"
        )
        == ()
    )


# ---------------------------------------------------------------------------
# 7. The loud-failure path.
# ---------------------------------------------------------------------------


def test_an_unreadable_remediation_fails_loud_instead_of_reporting_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 2 when the verdict owes a self-bind but no ticket can be read.

    "Extracted nothing" and "nothing was owed" produce the same empty write
    set. Only the exit code can tell an author that the mint has stopped
    working, so a silent 0 here is the failure mode this test forbids.
    """
    tree = _build_tree(tmp_path, TICKET_A)
    calls = _stub_probe(monkeypatch)
    monkeypatch.setattr(
        mint_occ_self_bind,
        "unbound_tickets_from_detail",
        lambda _detail: (),
    )

    assert main(tree.argv(write=True)) == 2
    assert tree.minted_files() == []
    # Refused before the probe ran: there is nothing to probe FOR.
    assert calls == []


# ---------------------------------------------------------------------------
# 8. Multi-ticket.
# ---------------------------------------------------------------------------


def test_two_owed_tickets_both_mint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PR citing two tickets that both owe a self-bind mints both.

    The serial-repair chain this script exists to stop is precisely what
    happens when a remedy handles one owed ticket per CI round.
    """
    tree = _build_tree(tmp_path, TICKET_A, TICKET_B)
    assert tree.verdict().reason is EnumOccEligibilityReason.MISSING_OCC_SELF_BIND

    _stub_probe(monkeypatch)
    assert main(tree.argv(write=True)) == 0

    assert tree.self_bind(TICKET_A).is_file()
    assert tree.self_bind(TICKET_B).is_file()
    assert tree.minted_files() == sorted(
        [tree.self_bind(TICKET_A), tree.self_bind(TICKET_B)]
    )

    after = tree.verdict()
    assert after.reason is EnumOccEligibilityReason.ELIGIBLE, after.detail
    assert after.eligible is True


# ---------------------------------------------------------------------------
# The probe itself: real argv, stubbed only at the process boundary.
# ---------------------------------------------------------------------------


def test_the_probe_argv_is_the_declared_check_and_its_stdout_is_recorded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub at ``subprocess.run``, not at ``run_probe``.

    Every other test replaces ``run_probe`` wholesale, which would let a wrong
    argv — a missing ``--repo``, a stringified PR number in the wrong slot —
    pass unnoticed everywhere. This one exercises the real construction and
    asserts the recorded ``probe_command`` and ``probe_stdout`` are the command
    that ran and the output it produced, not a description of them.
    """
    tree = _build_tree(tmp_path, TICKET_A)
    recorded: list[list[str]] = []

    def _fake_run(
        argv: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        recorded.append(list(argv))
        return subprocess.CompletedProcess(
            args=list(argv), returncode=0, stdout=PROBE_STDOUT, stderr=""
        )

    # Patched on the stdlib modules the script imported, not on the script:
    # it calls ``shutil.which`` / ``subprocess.run`` through those module
    # objects, so this is the same seam without asserting a re-export the
    # script never declared.
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(subprocess, "run", _fake_run)

    assert main(tree.argv(write=True)) == 0

    assert recorded == [
        [
            "/usr/bin/gh",
            "pr",
            "view",
            str(PR_NUMBER),
            "--repo",
            OCC_REPO,
            "--json",
            "number,state,headRefName",
        ]
    ]

    receipt = _load_receipt(tree.self_bind(TICKET_A))
    assert receipt.probe_command == (
        f"gh pr view {PR_NUMBER} --repo {OCC_REPO} --json number,state,headRefName"
    )
    assert receipt.check_value == receipt.probe_command
    assert PROBE_STDOUT.strip() in receipt.probe_stdout
    assert receipt.exit_code == 0


def test_an_absent_gh_is_a_loud_failure_not_a_silent_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``gh`` on PATH raises rather than writing an unearned PASS."""
    tree = _build_tree(tmp_path, TICKET_A)
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="gh is not on PATH"):
        main(tree.argv(write=True))
    assert tree.minted_files() == []


# ---------------------------------------------------------------------------
# The workflow: two properties, both load-bearing.
# ---------------------------------------------------------------------------


def test_the_workflow_pushes_as_an_identity_whose_push_triggers_ci() -> None:
    """The token must carry no ``github.token`` fallback and the checkout must
    not persist credentials.

    WHY BOTH ARE LOAD-BEARING, and why this is a test rather than a comment.
    The mint's value is that the commit it pushes fires a fresh
    ``pull_request`` event, which re-runs the eligibility check, which then
    passes — the author never sees a red preflight. A push authenticated as the
    job's own ``GITHUB_TOKEN`` triggers NO workflow runs, so with either
    property wrong the mint would land a commit that never re-runs the gate and
    the author would still be looking at a red ``occ-preflight / eligibility``,
    one push later than before. Both failure shapes are invisible from outside:
    ``actions/checkout`` persists a ``GITHUB_TOKEN`` extraheader that OVERRIDES
    any credential in the push URL, and a ``|| github.token`` fallback silently
    substitutes the suppressed identity exactly when the app-token mint failed
    (OMN-18273).
    """
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = yaml.safe_load(raw)
    steps = workflow["jobs"]["mint"]["steps"]

    checkouts = [
        step
        for step in steps
        if isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/checkout")
    ]
    assert checkouts, "the mint job must check the pull request head out"
    for step in checkouts:
        assert step.get("with", {}).get("persist-credentials") is False, (
            "a persisted GITHUB_TOKEN extraheader overrides the push URL's "
            "credential, so the push would be suppressed and no CI would re-run"
        )

    token_envs = [
        step["env"]["GH_TOKEN"]
        for step in steps
        if isinstance(step.get("env"), dict) and "GH_TOKEN" in step["env"]
    ]
    assert token_envs, "the mint and push steps must be given a token"
    for expression in token_envs:
        assert expression.strip() == "${{ steps.app-token.outputs.token }}", expression
        assert "||" not in expression

    # Belt and braces over every EXECUTABLE line: no step may reach for the
    # suppressed identity under any spelling. Comment lines are excluded on
    # purpose — the workflow's own header explains why the fallback is absent,
    # and a scan that cannot tell an expression from prose about that
    # expression is the substring-matching failure of Operating Rule 15.
    executable = "\n".join(
        line for line in raw.splitlines() if not line.lstrip().startswith("#")
    )
    assert "github.token" not in executable
    assert "secrets.GITHUB_TOKEN" not in executable


# ---------------------------------------------------------------------------
# The snapshot must be the one occ-preflight builds, field for field.
# ---------------------------------------------------------------------------


def test_a_ticket_bound_only_by_a_commit_message_still_mints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mint's snapshot must match the one occ-preflight feeds the gate.

    This script's safety property is that it mints on the gate's OWN verdict
    rather than on a second derivation of "is a self-bind owed". That holds
    only while it hands the gate the same snapshot CI hands it.
    ``call-occ-preflight.yml`` loops over every commit and passes both a sha
    and a message per commit, and ``_ticket_bound_to_pr`` searches those
    messages as well as the title and the branch.

    So a companion whose ticket is cited ONLY in a commit message is the case
    that separates the two snapshots, and it is a case the mint must handle:
    getting it wrong means exiting 0 reporting success while the author still
    pays the CI round this script exists to remove.
    """
    tree = _build_tree(tmp_path, TICKET_A)
    # The ticket must be absent from the title AND the branch: the validator
    # searches both, so leaving the token in either would bind the ticket by
    # another route and the commit message would prove nothing.
    bound_by_commit = replace(
        tree,
        title_override="evidence: fixture companion with no ticket in the title",
        branch="fixture/companion-with-no-ticket-in-the-branch",
        commit_texts=(f"evidence({TICKET_A}): the commit that cites it",),
    )
    assert (
        bound_by_commit.verdict().reason
        is EnumOccEligibilityReason.MISSING_OCC_SELF_BIND
    )

    _stub_probe(monkeypatch)
    assert main(bound_by_commit.argv(write=True)) == 0
    assert bound_by_commit.self_bind(TICKET_A).is_file()
    assert bound_by_commit.verdict().reason is EnumOccEligibilityReason.ELIGIBLE


def test_dropping_the_commit_texts_reaches_a_different_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The measurement behind the test above, kept executable.

    Same tree, same PR, commit messages withheld: the verdict is
    ``pr_ticket_mismatch``, not ``missing_occ_self_bind``, so the mint
    correctly declines and writes nothing. That is the right behaviour for
    this snapshot and the WRONG answer for the pull request, which is why the
    workflow must enumerate every commit rather than passing the head sha
    alone. Pinned so a later simplification of the workflow's commit
    enumeration fails here instead of silently reintroducing the no-op.
    """
    tree = _build_tree(tmp_path, TICKET_A)
    unbound = replace(
        tree,
        title_override="evidence: fixture companion with no ticket in the title",
        branch="fixture/companion-with-no-ticket-in-the-branch",
        commit_texts=(),
    )
    assert unbound.verdict().reason is EnumOccEligibilityReason.PR_TICKET_MISMATCH

    _stub_probe(monkeypatch)
    assert main(unbound.argv(write=True)) == 0
    assert unbound.minted_files() == []


def test_the_workflow_enumerates_every_commit_not_just_the_head() -> None:
    """The workflow half of the same property.

    The script gained ``--pr-commit-text`` for the case above; it only helps
    if the workflow actually passes one per commit. Asserting on the workflow
    text is weaker than asserting on behaviour, but the alternative is no
    check at all on the half that lives in YAML.
    """
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "--pr-commit-text" in raw
    assert "--pr-commit-sha" in raw
    assert ".commits[].oid" in raw
    assert ".commits[].messageHeadline" in raw


# ---------------------------------------------------------------------------
# An indeterminate probe is not a pass.
# ---------------------------------------------------------------------------


def test_a_probe_that_exits_zero_with_no_output_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A receipt with empty ``probe_stdout`` cannot be told from an unrun one.

    The gate refuses such a receipt as non-PASS, so writing it is strictly
    worse than writing none: the companion looks handled, the artifact is
    present, and the pull request is still blocked -- with the extra cost that
    the next reader has to work out why a minted file did not help.
    """
    tree = _build_tree(tmp_path, TICKET_A)
    _stub_probe(monkeypatch, exit_code=0, stdout="   \n")
    assert main(tree.argv(write=True)) == 3
    assert tree.minted_files() == []
