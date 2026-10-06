# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""The contract-change rebind gate, and its wiring (OMN-18304).

The live defect is replayed from the real bytes of the commit that shipped it
(``onex_change_control#9327``, bot pass ``be02006e78``), rather than from a
fixture written to match the fix. The repaired merged state is the negative
control on the same bytes, so the test distinguishes "the gate catches this"
from "the gate catches everything".

Wiring is asserted beside behaviour: a detection tool that is not a pre-merge
gate is advisory and gets ignored, so removing either half is a red test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from omnibase_core.validation.validator_receipt_gate import (
    compute_contract_entry_sha256,
)

from scripts.validation.check_contract_change_rebinds_receipts import (
    check_ticket,
    main,
    touched_entry_ids,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = "scripts/validation/check_contract_change_rebinds_receipts.py"
_HOOK_ID = "check-contract-change-rebinds-receipts"

_TICKET = "OMN-0000"


def _contract(*item_ids: str) -> str:
    head = f'---\nschema_version: "1.0.0"\nticket_id: "{_TICKET}"\ndod_evidence:\n'
    return head + "".join(
        f'  - id: "{item_id}"\n'
        '    source: "generated"\n'
        "    checks:\n"
        '      - check_type: "test_passes"\n'
        '        check_value: "uv run pytest tests/test_thing.py -q"\n'
        for item_id in item_ids
    )


def _write_receipt(
    receipts_root: Path,
    item_id: str,
    entry_hash: str,
    *,
    check_type: str = "test_passes",
) -> Path:
    directory = receipts_root / _TICKET / item_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{check_type}.yaml"
    path.write_text(
        "---\n"
        'schema_version: "1.0.0"\n'
        f'ticket_id: "{_TICKET}"\n'
        f'evidence_item_id: "{item_id}"\n'
        f'check_type: "{check_type}"\n'
        f'contract_entry_sha256: "{entry_hash}"\n'
        "status: PASS\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Scope: what a change touches, and what it deliberately does not.
# ---------------------------------------------------------------------------


def test_an_unchanged_contract_touches_nothing() -> None:
    data = yaml.safe_load(_contract("dod-a", "dod-b"))
    assert touched_entry_ids(data, data) == set()


def test_a_rename_puts_both_the_old_and_the_new_id_in_scope() -> None:
    before = yaml.safe_load(_contract("dod-a"))
    after = yaml.safe_load(_contract("dod-b"))
    assert touched_entry_ids(before, after) == {"dod-a", "dod-b"}


def test_appending_a_sibling_leaves_the_existing_entry_out_of_scope() -> None:
    """The property that makes an unscoped gate unnecessary.

    A companion appending its own rows must not drag every other lane's
    bindings into this author's diff.
    """
    before = yaml.safe_load(_contract("dod-a"))
    after = yaml.safe_load(_contract("dod-a", "dod-b"))
    assert touched_entry_ids(before, after) == {"dod-b"}


def test_a_new_contract_puts_every_entry_in_scope() -> None:
    """A created contract has no prior revision, so nothing is grandfathered."""
    after = yaml.safe_load(_contract("dod-a", "dod-b"))
    assert touched_entry_ids(None, after) == {"dod-a", "dod-b"}


# ---------------------------------------------------------------------------
# Behaviour.
# ---------------------------------------------------------------------------


def test_a_rename_leaving_the_pre_rename_hash_is_refused(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    receipts = tmp_path / "drift" / "dod_receipts"
    contracts.mkdir(parents=True)

    before = yaml.safe_load(_contract("dod-a"))
    _write_receipt(receipts, "dod-a", compute_contract_entry_sha256(before, "dod-a"))

    after_text = _contract("dod-b")
    (contracts / f"{_TICKET}.yaml").write_text(after_text, encoding="utf-8")
    after = yaml.safe_load(after_text)

    findings = check_ticket(
        _TICKET,
        contracts_dir=contracts,
        receipts_root=receipts,
        scope=touched_entry_ids(before, after),
    )
    assert len(findings) == 1
    assert "no longer declares" in findings[0]


def test_a_correct_rebind_passes(tmp_path: Path) -> None:
    """Negative control: the same rename, with the receipt rebound, is clean."""
    contracts = tmp_path / "contracts"
    receipts = tmp_path / "drift" / "dod_receipts"
    contracts.mkdir(parents=True)

    before = yaml.safe_load(_contract("dod-a"))
    after_text = _contract("dod-b")
    (contracts / f"{_TICKET}.yaml").write_text(after_text, encoding="utf-8")
    after = yaml.safe_load(after_text)
    _write_receipt(receipts, "dod-b", compute_contract_entry_sha256(after, "dod-b"))

    assert (
        check_ticket(
            _TICKET,
            contracts_dir=contracts,
            receipts_root=receipts,
            scope=touched_entry_ids(before, after),
        )
        == []
    )


def test_a_pre_existing_stale_binding_on_an_untouched_entry_is_not_this_change(
    tmp_path: Path,
) -> None:
    """Somebody else's old defect is not reported against this author.

    This is the property the corpus measurement forced: an unscoped sweep found
    10 genuinely stale bindings on long-merged receipts that cannot be repaired
    in place, and reporting them on every pull request that touches those five
    contracts would have made the gate noise.
    """
    contracts = tmp_path / "contracts"
    receipts = tmp_path / "drift" / "dod_receipts"
    contracts.mkdir(parents=True)

    before_text = _contract("dod-a")
    after_text = _contract("dod-a", "dod-b")
    (contracts / f"{_TICKET}.yaml").write_text(after_text, encoding="utf-8")
    before = yaml.safe_load(before_text)
    after = yaml.safe_load(after_text)

    _write_receipt(receipts, "dod-a", "sha256:" + "0" * 64)  # stale, and untouched
    _write_receipt(receipts, "dod-b", compute_contract_entry_sha256(after, "dod-b"))

    scope = touched_entry_ids(before, after)
    assert (
        check_ticket(
            _TICKET, contracts_dir=contracts, receipts_root=receipts, scope=scope
        )
        == []
    )
    # ...and the unscoped MEASUREMENT sweep does see it, so the exclusion above
    # is a scoping decision rather than a blind spot.
    unscoped = check_ticket(
        _TICKET, contracts_dir=contracts, receipts_root=receipts, scope=None
    )
    assert len(unscoped) == 1


def test_an_unrebound_sentinel_is_refused(tmp_path: Path) -> None:
    contracts = tmp_path / "contracts"
    receipts = tmp_path / "drift" / "dod_receipts"
    contracts.mkdir(parents=True)

    after_text = _contract("dod-a")
    (contracts / f"{_TICKET}.yaml").write_text(after_text, encoding="utf-8")
    _write_receipt(receipts, "dod-a", "sha256:PENDING")

    findings = check_ticket(
        _TICKET,
        contracts_dir=contracts,
        receipts_root=receipts,
        scope=touched_entry_ids(None, yaml.safe_load(after_text)),
    )
    assert len(findings) == 1
    assert "unrebound" in findings[0]


def test_a_supersession_record_is_read_at_its_replacement(tmp_path: Path) -> None:
    """A union emits supersession records; reading only the top level skips them."""
    contracts = tmp_path / "contracts"
    receipts = tmp_path / "drift" / "dod_receipts"
    contracts.mkdir(parents=True)

    after_text = _contract("dod-a")
    (contracts / f"{_TICKET}.yaml").write_text(after_text, encoding="utf-8")
    directory = receipts / _TICKET / "dod-a"
    directory.mkdir(parents=True)
    (directory / "test_passes.supersede.1234.yaml").write_text(
        "---\n"
        'schema_version: "1.0.0"\n'
        f'ticket_id: "{_TICKET}"\n'
        'evidence_item_id: "dod-a"\n'
        'check_type: "test_passes"\n'
        "replacement:\n"
        f'  ticket_id: "{_TICKET}"\n'
        '  evidence_item_id: "dod-a"\n'
        '  contract_entry_sha256: "sha256:' + "0" * 64 + '"\n',
        encoding="utf-8",
    )

    findings = check_ticket(
        _TICKET,
        contracts_dir=contracts,
        receipts_root=receipts,
        scope=touched_entry_ids(None, yaml.safe_load(after_text)),
    )
    assert len(findings) == 1
    assert "replacement.contract_entry_sha256" in findings[0]


def test_a_block_scalar_trailing_newline_is_not_a_mismatch(tmp_path: Path) -> None:
    """Three live receipts render the hash through a block scalar (OMN-15320).

    The trailing newline is a rendering artifact of the file, not a different
    digest, and reporting it would be a finding whose expected and actual differ
    by an invisible character.
    """
    contracts = tmp_path / "contracts"
    receipts = tmp_path / "drift" / "dod_receipts"
    contracts.mkdir(parents=True)

    after_text = _contract("dod-a")
    (contracts / f"{_TICKET}.yaml").write_text(after_text, encoding="utf-8")
    after = yaml.safe_load(after_text)
    entry_hash = compute_contract_entry_sha256(after, "dod-a")

    directory = receipts / _TICKET / "dod-a"
    directory.mkdir(parents=True)
    (directory / "test_passes.yaml").write_text(
        "---\n"
        'schema_version: "1.0.0"\n'
        f'ticket_id: "{_TICKET}"\n'
        'evidence_item_id: "dod-a"\n'
        'check_type: "test_passes"\n'
        "contract_entry_sha256: |\n"
        f"  {entry_hash}\n"
        "status: PASS\n",
        encoding="utf-8",
    )

    assert (
        check_ticket(
            _TICKET,
            contracts_dir=contracts,
            receipts_root=receipts,
            scope=touched_entry_ids(None, after),
        )
        == []
    )


# ---------------------------------------------------------------------------
# The live replay: the real bytes of the commit that shipped the defect.
# ---------------------------------------------------------------------------

_LIVE = _REPO_ROOT / "tests" / "fixtures" / "omn_18304"
_LIVE_TICKET = "OMN-18291"
_LIVE_ITEM = "dod-occ-proof-node-1414"


def _live_contract(case: str) -> object:
    return yaml.safe_load(
        (_LIVE / case / "contracts" / f"{_LIVE_TICKET}.yaml").read_text(
            encoding="utf-8"
        )
    )


def test_live_replay_of_the_union_defect_is_refused() -> None:
    """The bytes of bot pass ``be02006e78`` on onex_change_control#9327."""
    scope = touched_entry_ids(_live_contract("before"), _live_contract("union"))
    assert _LIVE_ITEM in scope

    findings = check_ticket(
        _LIVE_TICKET,
        contracts_dir=_LIVE / "union" / "contracts",
        receipts_root=_LIVE / "union" / "drift" / "dod_receipts",
        scope=scope,
    )
    assert len(findings) == 1
    assert _LIVE_ITEM in findings[0]


def test_live_replay_of_the_repair_passes() -> None:
    """Negative control: the same ticket, as merged at ``bb470410b6``."""
    scope = touched_entry_ids(_live_contract("before"), _live_contract("repaired"))
    assert (
        check_ticket(
            _LIVE_TICKET,
            contracts_dir=_LIVE / "repaired" / "contracts",
            receipts_root=_LIVE / "repaired" / "drift" / "dod_receipts",
            scope=scope,
        )
        == []
    )


# ---------------------------------------------------------------------------
# CLI and wiring.
# ---------------------------------------------------------------------------


def test_cli_positive_control_passes() -> None:
    assert main(["--self-test"]) == 0


def test_cli_reports_clean_with_no_changed_contract() -> None:
    assert main([]) == 0


def test_gate_is_wired_as_a_pre_commit_hook() -> None:
    config = yaml.safe_load((_REPO_ROOT / ".pre-commit-config.yaml").read_text())
    hook_ids = {
        hook.get("id")
        for repo in config.get("repos", [])
        for hook in repo.get("hooks", [])
    }
    assert _HOOK_ID in hook_ids


@pytest.mark.parametrize("suffix", ["--self-test", "--paths-file0"])
def test_gate_and_its_positive_control_are_wired_in_ci(suffix: str) -> None:
    """Both the control and the gate itself must run on a pull request."""
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert _SCRIPT in ci
    assert suffix in ci.split(_SCRIPT, 1)[1][:400] or any(
        suffix in segment[:400] for segment in ci.split(_SCRIPT)[1:]
    )
