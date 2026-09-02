# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Focused red/green controls for the OMN-17495 receipt-honesty ledger."""

from __future__ import annotations

import copy
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
import yaml

from onex_change_control.validation import receipt_honesty_ratchet as ratchet

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NONREGULAR_SUBPROCESS_TIMEOUT_SECONDS = 20


def _document(findings: list[dict[str, str]]) -> bytes:
    return yaml.safe_dump(
        {
            "schema_version": 1,
            "origin_commit": ratchet._ORIGIN_COMMIT,
            "bootstrap_base_commit": ratchet._BOOTSTRAP_BASE_COMMIT,
            "seed_finding_count": ratchet._SEED_FINDING_COUNT,
            "seed_receipt_path_count": ratchet._SEED_RECEIPT_PATH_COUNT,
            "scanner_limitation": ratchet._SCANNER_LIMITATION,
            "findings": findings,
        },
        sort_keys=False,
    ).encode()


def _identity(
    *,
    path: str = "drift/dod_receipts/OMN-1/dod-001/command.yaml",
    rule: str = "NO_OP_PROBE",
    sha256: str = "a" * 64,
    blob_oid: str = "b" * 40,
) -> Any:
    return ratchet.FindingIdentity(path, rule, sha256, blob_oid)


@pytest.mark.unit
def test_live_baseline_is_exact_seed_census_and_deterministic() -> None:
    baseline = ratchet.load_baseline(_REPO_ROOT)
    assert len(baseline.findings) == 1142
    assert len({item.path for item in baseline.findings}) == 1055
    assert list(baseline.findings) == sorted(baseline.findings)


@pytest.mark.unit
def test_unknown_root_key_and_traversal_path_fail_closed() -> None:
    raw = _document([_identity(path="drift/dod_receipts/../escape.yaml").as_mapping()])
    with pytest.raises(ratchet.RatchetError, match="traverses"):
        ratchet.parse_baseline(raw)

    document = yaml.safe_load(_document([]))
    document["untrusted"] = "no"
    with pytest.raises(ratchet.RatchetError, match="root keys"):
        ratchet.parse_baseline(yaml.safe_dump(document).encode())


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "./drift/dod_receipts/OMN-1/dod-001/command.yaml",
        "drift//dod_receipts/OMN-1/dod-001/command.yaml",
    ],
)
def test_noncanonical_dot_and_double_slash_paths_fail_closed(path: str) -> None:
    raw = _document([_identity(path=path).as_mapping()])
    with pytest.raises(ratchet.RatchetError, match="exact canonical"):
        ratchet.parse_baseline(raw)


@pytest.mark.unit
def test_duplicate_and_malformed_hash_or_oid_fail_closed() -> None:
    entry = _identity().as_mapping()
    duplicate = _document([entry, entry])
    with pytest.raises(ratchet.RatchetError, match="duplicate"):
        ratchet.parse_baseline(duplicate)

    bad_hash = _identity(sha256="not-a-sha").as_mapping()
    with pytest.raises(ratchet.RatchetError, match="SHA-256"):
        ratchet.parse_baseline(_document([bad_hash]))

    bad_oid = _identity(blob_oid="not-an-oid").as_mapping()
    with pytest.raises(ratchet.RatchetError, match="blob OID"):
        ratchet.parse_baseline(_document([bad_oid]))


@pytest.mark.unit
def test_baseline_must_be_regular_file_not_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.yaml"
    target.write_bytes(_document([]))
    baseline = tmp_path / ".onex_ratchets" / "omn_17495_receipt_honesty_baseline.yaml"
    baseline.parent.mkdir()
    baseline.symlink_to(target)
    with pytest.raises(ratchet.RatchetError, match="regular non-symlink"):
        ratchet.load_baseline(tmp_path)


@pytest.mark.unit
def test_duplicate_yaml_key_is_rejected_not_last_value_wins() -> None:
    raw = (
        b"schema_version: 1\n"
        b"origin_commit: 65a2adbba8a3c4f6cc57c1c7250480bfb708fac0\n"
        b"bootstrap_base_commit: b2293819e69a3bf4b58107bd2b951f3a45cc377f\n"
        b"seed_finding_count: 1142\n"
        b"seed_receipt_path_count: 1055\n"
        b"scanner_limitation: The locked core scanner includes "
        b"superseded receipt bases. "
        b"This ledger is a truthful persistent-debt migration control, not a burn-down "
        b"claim; active-supersession scanner work is follow-up.\n"
        b"findings: []\n"
        b"findings: []\n"
    )
    with pytest.raises(ratchet.RatchetError, match="duplicate YAML key"):
        ratchet.parse_baseline(raw)


