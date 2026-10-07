# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for scripts/migrate_dod_contracts.py."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

# Add scripts/ to path so we can import directly
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from migrate_dod_contracts import (
    classify_ticket,
    make_dod_evidence,
    migrate_contract_file,
    needs_migration,
)

# ---------------------------------------------------------------------------
# classify_ticket — must distinguish governance/process from code work
# ---------------------------------------------------------------------------


def test_classify_repo_transfer_is_governance() -> None:
    """OMN-9829 ('Bret initiates GitHub repo transfer') has no test surface;
    even though the upstream auto-generator stamps is_seam_ticket=True, this
    must be classified as governance so the migration script does NOT emit
    a pytest check for it.
    """
    contract = {
        "summary": (
            "Task 1: [Epic A] Bret initiates GitHub repo transfer to OmniNode-ai"
        ),
        "is_seam_ticket": True,
        "interface_change": True,
        "interfaces_touched": ["public_api"],
    }
    assert classify_ticket(contract) == "governance"


def test_classify_branch_protection_is_governance() -> None:
    contract = {
        "summary": "Configure v2 main branch protection + merge queue",
        "is_seam_ticket": True,
        "interfaces_touched": ["public_api"],
    }
    assert classify_ticket(contract) == "governance"


def test_classify_workflow_lift_is_governance() -> None:
    """OMN-9832 ('Lift 5 zero-adapt workflows') is a config copy task — no
    pytest equivalent exists, so it should be governance.
    """
    contract = {
        "summary": "Task 3: [Epic A] Lift 5 zero-adapt workflows from v1 omnidash",
        "is_seam_ticket": False,
        "interfaces_touched": [],
    }
    assert classify_ticket(contract) == "governance"


def test_classify_doc_edit_is_governance() -> None:
    contract = {
        "summary": "Update omnidash-v2/CLAUDE.md to reference OmniNode-ai org",
        "is_seam_ticket": False,
        "interfaces_touched": [],
    }
    assert classify_ticket(contract) == "governance"


def test_classify_make_public_is_governance() -> None:
    contract = {
        "summary": "Make repo public — IRREVERSIBLE PUBLICATION GATE",
        "is_seam_ticket": True,
        "interfaces_touched": ["public_api"],
    }
    assert classify_ticket(contract) == "governance"


def test_classify_proof_of_life_is_governance() -> None:
    contract = {
        "summary": "Proof of Life — End-to-End Verification",
        "is_seam_ticket": False,
        "interfaces_touched": [],
    }
    assert classify_ticket(contract) == "governance"


def test_classify_real_code_change_is_code() -> None:
    """A real code-touching ticket must keep the pytest+CI evidence pair."""
    contract = {
        "summary": "Add new validator for ModelDispatchItem in omnimarket",
        "is_seam_ticket": True,
        "interfaces_touched": ["events"],
    }
    assert classify_ticket(contract) == "code"


def test_classify_uncategorised_is_governance() -> None:
    """When no signal is present (no surfaces, not seam, no governance kw),
    governance is the safer default — the failure mode of falsely emitting
    pytest is what motivated this script.
    """
    contract = {
        "summary": "Unspecified work",
        "is_seam_ticket": False,
        "interfaces_touched": [],
    }
    assert classify_ticket(contract) == "governance"


# ---------------------------------------------------------------------------
# make_dod_evidence — the two ticket classes must produce DIFFERENT evidence
# ---------------------------------------------------------------------------


def test_make_dod_evidence_governance_does_not_emit_pytest() -> None:
    """Critical regression assertion: governance tickets must NOT receive a
    pytest check. The whole point of OMN-10086 is to stop blanket-stamping
    ``uv run pytest`` on tickets that have no test surface.
    """
    evidence = make_dod_evidence("governance", "OMN-9829")
    for item in evidence:
        for check in item["checks"]:
            assert "pytest" not in check["check_value"], (
                f"governance evidence must not include pytest, "
                f"got: {check['check_value']!r}"
            )


