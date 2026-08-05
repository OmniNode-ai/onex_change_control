# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the receipt hardening gate (OMN-13060, retro A-5; OMN-14411)."""

from __future__ import annotations

import copy
import hashlib
from typing import TYPE_CHECKING

import yaml
from omnibase_core.validation.validator_receipt_gate import (
    compute_contract_entry_sha256,
)

from scripts.validation.check_receipt_hardening import (
    DENYLISTED_VERIFIERS,
    check_contract_file,
    check_receipt_file,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

POST_CUTOFF_TS = "2026-06-12T03:00:00+00:00"
PRE_CUTOFF_TS = "2026-06-11T23:59:59+00:00"

# OMN-15710 (ABS_PATH / STDOUT_EMIT) uses its OWN, later cutoff
# (OMN_15710_CUTOFF, 2026-08-01) than the other rules' HARDENING_CUTOFF
# (2026-06-12) — see that constant's comment in check_receipt_hardening.py.
# POST_CUTOFF_TS above predates OMN_15710_CUTOFF, so it would silently
# exempt every ABS_PATH/STDOUT_EMIT test; those tests must override
# run_timestamp with this constant instead.
POST_OMN15710_CUTOFF_TS = "2026-08-02T00:00:00+00:00"
PRE_OMN15710_CUTOFF_TS = "2026-07-31T23:59:59+00:00"

# OMN-15459 (S2 family binding): a supersession replacement must reference an
# anchor the item it supersedes actually declares. The two wrappers below exist
# to prove supersession records are NOT run through *plain-receipt* hardening;
# with the generic default check_value ("uv run pytest tests/ -q") their
# `== []` assertion would also have asserted "S2 never fires", which is the
# opposite of what this gate is for. They therefore carry an item-bound check.
# The S2-fires direction is covered by
# tests/unit/scripts/test_supersession_binding_gate.py.
ITEM_BOUND_CHECK = "test -s drift/dod_receipts/OMN-13060/dod-001/command.yaml"

CONTRACT_BODY = "ticket_id: OMN-13060\ntitle: test contract\n"

# A contract shaped with real dod_evidence entries, for OMN-14411 per-entry
# hash tests. schema_version is part of the immutable per-entry hash header
# (HEADER_FIELDS in validator_receipt_gate.compute_contract_entry_sha256).
ENTRY_CONTRACT_DATA: dict[str, object] = {
    "ticket_id": "OMN-13060",
    "schema_version": "1.0.0",
    "title": "test contract",
    "dod_evidence": [
        {
            "id": "dod-001",
            "summary": "first item",
            "checks": [{"check_type": "command"}],
        },
    ],
}


def _contract_sha(contract_path: Path) -> str:
    return f"sha256:{hashlib.sha256(contract_path.read_bytes()).hexdigest()}"


def _write_contract(tmp_path: Path, ticket: str = "OMN-13060") -> Path:
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    contract_path = contracts_dir / f"{ticket}.yaml"
    contract_path.write_text(CONTRACT_BODY)
    return contract_path


def _write_entry_contract(
    tmp_path: Path, contract_data: dict[str, object], ticket: str = "OMN-13060"
) -> Path:
    """Write a contract with a real dod_evidence list for per-entry tests."""
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    contract_path = contracts_dir / f"{ticket}.yaml"
    contract_path.write_text(yaml.safe_dump(contract_data))
    return contract_path


def _receipt_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-13060",
        "evidence_item_id": "dod-001",
        "check_type": "command",
        "check_value": "uv run pytest tests/ -q",
        "status": "PASS",
        "run_timestamp": POST_CUTOFF_TS,
        "commit_sha": "abc1234def",
        "runner": "worker-a",
        "verifier": "receipt-gate-ci",
        "probe_command": "uv run pytest tests/ -q",
        "probe_stdout": "37 passed",
    }
    data.update(overrides)
    return data


def _write_receipt(tmp_path: Path, data: dict[str, object]) -> Path:
    receipt_dir = tmp_path / "drift" / "dod_receipts" / "OMN-13060" / "dod-001"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / "command.yaml"
    receipt_path.write_text(yaml.safe_dump(data))
    return receipt_path