@pytest.mark.unit
def test_live_set_equality_reports_new_and_stale_for_one_byte_change() -> None:
    old = _identity(sha256="a" * 64, blob_oid="b" * 40)
    changed = _identity(sha256="c" * 64, blob_oid="d" * 40)
    baseline = ratchet.Baseline((old,))
    with pytest.raises(ratchet.RatchetError) as raised:
        ratchet._assert_live_equals_baseline(frozenset({changed}), baseline)
    message = str(raised.value)
    assert "new or modified" in message
    assert "stale ledger" in message


@pytest.mark.unit
def test_copy_new_rule_repair_and_reintroduction_all_fail_identity_equality() -> None:
    original = _identity()
    baseline = ratchet.Baseline((original,))
    copied = _identity(path="drift/dod_receipts/OMN-2/dod-001/command.yaml")
    added_rule = _identity(rule="PENDING_IN_PASS")

    with pytest.raises(ratchet.RatchetError, match="new or modified"):
        ratchet._assert_live_equals_baseline(frozenset({original, copied}), baseline)
    with pytest.raises(ratchet.RatchetError, match="new or modified"):
        ratchet._assert_live_equals_baseline(
            frozenset({original, added_rule}), baseline
        )
    with pytest.raises(ratchet.RatchetError, match="stale ledger"):
        ratchet._assert_live_equals_baseline(frozenset(), baseline)
    # A repaired finding which is later reintroduced with its old fingerprint
    # is still a new identity once the ledger has truthfully dropped it.
    with pytest.raises(ratchet.RatchetError, match="new or modified"):
        ratchet._assert_live_equals_baseline(
            frozenset({original}), ratchet.Baseline(())
        )


@pytest.mark.unit
def test_base_ledger_growth_is_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    base_identity = _identity()
    grown_identity = _identity(
        path="drift/dod_receipts/OMN-2/dod-001/command.yaml",
        sha256="c" * 64,
        blob_oid="d" * 40,
    )
    monkeypatch.setattr(
        ratchet,
        "_baseline_at_commit",
        lambda _root, _base: ratchet.Baseline((base_identity,)),
    )
    monkeypatch.setattr(
        ratchet, "_validate_provenance", lambda _root, _baseline: frozenset()
    )
    with pytest.raises(ratchet.RatchetError, match="growth is forbidden"):
        ratchet._assert_base_monotonic(
            _REPO_ROOT,
            ratchet.Baseline((base_identity, grown_identity)),
            "a" * 40,
        )


@pytest.mark.unit
def test_base_must_be_full_commit_oid_not_tag_or_abbreviation() -> None:
    with pytest.raises(ratchet.RatchetError, match="full canonical"):
        ratchet._normalize_base_commit(_REPO_ROOT, "release-candidate")
    with pytest.raises(ratchet.RatchetError, match="full canonical"):
        ratchet._normalize_base_commit(_REPO_ROOT, "e54a0e5")


@pytest.mark.unit
def test_base_must_be_ancestor_to_reject_force_push_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = "a" * 40
    monkeypatch.setattr(ratchet, "_git_object_format", lambda _root: "sha1")
    monkeypatch.setattr(ratchet, "_git", lambda *_args: f"{candidate}\n".encode())
    monkeypatch.setattr(ratchet, "_head_commit", lambda _root: "b" * 40)
    monkeypatch.setattr(ratchet, "_is_ancestor", lambda *_args: False)
    with pytest.raises(ratchet.RatchetError, match="not an ancestor"):
        ratchet._normalize_base_commit(_REPO_ROOT, candidate)


