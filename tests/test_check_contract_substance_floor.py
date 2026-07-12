# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Adversarial tests for the contract substance floor (OMN-14409).

The load-bearing property is asymmetric and must be proven in BOTH directions:

* a DoD made entirely of existence probes must FAIL (it certifies nothing), and
* the Evidence-Source autobind stamp path must reject NOTHING.

The second is as important as the first. Downgrading or rejecting autobind
binding receipts would kill the F1 stamp path and push authors back to
hand-authored companions — the exact outcome OMN-14055 exists to prevent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml
from omnibase_core.enums.ticket.enum_proof_tier import EnumProofTier

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "validation"
    / "check_contract_substance_floor.py"
)
_spec = importlib.util.spec_from_file_location(
    "check_contract_substance_floor", _MODULE_PATH
)
assert _spec is not None
assert _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["check_contract_substance_floor"] = _mod
_spec.loader.exec_module(_mod)

derive_proof_tier = _mod.derive_proof_tier
evaluate_contract = _mod.evaluate_contract

# The exact probes the autobind emits (OMN-13317 F1), verbatim from
# contracts/OMN-14400.yaml as minted at commit 97e704f84.
_AUTOBIND_PR_PROBE = (
    "PR_NUMBER=1721 REPO=OmniNode-ai/omnimarket gh pr view ${PR_NUMBER} "
    "--repo ${REPO} --json number,state"
)
_AUTOBIND_SELF_BIND_PROBE = (
    "PR_NUMBER=3971 REPO=OmniNode-ai/onex_change_control gh pr view ${PR_NUMBER} "
    "--repo ${REPO} --json number,state"
)
_SUBSTANTIVE_PROBE = "uv run pytest tests/unit/nodes/node_dod_verify/ -v"


def _write_contract(
    tmp_path: Path, ticket: str, items: list[dict[str, object]]
) -> Path:
    """Serialize a contract via yaml.safe_dump — never string templating.

    Hand-built YAML silently mangles probes containing quotes and colons, which
    is exactly the input class under test here.
    """
    contracts = tmp_path / "contracts"
    contracts.mkdir(exist_ok=True)
    path = contracts / f"{ticket}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "ticket_id": ticket,
                "title": "test",
                "dod_evidence": items,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _item(
    item_id: str, check_value: str, check_type: str = "command"
) -> dict[str, object]:
    return {
        "id": item_id,
        "checks": [{"check_type": check_type, "check_value": check_value}],
    }


class TestDeriveProofTier:
    """The tier is derived from the probe — the input OMN-13338 never had."""

    def test_existence_probe_derives_l0(self) -> None:
        assert derive_proof_tier("command", _AUTOBIND_PR_PROBE) is EnumProofTier.L0

    def test_bare_gh_pr_view_derives_l0(self) -> None:
        assert derive_proof_tier("command", "gh pr view 1721") is EnumProofTier.L0

    def test_gh_pr_view_with_status_rollup_is_not_existence(self) -> None:
        """`statusCheckRollup` carries CI signal, so it is not a bare existence
        probe."""
        probe = "gh pr view 1721 --json number,statusCheckRollup"
        assert derive_proof_tier("command", probe).satisfies(EnumProofTier.L1)

    def test_test_probe_derives_l1(self) -> None:
        assert derive_proof_tier("test_passes", _SUBSTANTIVE_PROBE) is EnumProofTier.L1

    def test_runtime_probe_derives_l2(self) -> None:
        probe = "kubectl -n onex-dev get deploy omnidash -o jsonpath='{.spec}'"
        assert derive_proof_tier("command", probe) is EnumProofTier.L2

    def test_empty_probe_derives_l0(self) -> None:
        assert derive_proof_tier("command", "") is EnumProofTier.L0


class TestSubstanceFloor:
    def test_all_existence_dod_fails(self, tmp_path: Path) -> None:
        """The core defect: a DoD made only of existence probes certifies nothing.

        This is contracts/OMN-14400.yaml exactly as the autobind minted it — the
        contract that merged GREEN through every gate (occ-preflight, Receipt
        Gate, Receipt Honesty, Receipt Hardening).
        """
        path = _write_contract(
            tmp_path,
            "OMN-14400",
            [
                _item("dod-OmniNode-ai-omnimarket-pr-1721", _AUTOBIND_PR_PROBE),
                _item("occ-self-bind-pr-3971", _AUTOBIND_SELF_BIND_PROBE),
            ],
        )
        result = evaluate_contract(path)
        assert not result.passed
        assert result.substantive == []
        assert all(f.tier is EnumProofTier.L0 for f in result.findings)

    def test_substantive_item_plus_autobind_binding_items_passes(
        self, tmp_path: Path
    ) -> None:
        """RED -> GREEN: the ONLY delta is one real check. Binding probes stay put."""
        path = _write_contract(
            tmp_path,
            "OMN-14400",
            [
                _item("dod-OmniNode-ai-omnimarket-pr-1721", _AUTOBIND_PR_PROBE),
                _item("occ-self-bind-pr-3971", _AUTOBIND_SELF_BIND_PROBE),
                _item(
                    "dod-behavioral-proof", _SUBSTANTIVE_PROBE, check_type="test_passes"
                ),
            ],
        )
        result = evaluate_contract(path)
        assert result.passed
        assert len(result.substantive) == 1
        # The autobind's two binding probes are still present and still valid —
        # they simply do not COUNT toward the floor.
        assert len(result.findings) == 3

    def test_autobind_stamp_on_contract_with_substantive_item_rejects_nothing(
        self, tmp_path: Path
    ) -> None:
        """The autobind stamp path must have a rejection rate of exactly zero.

        A contract that already carries a real check keeps passing no matter how
        many Evidence-Source binding items the autobind appends to it.
        """
        items = [_item("dod-real", _SUBSTANTIVE_PROBE, check_type="test_passes")]
        for pr in (3971, 3981, 3985, 3986):
            items.append(
                _item(
                    f"occ-self-bind-pr-{pr}",
                    f"gh pr view {pr} --repo OmniNode-ai/onex_change_control "
                    f"--json number,state",
                )
            )
        result = evaluate_contract(_write_contract(tmp_path, "OMN-14409", items))
        assert result.passed, "autobind binding items must never cause a rejection"
        assert len(result.substantive) == 1

    def test_contract_with_no_dod_evidence_is_out_of_scope(
        self, tmp_path: Path
    ) -> None:
        """Whether a contract is REQUIRED is the Receipt Gate's job, not this gate's."""
        path = tmp_path / "contracts" / "OMN-1.yaml"
        path.parent.mkdir(exist_ok=True)
        path.write_text('---\nticket_id: "OMN-1"\ntitle: "t"\n', encoding="utf-8")
        assert evaluate_contract(path).passed

    @pytest.mark.parametrize(
        "probe",
        [
            "grep -q 'def handle' src/omnimarket/nodes/node_x/handler.py",
            "gh pr checks 1721 --repo OmniNode-ai/omnimarket",
            "curl -sf http://localhost:18085/health",
        ],
    )
    def test_falsifiable_probes_are_substantive(
        self, tmp_path: Path, probe: str
    ) -> None:
        """The gate rejects only what is provably content-free, never what is merely
        hard to classify. A static assertion, a CI outcome, and a live readback all
        say something falsifiable about the change."""
        result = evaluate_contract(
            _write_contract(tmp_path, "OMN-2", [_item("dod-001", probe)])
        )
        assert result.passed