def test_post_cutoff_receipt_with_matching_sha_passes(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path, _receipt_data(contract_sha256=_contract_sha(contract))
    )
    assert check_receipt_file(receipt, tmp_path / "contracts") == []


def test_post_cutoff_receipt_missing_sha_fails(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    receipt = _write_receipt(tmp_path, _receipt_data())
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "missing contract_sha256" in violations[0]


def test_pre_cutoff_receipt_is_exempt(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(run_timestamp=PRE_CUTOFF_TS, verifier="automated"),
    )
    assert check_receipt_file(receipt, tmp_path / "contracts") == []


def test_hash_mismatch_fails(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path, _receipt_data(contract_sha256=f"sha256:{'0' * 64}")
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "contract_sha256 mismatch" in violations[0]


def test_missing_contract_file_fails(tmp_path: Path) -> None:
    (tmp_path / "contracts").mkdir()
    receipt = _write_receipt(
        tmp_path, _receipt_data(contract_sha256=f"sha256:{'0' * 64}")
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "does not exist" in violations[0]


def test_denylisted_verifier_on_pass_fails(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(contract_sha256=_contract_sha(contract), verifier="automated"),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "session-local verifier alias" in violations[0]


def test_denylisted_verifier_on_fail_status_is_exempt(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            verifier="automated",
            status="FAIL",
        ),
    )
    assert check_receipt_file(receipt, tmp_path / "contracts") == []


def test_container_id_verifier_fails(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(contract_sha256=_contract_sha(contract), verifier="a1b2c3d4e5f6"),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "session-local verifier alias" in violations[0]


def test_self_attested_pass_is_demoted_not_denylist_checked(tmp_path: Path) -> None:
    """verifier == runner demotes PASS to ADVISORY at parse; the denylist
    rule only fires on receipts that remain PASS."""
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            runner="automated",
            verifier="automated",
        ),
    )
    assert check_receipt_file(receipt, tmp_path / "contracts") == []


def test_invalid_receipt_fails_model_validation(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    data = _receipt_data()
    del data["verifier"]
    receipt = _write_receipt(tmp_path, data)
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "ModelDodReceipt validation" in violations[0]


def test_minimal_supersession_record_is_not_plain_receipt_hardened(
    tmp_path: Path,
) -> None:
    """Supersession wrappers are validated by receipt-gate chain resolution."""
    _write_contract(tmp_path)
    receipt_dir = tmp_path / "drift" / "dod_receipts" / "OMN-13060" / "dod-001"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    supersession = receipt_dir / "command.supersede.0001.yaml"
    supersession.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "ticket_id": "OMN-13060",
                "evidence_item_id": "dod-001",
                "check_type": "command",
                "supersedes": "drift/dod_receipts/OMN-13060/dod-001/command.yaml",
                "reason": "test correction",
                "superseder": "pytest",
                "created_at": POST_CUTOFF_TS,
                "tombstone": False,
                "replacement": _receipt_data(
                    check_value=ITEM_BOUND_CHECK,
                    probe_command=ITEM_BOUND_CHECK,
                ),
            }
        )
    )
    assert check_receipt_file(supersession, tmp_path / "contracts") == []


def test_timestamp_less_receipt_is_exempt(tmp_path: Path) -> None:
    """No timestamp anywhere = pre-schema legacy artifact; the receipt
    gate already rejects it as NONPASS, so this hook exempts it."""
    _write_contract(tmp_path)
    data = _receipt_data()
    del data["run_timestamp"]
    receipt = _write_receipt(tmp_path, data)
    assert check_receipt_file(receipt, tmp_path / "contracts") == []


def test_nested_verified_at_fallback_enforces_post_cutoff(tmp_path: Path) -> None:
    """Legacy-shaped files with a nested post-cutoff verified_at are enforced."""
    _write_contract(tmp_path)
    data = _receipt_data()
    del data["run_timestamp"]
    data["evidence"] = {"verified_at": POST_CUTOFF_TS}
    receipt = _write_receipt(tmp_path, data)
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "ModelDodReceipt validation" in violations[0]