def test_make_dod_evidence_code_emits_pytest_and_ci() -> None:
    evidence = make_dod_evidence("code", "OMN-9999")
    check_values = [c["check_value"] for item in evidence for c in item["checks"]]
    assert any("uv run pytest" in v for v in check_values), check_values
    assert any("gh pr checks" in v for v in check_values), check_values


def test_make_dod_evidence_uses_pr_number_template() -> None:
    """Code-class evidence must template ``${PR_NUMBER}`` and ``${REPO}`` so
    the runner can substitute them — bare ``gh pr checks`` (no PR arg) is the
    failure mode that PR #452 hit.
    """
    evidence = make_dod_evidence("code", "OMN-9999")
    check_values = [c["check_value"] for item in evidence for c in item["checks"]]
    gh_checks = [v for v in check_values if "gh pr checks" in v]
    assert gh_checks, "code class must include gh pr checks"
    for v in gh_checks:
        assert "${PR_NUMBER}" in v, f"PR_NUMBER template missing in: {v!r}"
        assert "${REPO}" in v, f"REPO template missing in: {v!r}"


def test_make_dod_evidence_governance_uses_ticket_id_template() -> None:
    """Governance evidence references the contract by TICKET_ID, not by
    hard-coded ticket number.
    """
    evidence = make_dod_evidence("governance", "OMN-9829")
    check_values = [c["check_value"] for item in evidence for c in item["checks"]]
    yaml_checks = [v for v in check_values if "yaml" in v.lower()]
    assert yaml_checks, "governance class must include a YAML-validity check"
    for v in yaml_checks:
        assert "${TICKET_ID}" in v, f"TICKET_ID template missing in: {v!r}"


def test_make_dod_evidence_dispatcher_branches_differently() -> None:
    """Direct assertion that the two ticket classes produce different evidence."""
    code = make_dod_evidence("code", "OMN-1")
    governance = make_dod_evidence("governance", "OMN-1")
    assert code != governance


# ---------------------------------------------------------------------------
# needs_migration — also catches the legacy bare ``gh pr checks`` pattern
# ---------------------------------------------------------------------------


def test_needs_migration_when_dod_evidence_missing() -> None:
    contract = {"summary": "x", "is_seam_ticket": False, "interfaces_touched": []}
    assert needs_migration(contract) is True


def test_needs_migration_for_legacy_bare_gh_pr_checks() -> None:
    """Contracts stamped by the prior one-shot migration carry a bare
    ``gh pr checks`` (no PR arg). These must be re-migrated.
    """
    contract = {
        "dod_evidence": [
            {
                "id": "dod-002",
                "description": "ci",
                "checks": [{"check_type": "command", "check_value": "gh pr checks"}],
            },
        ],
    }
    assert needs_migration(contract) is True


def test_needs_migration_for_legacy_gh_pr_view_state() -> None:
    contract = {
        "dod_evidence": [
            {
                "id": "dod-001",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": "gh pr view --json state -q .state",
                    }
                ],
            },
        ],
    }
    assert needs_migration(contract) is True


def test_needs_migration_for_legacy_gh_pr_view_merged_at() -> None:
    contract = {
        "dod_evidence": [
            {
                "id": "dod-002",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": "gh pr view --json mergedAt -q .mergedAt",
                    }
                ],
            },
        ],
    }
    assert needs_migration(contract) is True


def test_needs_migration_for_legacy_gh_pr_checks_watch() -> None:
    contract = {
        "dod_evidence": [
            {
                "id": "dod-003",
                "checks": [
                    {"check_type": "command", "check_value": "gh pr checks --watch"}
                ],
            },
        ],
    }
    assert needs_migration(contract) is True


def test_needs_migration_when_gh_pr_command_missing_repo_context() -> None:
    contract = {
        "dod_evidence": [
            {
                "id": "dod-003",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": "gh pr checks ${PR_NUMBER}",
                    }
                ],
            },
        ],
    }
    assert needs_migration(contract) is True


