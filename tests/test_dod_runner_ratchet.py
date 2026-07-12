# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-14436: the DoD-runner grandfather ratchet may only shrink.

The allowlist exempts contracts authored while the runner executed check_values
against the onex_change_control clone instead of the product checkout. Those
contracts could only ever reach the receipt store. They are reported, not
enforced.

The danger is that the allowlist becomes a dumping ground: a new contract that
fails against the product gets a line here and the gate quietly stops gating.
These tests make that mechanically impossible -- the count is pinned, so growth
fails CI.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ALLOWLIST = _REPO_ROOT / "scripts" / "ci" / "dod_runner_legacy_allowlist.txt"

# The corpus frozen at the cutoff (onex_change_control dev @ aa33fb386,
# 2026-07-12) -- every contract that existed before check_values began executing
# against the product. This number MUST NOT GROW. Lowering it is the entire
# point: each removal is a contract rewritten to actually observe its product.
CUTOFF_TICKET_COUNT = 6919


def _load_runner():  # type: ignore[no-untyped-def]
    """Import the CI runner by path (it is a script, not a package module)."""
    path = _REPO_ROOT / "scripts" / "ci" / "run_contract_compliance_check.py"
    spec = importlib.util.spec_from_file_location("_dod_runner", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_dod_runner"] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def _tickets() -> list[str]:
    return [
        line.split("#", 1)[0].strip()
        for line in _ALLOWLIST.read_text().splitlines()
        if line.split("#", 1)[0].strip()
    ]


def test_ratchet_never_grows() -> None:
    """The allowlist may only shrink. A grown count means new debt was hidden."""
    count = len(_tickets())
    assert count <= CUTOFF_TICKET_COUNT, (
        f"Grandfather allowlist GREW to {count} (cutoff: {CUTOFF_TICKET_COUNT}). "
        "A contract that fails against the product must be fixed, not exempted. "
        "This list is a ratchet -- it may only shrink."
    )


def test_ratchet_count_is_pinned() -> None:
    """If the list shrank, lower CUTOFF_TICKET_COUNT in the same PR."""
    count = len(_tickets())
    assert count == CUTOFF_TICKET_COUNT, (
        f"Allowlist is {count}, pin says {CUTOFF_TICKET_COUNT}. If you removed "
        "entries (good -- that is the ratchet turning), lower the pin to match."
    )


def test_no_duplicate_entries() -> None:
    tickets = _tickets()
    assert len(set(tickets)) == len(tickets), "duplicate ticket ids in allowlist"


@pytest.mark.parametrize(
    "check_value",
    [
        "grep -q '^status: PASS$' drift/dod_receipts/OMN-1/dod-x/command.yaml",
        "test -f contracts/OMN-14391.yaml",
        "uv run validate-yaml contracts/OMN-10080.yaml",
    ],
)
def test_inert_checks_are_detected(check_value: str) -> None:
    """A check that only reads the OCC store cannot observe the product."""
    assert runner._is_inert_check(check_value) is True


@pytest.mark.parametrize(
    "check_value",
    [
        'test "$(gh api repos/OmniNode-ai/omnibase_infra/pulls/2264'
        ' --jq .merged)" = "true"',
        "test -f src/omnibase_infra/runtime/message_dispatch_engine.py",
        "uv run pytest tests/integration/runtime/test_dispatch_seam.py",
    ],
)
def test_honest_checks_are_not_inert(check_value: str) -> None:
    """Checks that reach the product or GitHub are enforced, never demoted."""
    assert runner._is_inert_check(check_value) is False


def test_inert_only_contract_blocks_a_new_ticket() -> None:
    """A new contract whose every check is inert certifies nothing -> BLOCK."""
    dod = [{"id": "d1", "checks": [{"check_value": "grep x drift/dod_receipts/a"}]}]
    assert runner._has_effective_check(dod) is False


def test_one_product_check_is_enough_to_be_effective() -> None:
    dod = [
        {
            "id": "d1",
            "checks": [
                {"check_value": "grep x drift/dod_receipts/a"},
                {"check_value": "test -f src/real_file.py"},
            ],
        }
    ]
    assert runner._has_effective_check(dod) is True


def test_missing_allowlist_fails_loudly() -> None:
    """A silently-absent allowlist would enforce the whole legacy corpus."""
    with pytest.raises(FileNotFoundError):
        runner._load_legacy_allowlist(Path("/nonexistent/allowlist.txt"))


def test_allowlist_loads_and_contains_the_legacy_corpus() -> None:
    tickets = runner._load_legacy_allowlist(_ALLOWLIST)
    assert len(tickets) == CUTOFF_TICKET_COUNT
    assert "OMN-14391" in tickets  # the laundering case, grandfathered by design


# --------------------------------------------------------------------------
# OMN-14436: the grandfather is bound to the contract's CONTENT DIGEST, not to
# its ticket id. A ticket-keyed allowlist is a PERMANENT LAUNDERING CHANNEL:
# anyone could append a fresh circular dod_evidence entry under an old ticket id
# and inherit its exemption forever. These tests make that impossible.
# --------------------------------------------------------------------------


def test_every_allowlist_entry_carries_a_digest() -> None:
    """A digest-less entry is a ticket-keyed exemption. None may exist."""
    undigested = [line for line in _tickets() if len(line.split()) != 2]
    assert not undigested, (
        f"{len(undigested)} allowlist entries carry no contract digest "
        f"(e.g. {undigested[:3]}). A digest-less entry re-opens the ticket-keyed "
        "laundering hole: new circular evidence could be appended under an old "
        "ticket id and inherit its grandfather forever."
    )


def test_loader_rejects_a_digestless_entry(tmp_path: Path) -> None:
    """The runner must FAIL CLOSED on a ticket-only line, not silently exempt it."""
    bad = tmp_path / "allow.txt"
    bad.write_text("# comment\nOMN-9999\n")
    with pytest.raises(ValueError, match="digest-less entry"):
        runner._load_legacy_allowlist(bad)


def test_modifying_a_grandfathered_contract_revokes_the_exemption(
    tmp_path: Path,
) -> None:
    """Frozen debt stays frozen; TOUCHED debt must be paid.

    This is the property that separates a ratchet from an amnesty. If the
    exemption survived modification, an old ticket id would be a permanent
    laundering channel for brand-new circular evidence.
    """
    contract = tmp_path / "OMN-9999.yaml"
    contract.write_text("ticket_id: OMN-9999\ndod_evidence: []\n")
    digest = runner._contract_digest(contract)

    allow = tmp_path / "allow.txt"
    allow.write_text(f"OMN-9999 {digest}\n")
    entries = runner._load_legacy_allowlist(allow)

    # Untouched: the digest matches, so the contract is grandfathered.
    assert entries["OMN-9999"] == runner._contract_digest(contract)

    # Touched: append anything at all -- the digest moves and the exemption dies.
    contract.write_text(contract.read_text() + "# appended\n")
    assert entries["OMN-9999"] != runner._contract_digest(contract), (
        "Modifying a grandfathered contract MUST change its digest and revoke "
        "the exemption. Otherwise the allowlist is an amnesty, not a ratchet."
    )