def test_nested_verified_at_fallback_exempts_pre_cutoff(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    data = _receipt_data()
    del data["run_timestamp"]
    data["evidence"] = {"verified_at": PRE_CUTOFF_TS}
    receipt = _write_receipt(tmp_path, data)
    assert check_receipt_file(receipt, tmp_path / "contracts") == []


def test_non_mapping_yaml_fails(tmp_path: Path) -> None:
    receipt_path = tmp_path / "command.yaml"
    receipt_path.write_text("- just\n- a\n- list\n")
    violations = check_receipt_file(receipt_path, tmp_path / "contracts")
    assert len(violations) == 1
    assert "not a mapping" in violations[0]


def test_main_exit_codes(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    good = _write_receipt(
        tmp_path, _receipt_data(contract_sha256=_contract_sha(contract))
    )
    assert main([str(good), "--contracts-dir", str(tmp_path / "contracts")]) == 0

    bad_dir = tmp_path / "drift" / "dod_receipts" / "OMN-13060" / "dod-002"
    bad_dir.mkdir(parents=True)
    bad = bad_dir / "command.yaml"
    bad.write_text(yaml.safe_dump(_receipt_data()))
    assert main([str(bad), "--contracts-dir", str(tmp_path / "contracts")]) == 1


def test_main_skips_missing_files(tmp_path: Path) -> None:
    assert (
        main(
            [
                str(tmp_path / "nope.yaml"),
                "--contracts-dir",
                str(tmp_path / "contracts"),
            ]
        )
        == 0
    )


def test_supersession_record_is_not_plain_receipt_hardened(tmp_path: Path) -> None:
    receipt_path = tmp_path / "command.supersede.0001.yaml"
    receipt_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "ticket_id": "OMN-13060",
                "supersedes": ("drift/dod_receipts/OMN-13060/dod-001/command.yaml"),
                "reason": "test supersession",
                "superseder": "codex-gpt-5",
                "created_at": POST_CUTOFF_TS,
                "tombstone": False,
                "replacement": _receipt_data(
                    run_timestamp=POST_CUTOFF_TS,
                    check_value=ITEM_BOUND_CHECK,
                    probe_command=ITEM_BOUND_CHECK,
                ),
            }
        )
    )

    assert check_receipt_file(receipt_path, tmp_path / "contracts") == []


def test_denylist_is_lowercase_canonical() -> None:
    assert all(v == v.strip().lower() for v in DENYLISTED_VERIFIERS)


# --- OMN-14411: per-entry contract hash binding -----------------------------
#
# check_receipt_hardening.py previously validated a receipt's contract binding
# against a WHOLE-FILE hash (compute_contract_sha256), even though the
# append-only gate (validator_occ_append_only) already validates PER-ENTRY
# (compute_contract_entry_sha256) and explicitly permits appending new
# dod_evidence items. Because the contract file's bytes change on every
# append, every previously-merged receipt's contract_sha256 went stale the
# moment anyone appended a new item — even though nothing about that
# receipt's own entry changed. ModelDodReceipt already carries
# contract_entry_sha256 (OMN-13888) precisely because it is append-stable;
# these tests prove the gate now binds to it correctly, and that doing so
# does not weaken the gate (edits, unknown entries, and missing hashes still
# fail closed).


def test_entry_hash_matching_passes(tmp_path: Path) -> None:
    """Baseline: a receipt bound via contract_entry_sha256 to its own,
    unmodified entry passes."""
    contract_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    contract = _write_entry_contract(tmp_path, contract_data)
    entry_hash = compute_contract_entry_sha256(contract_data, "dod-001")
    receipt = _write_receipt(tmp_path, _receipt_data(contract_entry_sha256=entry_hash))
    assert check_receipt_file(receipt, contract.parent) == []


def test_entry_hash_edited_entry_fails(tmp_path: Path) -> None:
    """Adversarial: if the attested dod_evidence entry is edited after the
    receipt was minted, the per-entry hash must change and the gate must
    FAIL — proving the new binding still detects tampering/drift on the
    entry it actually covers."""
    original_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    entry_hash = compute_contract_entry_sha256(original_data, "dod-001")
    receipt = _write_receipt(tmp_path, _receipt_data(contract_entry_sha256=entry_hash))

    edited_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    edited_data["dod_evidence"][0]["summary"] = "entry content changed"  # type: ignore[index]
    contract = _write_entry_contract(tmp_path, edited_data)

    violations = check_receipt_file(receipt, contract.parent)
    assert len(violations) == 1
    assert "contract_entry_sha256 mismatch" in violations[0]