@pytest.mark.unit
def test_changed_cli_fails_deletion_or_malformed_live_as_stale(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = _identity()
    monkeypatch.setattr(ratchet, "_repo_root", lambda root: root)
    monkeypatch.setattr(
        ratchet, "load_baseline", lambda _root: ratchet.Baseline((original,))
    )
    monkeypatch.setattr(
        ratchet, "_scan_explicit_identities", lambda *_args, **_kwargs: frozenset()
    )
    rc = ratchet.main(["--changed", original.path, "--repo-root", str(_REPO_ROOT)])
    assert rc == 1
    assert "stale ledger" in capsys.readouterr().err


@pytest.mark.unit
def test_subprocess_cli_wiring_and_invalid_mode_are_fail_closed() -> None:
    module = "onex_change_control.validation.receipt_honesty_ratchet"
    green = subprocess.run(
        ["uv", "run", "python", "-m", module, "--check-wiring"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert green.returncode == 0, green.stderr
    invalid = subprocess.run(
        ["uv", "run", "python", "-m", module, "--changed", "--base", "a" * 40],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid.returncode == 2
    assert "unrecognized arguments" in invalid.stderr


def _honesty_ci_job() -> dict[str, Any]:
    ci = yaml.safe_load(
        (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    assert isinstance(ci, dict)
    jobs = ci["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["honesty-gate"]
    assert isinstance(job, dict)
    return copy.deepcopy(job)


@pytest.mark.unit
def test_ci_wiring_red_controls_cover_dynamic_core_and_pythonpath() -> None:
    job = _honesty_ci_job()
    job["steps"] = [step for step in job["steps"] if step.get("id") != "validator_ref"]
    failures = ratchet._check_ci_job(job, _REPO_ROOT)
    assert any("dynamic omnibase_core ref resolver" in failure for failure in failures)

    job = _honesty_ci_job()
    corpus_step = next(
        step
        for step in job["steps"]
        if "python -m onex_change_control.validation.receipt_honesty_ratchet --corpus"
        in str(step.get("run"))
    )
    corpus_step["env"].pop("PYTHONPATH")
    failures = ratchet._check_ci_job(job, _REPO_ROOT)
    assert any("PYTHONPATH" in failure for failure in failures)

    job = _honesty_ci_job()
    corpus_step = next(
        step
        for step in job["steps"]
        if "python -m onex_change_control.validation.receipt_honesty_ratchet --corpus"
        in str(step.get("run"))
    )
    corpus_step["env"]["BASE_REF"] = "${{ github.base_ref || 'dev' }}"
    failures = ratchet._check_ci_job(job, _REPO_ROOT)
    assert any("fallback BASE_REF" in failure for failure in failures)

    job = _honesty_ci_job()
    corpus_step = next(
        step
        for step in job["steps"]
        if "python -m onex_change_control.validation.receipt_honesty_ratchet --corpus"
        in str(step.get("run"))
    )
    corpus_step["env"].pop("MERGE_GROUP_BASE_REF")
    failures = ratchet._check_ci_job(job, _REPO_ROOT)
    assert any("merge_group base ref" in failure for failure in failures)

    job = _honesty_ci_job()
    corpus_step = next(
        step
        for step in job["steps"]
        if "python -m onex_change_control.validation.receipt_honesty_ratchet --corpus"
        in str(step.get("run"))
    )
    corpus_step["run"] += (
        "\nuv run python -m onex_change_control.validation."
        "receipt_honesty_ratchet --corpus --base bad"
    )
    failures = ratchet._check_ci_job(job, _REPO_ROOT)
    assert any("not pass a base SHA" in failure for failure in failures)


@pytest.mark.unit
def test_ci_base_wiring_covers_pr_merge_group_push_new_branch_and_manual() -> None:
    run_blob = ratchet._run_blob(_honesty_ci_job())
    for required in (
        "PR_BASE_REF",
        "MERGE_GROUP_BASE_REF",
        "BEFORE_SHA",
        "0000000000000000000000000000000000000000",
        "gh api",
        "default_branch",
        "--corpus",
    ):
        assert required in run_blob


def _git_command(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _temporary_git_history(tmp_path: Path) -> tuple[Path, str]:
    remote = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "init", "--initial-branch", "main", str(checkout)],
        check=True,
        capture_output=True,
    )
    _git_command(checkout, "config", "user.email", "ratchet@example.invalid")
    _git_command(checkout, "config", "user.name", "Receipt Ratchet Test")
    _git_command(checkout, "remote", "add", "origin", str(remote))
    (checkout / "proof.txt").write_text("base\n", encoding="utf-8")
    _git_command(checkout, "add", "proof.txt")
    _git_command(checkout, "commit", "-m", "base")
    base = _git_command(checkout, "rev-parse", "HEAD")
    _git_command(checkout, "push", "-u", "origin", "main")
    (checkout / "proof.txt").write_text("head\n", encoding="utf-8")
    _git_command(checkout, "commit", "-am", "head")
    return checkout, base


def _resolve_ci_base_cli(
    repo: Path, **arguments: str
) -> subprocess.CompletedProcess[str]:
    command = [
        "uv",
        "run",
        "python",
        "-m",
        "onex_change_control.validation.receipt_honesty_ratchet",
        "--resolve-ci-base",
        "--repo-root",
        str(repo),
    ]
    for name, value in arguments.items():
        command.extend([f"--{name.replace('_', '-')}", value])
    return subprocess.run(
        command,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.unit
def test_ci_base_resolution_helper_executes_all_event_paths(tmp_path: Path) -> None:
    repo, base = _temporary_git_history(tmp_path)
    zero = "0" * 40
    cases = (
        {"event_name": "pull_request", "pr_base_ref": "main"},
        {"event_name": "merge_group", "merge_group_base_ref": "refs/heads/main"},
        {"event_name": "push", "before_sha": base},
        {"event_name": "push", "before_sha": zero, "default_branch": "main"},
        {"event_name": "workflow_dispatch", "default_branch": "main"},
    )
    for arguments in cases:
        result = _resolve_ci_base_cli(repo, **arguments)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == base

    missing_pr_branch = _resolve_ci_base_cli(repo, event_name="pull_request")
    assert missing_pr_branch.returncode == 1
    assert "base branch is missing" in missing_pr_branch.stderr


def _single_rule_legacy_identity() -> Any:
    baseline = ratchet.load_baseline(_REPO_ROOT)
    by_path: dict[str, list[Any]] = {}
    for identity in baseline.findings:
        by_path.setdefault(identity.path, []).append(identity)
    return next(
        identities[0]
        for identities in by_path.values()
        if len(identities) == 1 and identities[0].rule == "NO_OP_PROBE"
    )


def _temporary_changed_receipt_repo(tmp_path: Path) -> tuple[Path, Any, bytes]:
    repo = tmp_path / "changed-receipt"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    identity = _single_rule_legacy_identity()
    raw = (_REPO_ROOT / identity.path).read_bytes()
    receipt = repo / identity.path
    receipt.parent.mkdir(parents=True)
    receipt.write_bytes(raw)
    baseline = repo / ".onex_ratchets" / "omn_17495_receipt_honesty_baseline.yaml"
    baseline.parent.mkdir()
    baseline.write_bytes(_document([identity.as_mapping()]))
    return repo, identity, raw


def _changed_receipt_cli(
    repo: Path, receipt_path: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "onex_change_control.validation.receipt_honesty_ratchet",
            "--changed",
            "--repo-root",
            str(repo),
            receipt_path,
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.unit
def test_real_filesystem_changed_cli_rejects_mutation_copy_rule_and_malformed_live(
    tmp_path: Path,
) -> None:
    repo, identity, raw = _temporary_changed_receipt_repo(tmp_path / "mutation")
    green = _changed_receipt_cli(repo, identity.path)
    assert green.returncode == 0, green.stderr
    receipt = repo / identity.path
    receipt.write_bytes(raw + b" ")
    mutated = _changed_receipt_cli(repo, identity.path)
    assert mutated.returncode == 1
    assert "new or modified" in mutated.stderr
    assert "stale ledger" in mutated.stderr

    repo, identity, raw = _temporary_changed_receipt_repo(tmp_path / "copy")
    copied_path = identity.path.replace("command.yaml", "copied-command.yaml")
    copied = repo / copied_path
    copied.parent.mkdir(parents=True, exist_ok=True)
    copied.write_bytes(raw)
    copied_result = _changed_receipt_cli(repo, copied_path)
    assert copied_result.returncode == 1
    assert "new or modified" in copied_result.stderr

    repo, identity, raw = _temporary_changed_receipt_repo(tmp_path / "new-rule")
    changed_document = yaml.safe_load(raw)
    assert isinstance(changed_document, dict)
    changed_document["probe_stdout"] = "PENDING: intentionally new honesty rule"
    (repo / identity.path).write_text(
        yaml.safe_dump(changed_document, sort_keys=False), encoding="utf-8"
    )
    new_rule = _changed_receipt_cli(repo, identity.path)
    assert new_rule.returncode == 1
    assert "new or modified" in new_rule.stderr

    repo, identity, _raw = _temporary_changed_receipt_repo(tmp_path / "malformed")
    (repo / identity.path).write_text("not: [valid\n", encoding="utf-8")
    malformed = _changed_receipt_cli(repo, identity.path)
    assert malformed.returncode == 1
    assert "stale ledger" in malformed.stderr


@pytest.mark.unit
def test_real_filesystem_changed_cli_rejects_missing_baseline(tmp_path: Path) -> None:
    repo, identity, _raw = _temporary_changed_receipt_repo(tmp_path)
    (repo / ".onex_ratchets" / "omn_17495_receipt_honesty_baseline.yaml").unlink()
    result = _changed_receipt_cli(repo, identity.path)
    assert result.returncode == 1
    assert "missing or unreadable" in result.stderr


@pytest.mark.unit
def test_fast_scan_batching_has_identical_core_identity_semantics() -> None:
    baseline = ratchet.load_baseline(_REPO_ROOT)
    first, second = baseline.findings[:2]
    both = ratchet._scan_explicit_identities(_REPO_ROOT, [first.path, second.path])
    split = ratchet._scan_explicit_identities(
        _REPO_ROOT, [first.path]
    ) | ratchet._scan_explicit_identities(_REPO_ROOT, [second.path])
    assert both == split
    assert {item.rule for item in both} == {first.rule, second.rule}


@pytest.mark.unit
def test_wiring_is_live_and_anti_removal_anchor_is_green() -> None:
    assert ratchet.check_wiring(_REPO_ROOT) == []
    assert ratchet.main(["--check-wiring", "--repo-root", str(_REPO_ROOT)]) == 0


def _missing_ledger_base_with_staged_attempt(
    tmp_path: Path, attempt: str
) -> tuple[Path, str]:
    repo = tmp_path / attempt
    subprocess.run(
        ["git", "init", "--initial-branch", "main", str(repo)],
        check=True,
        capture_output=True,
    )
    _git_command(repo, "config", "user.email", "ratchet@example.invalid")
    _git_command(repo, "config", "user.name", "Receipt Ratchet Test")
    (repo / "proof.txt").write_text("base\n", encoding="utf-8")
    _git_command(repo, "add", "proof.txt")
    _git_command(repo, "commit", "-m", "base")
    base = _git_command(repo, "rev-parse", "HEAD")
    ledger = repo / ".onex_ratchets" / "omn_17495_receipt_honesty_baseline.yaml"
    ledger.parent.mkdir()
    ledger.write_bytes(_document([]))
    _git_command(repo, "add", str(ledger.relative_to(repo)))
    contract = repo / "contracts" / "OMN-17495.yaml"
    contract.parent.mkdir()
    contract.write_text(
        "ticket_id: OMN-17495\ndod_evidence:\n  - id: dod-sealed\n",
        encoding="utf-8",
    )
    _git_command(repo, "add", str(contract.relative_to(repo)))
    if attempt == "declared-receipt":
        receipt = (
            repo
            / "drift"
            / "dod_receipts"
            / "OMN-17495"
            / "dod-sealed"
            / "command.yaml"
        )
        receipt.parent.mkdir(parents=True)
        receipt.write_text("status: PASS\n", encoding="utf-8")
        _git_command(repo, "add", str(receipt.relative_to(repo)))
    return repo, base


@pytest.mark.unit
@pytest.mark.parametrize("attempt", ["declared-contract", "declared-receipt"])
def test_missing_ledger_base_is_sealed_despite_staged_genesis_attempt(
    tmp_path: Path, attempt: str
) -> None:
    repo, base = _missing_ledger_base_with_staged_attempt(tmp_path, attempt)
    assert ratchet._baseline_at_commit(repo, base) is None
    with pytest.raises(
        ratchet.RatchetError, match="bootstrap sealed: base must contain ledger"
    ):
        ratchet._assert_base_monotonic(repo, ratchet.Baseline(()), base)


@pytest.mark.unit
def test_ledger_containing_base_uses_normal_non_growth_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity()
    baseline = ratchet.Baseline((identity,))
    validated: list[ratchet.Baseline] = []

    def record_provenance(
        _root: Path, value: ratchet.Baseline
    ) -> frozenset[ratchet.FindingIdentity]:
        validated.append(value)
        return frozenset({identity})

    monkeypatch.setattr(ratchet, "_baseline_at_commit", lambda *_args: baseline)
    monkeypatch.setattr(ratchet, "_validate_provenance", record_provenance)
    ratchet._assert_base_monotonic(_REPO_ROOT, baseline, "a" * 40)
    assert validated == [baseline]


@pytest.mark.unit
@pytest.mark.parametrize(
    "arguments",
    [
        ["--corpus", "--base", "a" * 40],
        [
            "--corpus",
            "--base",
            "a" * 40,
            "--event-name",
            "pull_request",
            "--pr-base-ref",
            "dev",
        ],
    ],
)
def test_corpus_cli_rejects_caller_supplied_base_in_local_and_ci_contexts(
    arguments: list[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        ratchet._parse_args(arguments)
    assert raised.value.code == 2


@pytest.mark.unit
def test_local_corpus_base_uses_origin_head_not_feature_upstream(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    repo = tmp_path / "checkout"
    subprocess.run(
        ["git", "init", "--initial-branch", "dev", str(repo)],
        check=True,
        capture_output=True,
    )
    _git_command(repo, "config", "user.email", "ratchet@example.invalid")
    _git_command(repo, "config", "user.name", "Receipt Ratchet Test")
    _git_command(repo, "remote", "add", "origin", str(remote))
    (repo / "proof.txt").write_text("dev\n", encoding="utf-8")
    _git_command(repo, "add", "proof.txt")
    _git_command(repo, "commit", "-m", "dev base")
    dev = _git_command(repo, "rev-parse", "HEAD")
    _git_command(repo, "push", "-u", "origin", "dev")
    _git_command(repo, "checkout", "-b", "feature")
    (repo / "proof.txt").write_text("feature\n", encoding="utf-8")
    _git_command(repo, "commit", "-am", "feature work")
    _git_command(repo, "push", "-u", "origin", "feature")
    _git_command(
        repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/dev"
    )

    assert _git_command(repo, "rev-parse", "@{upstream}") != dev
    assert ratchet.resolve_local_base(repo) == dev


@pytest.mark.unit
def test_local_corpus_base_hydrates_missing_origin_head_from_remote_default(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "init", "--initial-branch", "dev", str(seed)],
        check=True,
        capture_output=True,
    )
    _git_command(seed, "config", "user.email", "ratchet@example.invalid")
    _git_command(seed, "config", "user.name", "Receipt Ratchet Test")
    (seed / "proof.txt").write_text("dev\n", encoding="utf-8")
    _git_command(seed, "add", "proof.txt")
    _git_command(seed, "commit", "-m", "dev base")
    dev = _git_command(seed, "rev-parse", "HEAD")
    _git_command(seed, "remote", "add", "origin", str(remote))
    _git_command(seed, "push", "-u", "origin", "dev")
    _git_command(seed, "push", "origin", "dev:feature")
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/dev"],
        cwd=remote,
        check=True,
        capture_output=True,
    )

    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "clone", "--branch", "feature", str(remote), str(checkout)],
        check=True,
        capture_output=True,
    )
    _git_command(checkout, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")

    assert ratchet.resolve_local_base(checkout) == dev
    assert (
        _git_command(checkout, "symbolic-ref", "refs/remotes/origin/HEAD")
        == "refs/remotes/origin/dev"
    )


@pytest.mark.unit
def test_fast_changed_rejects_malicious_staged_receipt_hidden_by_worktree(
    tmp_path: Path,
) -> None:
    repo, identity, raw = _temporary_changed_receipt_repo(tmp_path)
    _git_command(repo, "config", "user.email", "ratchet@example.invalid")
    _git_command(repo, "config", "user.name", "Receipt Ratchet Test")
    _git_command(repo, "add", ".")
    _git_command(repo, "commit", "-m", "initial receipt")
    receipt = repo / identity.path
    receipt.write_bytes(raw + b" malicious staged bytes")
    _git_command(repo, "add", str(receipt.relative_to(repo)))
    receipt.write_bytes(raw)

    with pytest.raises(ratchet.RatchetError, match="working tree differs from staged"):
        ratchet.enforce_changed(repo, [identity.path], require_index=True)


@pytest.mark.unit
def test_fast_changed_rejects_executable_index_receipt_mode(tmp_path: Path) -> None:
    repo, identity, raw = _temporary_changed_receipt_repo(tmp_path)
    _git_command(repo, "config", "user.email", "ratchet@example.invalid")
    _git_command(repo, "config", "user.name", "Receipt Ratchet Test")
    _git_command(repo, "add", ".")
    _git_command(repo, "commit", "-m", "initial receipt")
    receipt = repo / identity.path
    receipt.chmod(0o700)
    _git_command(repo, "add", str(receipt.relative_to(repo)))
    receipt.chmod(0o600)

    with pytest.raises(ratchet.RatchetError, match="stage-0 regular"):
        ratchet.enforce_changed(repo, [identity.path], require_index=True)
    assert receipt.read_bytes() == raw


@pytest.mark.unit
def test_changed_index_batch_uses_constant_git_subprocesses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "index-batch"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    paths: list[PurePosixPath] = []
    for number in range(64):
        path = PurePosixPath(f"drift/dod_receipts/OMN-1/{number:03d}.yaml")
        candidate = repo / Path(path)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("status: staged\n", encoding="utf-8")
        paths.append(path)
    paths.extend(
        PurePosixPath(path)
        for path in (
            "drift/dod_receipts/OMN-1/space name.yaml",
            "drift/dod_receipts/OMN-1/tab\tname.yaml",
            "drift/dod_receipts/OMN-1/newline\nname.yaml",
            "drift/dod_receipts/OMN-1/-dash:colon.yaml",
        )
    )
    for path in paths[64:]:
        candidate = repo / Path(path)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("status: staged\n", encoding="utf-8")
    _git_command(repo, "add", ".")
    calls: list[str] = []
    original_git = ratchet._git

    def _counting_git(root: Path, *args: str, **kwargs: Any) -> bytes:
        calls.append(args[0])
        return original_git(root, *args, **kwargs)

    monkeypatch.setattr(ratchet, "_git", _counting_git)
    blobs = ratchet._read_regular_index_files(repo, tuple(paths), "changed receipt")

    assert set(blobs) == set(paths)
    assert calls.count("ls-files") == 1
    assert calls.count("cat-file") == 1
    assert len(calls) == 2


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_corpus_rejects_nonregular_receipts_without_blocking(
    tmp_path: Path, kind: str
) -> None:
    root = tmp_path / "corpus"
    receipts = root / "drift" / "dod_receipts" / "OMN-1"
    receipts.mkdir(parents=True)
    candidate = receipts / "nonregular.yaml"
    if kind == "symlink":
        target = root / "target.yaml"
        target.write_text("status: target\n", encoding="utf-8")
        candidate.symlink_to(target)
    else:
        os.mkfifo(candidate)
    code = (
        "from pathlib import Path; "
        "from onex_change_control.validation.receipt_honesty_ratchet "
        "import current_identities; "
        f"current_identities(Path({str(root)!r}))"
    )
    result = subprocess.run(
        ["uv", "run", "python", "-c", code],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=_NONREGULAR_SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode != 0
    assert "regular non-executable YAML" in result.stderr


@pytest.mark.unit
def test_corpus_rejects_executable_receipt_before_scanning(tmp_path: Path) -> None:
    root = tmp_path / "executable-corpus"
    receipt = root / "drift" / "dod_receipts" / "OMN-1" / "command.yaml"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("status: untrusted\n", encoding="utf-8")
    receipt.chmod(0o700)

    with pytest.raises(ratchet.RatchetError, match="regular non-executable YAML"):
        ratchet.current_identities(root)