def test_does_not_need_migration_when_gh_pr_view_already_templated() -> None:
    contract = {
        "dod_evidence": [
            {
                "id": "dod-002",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": (
                            "gh pr view ${PR_NUMBER} --repo ${REPO} "
                            "--json title,body --jq '.title'"
                        ),
                    }
                ],
            },
        ],
    }
    assert needs_migration(contract) is False


def test_does_not_need_migration_when_already_templated() -> None:
    contract = {
        "dod_evidence": [
            {
                "id": "dod-001",
                "description": "ci",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": "gh pr checks ${PR_NUMBER} --repo ${REPO}",
                    }
                ],
            }
        ],
    }
    assert needs_migration(contract) is False


# ---------------------------------------------------------------------------
# migrate_contract_file — round-trip on a temp directory
# ---------------------------------------------------------------------------


def test_migrate_contract_file_governance_round_trip(tmp_path: Path) -> None:
    """Process ticket gets governance evidence written back to disk."""
    path = tmp_path / "OMN-9829.yaml"
    summary = "'Task 1: [Epic A] Bret initiates GitHub repo transfer to OmniNode-ai'"
    path.write_text(
        textwrap.dedent(
            f"""\
            ---
            schema_version: 1.0.0
            ticket_id: OMN-9829
            summary: {summary}
            is_seam_ticket: true
            interface_change: true
            interfaces_touched:
              - public_api
            evidence_requirements: []
            emergency_bypass:
              enabled: false
              justification: ''
              follow_up_ticket_id: ''
            """
        )
    )
    migrated, klass = migrate_contract_file(path, apply=True)
    assert migrated is True
    assert klass == "governance"

    written = yaml.safe_load(path.read_text())
    dod = written["dod_evidence"]
    assert len(dod) == 2
    flat = " ".join(c["check_value"] for item in dod for c in item["checks"])
    assert "pytest" not in flat
    assert "${TICKET_ID}" in flat


def test_migrate_contract_file_dry_run_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "OMN-1234.yaml"
    original = textwrap.dedent(
        """\
        ---
        schema_version: 1.0.0
        ticket_id: OMN-1234
        summary: 'Refactor model_widget validation'
        is_seam_ticket: false
        interface_change: false
        interfaces_touched: []
        evidence_requirements: []
        emergency_bypass:
          enabled: false
          justification: ''
          follow_up_ticket_id: ''
        """
    )
    path.write_text(original)
    migrated, klass = migrate_contract_file(path, apply=False)
    assert migrated is True
    assert klass == "governance"  # uncategorised → governance default
    # File on disk is unchanged in dry-run
    assert path.read_text() == original


def test_migrate_contract_file_skips_already_migrated(tmp_path: Path) -> None:
    path = tmp_path / "OMN-9999.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            ---
            schema_version: 1.0.0
            ticket_id: OMN-9999
            summary: 'Already migrated'
            is_seam_ticket: false
            interface_change: false
            interfaces_touched: []
            evidence_requirements: []
            emergency_bypass:
              enabled: false
              justification: ''
              follow_up_ticket_id: ''
            dod_evidence:
              - id: dod-001
                description: 'CI green'
                source: generated
                checks:
                  - check_type: command
                    check_value: 'gh pr checks ${PR_NUMBER} --repo ${REPO}'
            """
        )
    )
    snapshot = path.read_text()
    migrated, klass = migrate_contract_file(path, apply=True)
    assert migrated is False
    assert klass is None
    assert path.read_text() == snapshot


# ---------------------------------------------------------------------------
# Schema validity — emitted evidence must satisfy ModelTicketContract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("klass", ["code", "governance"])
def test_emitted_evidence_validates_against_model(klass: str) -> None:
    """Both classes' evidence must round-trip through ModelDodEvidenceItem."""
    from onex_change_control.models.model_ticket_contract import ModelDodEvidenceItem

    evidence = make_dod_evidence(klass, "OMN-9999")  # type: ignore[arg-type]
    for item in evidence:
        ModelDodEvidenceItem.model_validate(item)