def test_entry_hash_missing_entry_fails(tmp_path: Path) -> None:
    """Adversarial: a receipt pointing at a dod_evidence entry that does not
    exist in the contract (renamed/removed id) must FAIL, not silently pass
    because 'some hash was present'."""
    contract_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    # Hash a real entry so the value is a well-formed sha256:<hex>, then bind
    # the receipt to an evidence_item_id absent from the contract.
    entry_hash = compute_contract_entry_sha256(contract_data, "dod-001")
    contract = _write_entry_contract(tmp_path, contract_data)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            evidence_item_id="dod-999-does-not-exist",
            contract_entry_sha256=entry_hash,
        ),
    )
    violations = check_receipt_file(receipt, contract.parent)
    assert len(violations) == 1
    assert "not found in" in violations[0]


def test_missing_both_hash_fields_fails(tmp_path: Path) -> None:
    """Adversarial: a receipt carrying neither contract_sha256 nor
    contract_entry_sha256 must FAIL — no silent pass-through when both the
    legacy and current binding fields are absent."""
    contract_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    _write_entry_contract(tmp_path, contract_data)
    receipt = _write_receipt(tmp_path, _receipt_data())
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert len(violations) == 1
    assert "missing contract_sha256" in violations[0]


def test_append_new_entry_does_not_invalidate_prior_receipt(tmp_path: Path) -> None:
    """Load-bearing regression test for OMN-14411.

    Mirrors the live incident on ``contracts/OMN-14400.yaml``: a receipt
    minted with BOTH ``contract_sha256`` (legacy whole-file) and
    ``contract_entry_sha256`` (OMN-13888 per-entry) set, both correct at
    mint time. Appending a brand-new, unrelated dod_evidence item is a
    supported, routine operation — ``validator_occ_append_only`` explicitly
    allows it — but it changes the contract file's bytes, so the whole-file
    hash goes stale regardless of which entry was appended. The per-entry
    hash of ``dod-001`` is untouched, because it folds in only that entry
    plus the immutable header (ticket_id, schema_version).

    Proven RED against pre-fix ``check_receipt_hardening.py``: pre-fix code
    validated only ``contract_sha256`` (``compute_contract_sha256``,
    whole-file), so after the append the gate FAILED with a
    'contract_sha256 mismatch' violation even though ``contract_entry_sha256``
    was present and still correct — reproducing the exact silent-rot failure
    mode from OMN-14411 (two independent actors hit this twice in 12
    minutes). Post-fix, ``contract_entry_sha256`` is authoritative when
    present, so the same receipt passes unchanged after the append.
    """
    original_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    entry_hash = compute_contract_entry_sha256(original_data, "dod-001")
    contract = _write_entry_contract(tmp_path, original_data)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            contract_entry_sha256=entry_hash,
        ),
    )
    # Sanity: passes against the contract as originally minted.
    assert check_receipt_file(receipt, contract.parent) == []

    # Now append a brand-new, unrelated dod_evidence item — the supported,
    # routine operation the append-only gate exists to allow.
    appended_data = copy.deepcopy(original_data)
    appended_data["dod_evidence"].append(  # type: ignore[attr-defined]
        {"id": "dod-002", "summary": "second item", "checks": []}
    )
    _write_entry_contract(tmp_path, appended_data)

    # The prior receipt, bound to dod-001's per-entry hash, must still pass:
    # its own entry did not change, only the file grew a sibling entry. Its
    # (legacy) contract_sha256 is now stale — that is exactly the condition
    # contract_entry_sha256 exists to make irrelevant.
    assert check_receipt_file(receipt, contract.parent) == []


# ---------------------------------------------------------------------------
# OMN-15710 — ABS_PATH: no machine-specific absolute paths in probe bodies.
# ---------------------------------------------------------------------------


def test_abs_path_in_probe_command_fails(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            probe_command=(
                "grep -c 'x' /Users/jonah/Code/omni_home/docs/tracking/LEDGER.md"
            ),
            probe_stdout="4",
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert any("[ABS_PATH]" in v and "probe_command" in v for v in violations)


def test_abs_path_in_check_value_fails(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            check_value="test -f /Volumes/data/marker.txt",
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert any("[ABS_PATH]" in v and "check_value" in v for v in violations)


def test_abs_path_home_user_variant_fails(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            probe_command="cat /home/alice/notes.txt",
            probe_stdout="ok",
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert any("[ABS_PATH]" in v for v in violations)


def test_repo_relative_path_receipt_passes(tmp_path: Path) -> None:
    """Negative control: a repo-relative path never trips ABS_PATH."""
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            probe_command="grep -c 'x' docs/tracking/ROLLING_WORK_LEDGER.md",
            probe_stdout="4",
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert not any("[ABS_PATH]" in v for v in violations)


def test_check_contract_file_abs_path_fails(tmp_path: Path) -> None:
    contract_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    contract_data["dod_evidence"][0]["checks"][0]["check_value"] = (  # type: ignore[index]
        "grep -c pattern /Users/jonah/Code/omni_home/CLAUDE.md"
    )
    contract = _write_entry_contract(tmp_path, contract_data)
    violations = check_contract_file(contract)
    assert len(violations) == 1
    assert "[ABS_PATH]" in violations[0]
    assert "dod-001" in violations[0]


def test_check_contract_file_repo_relative_passes(tmp_path: Path) -> None:
    contract_data = copy.deepcopy(ENTRY_CONTRACT_DATA)
    contract_data["dod_evidence"][0]["checks"][0]["check_value"] = (  # type: ignore[index]
        "grep -c pattern docs/CLAUDE.md"
    )
    contract = _write_entry_contract(tmp_path, contract_data)
    assert check_contract_file(contract) == []


def test_check_contract_file_no_dod_evidence_passes(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)  # CONTRACT_BODY has no dod_evidence key
    assert check_contract_file(contract) == []


# ---------------------------------------------------------------------------
# OMN-15710 — STDOUT_EMIT: bounded terminal-command shape consistency.
# ---------------------------------------------------------------------------


def test_grep_c_prose_stdout_fails(tmp_path: Path) -> None:
    """Regression for the live OCC#6080(a) defect shape: a grep -c terminal
    command recorded prose instead of the integer it can only emit."""
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            probe_command=(
                "gh pr view 769 --json state --jq '.state' ; "
                "grep -c 'wave-0730/terraform' docs/tracking/LEDGER.md"
            ),
            probe_stdout=(
                "OPEN\nledger citations of wave-0730/terraform confirmed present"
            ),
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert any("[STDOUT_EMIT]" in v and "GREP_COUNT" in v for v in violations), (
        violations
    )


def test_grep_c_integer_stdout_passes(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            probe_command="grep -c 'pattern' docs/CLAUDE.md",
            probe_stdout="4",
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert not any("[STDOUT_EMIT]" in v for v in violations)


def test_actual_output_paraphrase_is_documented_residual_not_flagged(
    tmp_path: Path,
) -> None:
    """Documented residual (narrowed during OMN-15710 verification): the
    occ6080-grammar-repair-note shape (probe_stdout byte-exact, actual_output
    paraphrased) is NOT caught by STDOUT_EMIT — actual_output is out of
    scope for every class because ModelDodReceipt.actual_output is
    schema-sanctioned to be a "structured / truncated rendering" distinct
    from probe_stdout. A corpus-wide dry run of the pre-narrowing design
    against live dev found dozens of legitimate receipts using
    actual_output that way; checking it for literal equality was a
    systemic false positive, not a defect signal. See module docstring."""
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            check_value=(
                "test -f contracts/OMN-13060.yaml "
                "&& grep -q 'x' contracts/OMN-13060.yaml "
                "&& printf 'diagnostic note anchor PASS\\n'"
            ),
            probe_command=(
                "test -f contracts/OMN-13060.yaml "
                "&& grep -q 'x' contracts/OMN-13060.yaml "
                "&& printf 'diagnostic note anchor PASS\\n'"
            ),
            probe_stdout="diagnostic note anchor PASS",
            actual_output=(
                "Diagnostic note anchor independently re-probed live at "
                "2026-08-05T02:16:43Z; contracts/OMN-13060.yaml contains "
                "the anchor."
            ),
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert not any("[STDOUT_EMIT]" in v for v in violations), violations


def test_registry_class_skips_json_shaped_probe_stdout(tmp_path: Path) -> None:
    """Regression for a live dev-tip corpus pattern: a JSON RED/GREEN
    differential evidence bundle in probe_stdout is a distinct structured
    proof format, not the bare integer GREP_COUNT expects — must not
    false-positive."""
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            probe_command=(
                "gh api 'repos/OmniNode-ai/x/contents/y.py?ref=abc' "
                "--jq '.content' | base64 -d | grep -c 'def _seed'"
            ),
            probe_stdout=(
                '{"evidence_ref":"abc123","green_exit":0,"red_exit":1,'
                '"red_ref":"def456"}'
            ),
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert not any("[STDOUT_EMIT]" in v for v in violations), violations


def test_printf_literal_matching_actual_output_passes(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            check_value="printf 'diagnostic note anchor PASS\\n'",
            probe_command="printf 'diagnostic note anchor PASS\\n'",
            probe_stdout="diagnostic note anchor PASS",
            actual_output="diagnostic note anchor PASS",
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert not any("[STDOUT_EMIT]" in v for v in violations)


def test_echo_literal_mismatch_fails(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            check_value="echo 'anchor PASS'",
            probe_command="echo 'anchor PASS'",
            probe_stdout="something else entirely",
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert any("[STDOUT_EMIT]" in v for v in violations)


def test_wc_l_integer_passes_and_prose_fails(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    passing = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            probe_command="wc -l docs/CLAUDE.md",
            probe_stdout="120 docs/CLAUDE.md",
        ),
    )
    assert not any(
        "[STDOUT_EMIT]" in v
        for v in check_receipt_file(passing, tmp_path / "contracts")
    )

    failing_dir = tmp_path / "drift" / "dod_receipts" / "OMN-13060" / "dod-002"
    failing_dir.mkdir(parents=True, exist_ok=True)
    failing = failing_dir / "command.yaml"
    failing.write_text(
        yaml.safe_dump(
            _receipt_data(
                evidence_item_id="dod-002",
                contract_sha256=_contract_sha(contract),
                run_timestamp=POST_OMN15710_CUTOFF_TS,
                probe_command="wc -l docs/CLAUDE.md",
                probe_stdout="lots of lines",
            )
        )
    )
    violations = check_receipt_file(failing, tmp_path / "contracts")
    assert any("[STDOUT_EMIT]" in v and "WC_LINES" in v for v in violations)


def test_jq_sha_hex_passes_and_prose_fails(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path)
    passing = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            probe_command="gh pr view 1 --json mergeCommit --jq '.mergeCommit.oid'",
            probe_stdout="3e24d9bd9aa1122334455667788990011223344",
        ),
    )
    assert not any(
        "[STDOUT_EMIT]" in v
        for v in check_receipt_file(passing, tmp_path / "contracts")
    )

    failing_dir = tmp_path / "drift" / "dod_receipts" / "OMN-13060" / "dod-003"
    failing_dir.mkdir(parents=True, exist_ok=True)
    failing = failing_dir / "command.yaml"
    failing.write_text(
        yaml.safe_dump(
            _receipt_data(
                evidence_item_id="dod-003",
                contract_sha256=_contract_sha(contract),
                run_timestamp=POST_OMN15710_CUTOFF_TS,
                probe_command="gh pr view 1 --json mergeCommit --jq '.mergeCommit.oid'",
                probe_stdout="the merge commit sha",
            )
        )
    )
    violations = check_receipt_file(failing, tmp_path / "contracts")
    assert any("[STDOUT_EMIT]" in v and "JQ_SHA" in v for v in violations)


def test_stdout_emit_compound_jq_filter_is_undetectable_not_flagged(
    tmp_path: Path,
) -> None:
    """Documented residual: a compound jq filter ([.a,.b] | @tsv) is out of
    the closed registry's scope and must not false-positive."""
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            probe_command=(
                "gh pr view 769 --json state,mergeCommit "
                "--jq '[.state,.mergeCommit.oid] | @tsv'"
            ),
            probe_stdout="anything at all, not shape-checked",
        ),
    )
    assert not any(
        "[STDOUT_EMIT]" in v
        for v in check_receipt_file(receipt, tmp_path / "contracts")
    )


def test_stdout_emit_skips_non_pass_status(tmp_path: Path) -> None:
    """PENDING receipts are not asserting the check ran cleanly — out of scope."""
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            status="PENDING",
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            probe_command="grep -c 'x' docs/CLAUDE.md",
            probe_stdout="not an integer at all",
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert not any("[STDOUT_EMIT]" in v for v in violations)


def test_stdout_emit_unregistered_command_class_not_flagged(tmp_path: Path) -> None:
    """A command shape outside the closed registry (curl) is a documented
    false negative, not a false pass to be reported as clean-but-unchecked."""
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            probe_command="curl -sf https://example.invalid/health",
            probe_stdout="anything the server happened to return",
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert not any("[STDOUT_EMIT]" in v for v in violations)


def test_occ6084_fabricated_receipt_fixture_fails_both_rules(tmp_path: Path) -> None:
    """Scratch reproduction of the merged OCC#6084(a) defect
    (occ6080-attribution-fix-no-live-mutation): must fail with a pointed
    message on BOTH new rules simultaneously."""
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            check_value=(
                "gh pr view 769 --repo OmniNode-ai/omninode_infra "
                "--json state,mergeCommit "
                "--jq '[.state,.mergeCommit.oid] | @tsv' ; "
                "grep -c 'wave-0730/terraform' "
                "/Users/jonah/Code/omni_home/docs/tracking/LEDGER.md"
            ),
            probe_command=(
                "gh pr view 769 --repo OmniNode-ai/omninode_infra "
                "--json state,mergeCommit "
                "--jq '[.state,.mergeCommit.oid] | @tsv' ; "
                "grep -c 'wave-0730/terraform' "
                "/Users/jonah/Code/omni_home/docs/tracking/LEDGER.md"
            ),
            probe_stdout=(
                "MERGED\t3e24d9bd9\n"
                "ledger citations of wave-0730/terraform confirmed present"
            ),
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert any("[ABS_PATH]" in v for v in violations), violations
    assert any("[STDOUT_EMIT]" in v and "GREP_COUNT" in v for v in violations), (
        violations
    )


def test_omn_15710_cutoff_exempts_pre_existing_corpus_receipts(
    tmp_path: Path,
) -> None:
    """The same fabricated shape as the test above, but timestamped just
    before OMN_15710_CUTOFF: must be exempt from BOTH new rules, mirroring
    HARDENING_CUTOFF's own migration-debt exemption for the other rules.
    Regression for the corpus-wide false-positive volume ABS_PATH/STDOUT_EMIT
    produced when applied retroactively (OMN-15710 verification)."""
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=PRE_OMN15710_CUTOFF_TS,
            probe_command="grep -c 'x' /Users/jonah/Code/omni_home/notes.txt",
            probe_stdout="not an integer at all",
        ),
    )
    violations = check_receipt_file(receipt, tmp_path / "contracts")
    assert not any("[ABS_PATH]" in v or "[STDOUT_EMIT]" in v for v in violations), (
        violations
    )


def test_main_reports_both_rule_violations_exit_1(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _write_contract(tmp_path)
    receipt = _write_receipt(
        tmp_path,
        _receipt_data(
            contract_sha256=_contract_sha(contract),
            run_timestamp=POST_OMN15710_CUTOFF_TS,
            probe_command="grep -c 'x' /Users/jonah/notes.txt",
            probe_stdout="not an integer",
        ),
    )
    monkeypatch.chdir(tmp_path)
    exit_code = main(
        [
            str(receipt.relative_to(tmp_path)),
            "--contracts-dir",
            "contracts",
        ]
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "[ABS_PATH]" in captured.out
    assert "[STDOUT_EMIT]" in captured.out
