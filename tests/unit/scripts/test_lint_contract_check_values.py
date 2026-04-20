# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Unit tests for scripts/lint_contract_check_values.py.

Coverage:
  1. Each anti-pattern category exits non-zero when present
  2. Fail-closed patterns pass (exit 0)
  3. Contracts with no dod_evidence are clean
  4. YAML parse errors are reported
  5. Flat check_value at item level (legacy schema) is also scanned
  6. Multiple findings across multiple files are all reported
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Add scripts dir to sys.path so we can import the module under test
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import lint_contract_check_values as linter  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_contract(tmp_path: Path, check_value: str, dod_id: str = "dod-003") -> Path:
    """Write a minimal contract YAML with one dod_evidence item.

    Uses yaml.dump to ensure complex check_value strings (containing brackets,
    quotes, semicolons) are always serialized as valid YAML.
    """
    data = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-TEST",
        "summary": "test contract",
        "is_seam_ticket": False,
        "interface_change": False,
        "interfaces_touched": [],
        "evidence_requirements": [],
        "dod_evidence": [
            {
                "id": dod_id,
                "description": "CI check",
                "source": "generated",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": check_value,
                    }
                ],
            }
        ],
        "emergency_bypass": {
            "enabled": False,
            "justification": "",
            "follow_up_ticket_id": "",
        },
    }
    p = tmp_path / "OMN-TEST.yaml"
    p.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True), encoding="utf-8"
    )
    return p


def write_flat_contract(tmp_path: Path, check_value: str) -> Path:
    """Write a contract with check_value at the item level (legacy schema)."""
    data = {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-FLAT",
        "dod_evidence": [
            {
                "id": "dod-003",
                "check_value": check_value,
            }
        ],
    }
    p = tmp_path / "OMN-FLAT.yaml"
    p.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True), encoding="utf-8"
    )
    return p


# ---------------------------------------------------------------------------
# Anti-pattern tests — each should produce a non-zero exit
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_empty_permissive_z_pattern(tmp_path: Path) -> None:
    """[ -z "$result" ] || should be flagged as fail-open."""
    bad = '[ -z "$result" ] || [ "$result" = "SUCCESS" ]'
    path = write_contract(tmp_path, bad)
    findings = linter.lint_contract(path)
    assert findings, f"Expected findings for: {bad}"
    assert any("empty-permissive" in label for _, label, _ in findings)


@pytest.mark.unit
def test_trailing_or_true(tmp_path: Path) -> None:
    """|| true at the end should be flagged."""
    bad = "some_command || true"
    path = write_contract(tmp_path, bad)
    findings = linter.lint_contract(path)
    assert findings, f"Expected findings for: {bad}"
    assert any("trailing || true" in label for _, label, _ in findings)


@pytest.mark.unit
def test_trailing_or_exit_0(tmp_path: Path) -> None:
    """|| exit 0 should be flagged."""
    bad = "some_command || exit 0"
    path = write_contract(tmp_path, bad)
    findings = linter.lint_contract(path)
    assert findings, f"Expected findings for: {bad}"
    assert any("trailing || exit 0" in label for _, label, _ in findings)


@pytest.mark.unit
def test_silenced_2_dev_null_at_end(tmp_path: Path) -> None:
    """2>/dev/null at end of fragment (no explicit exit check) should be flagged."""
    bad = "some_command 2>/dev/null"
    path = write_contract(tmp_path, bad)
    findings = linter.lint_contract(path)
    assert findings, f"Expected findings for: {bad}"
    assert any("2>/dev/null" in label for _, label, _ in findings)


@pytest.mark.unit
def test_empty_permissive_brace_variable(tmp_path: Path) -> None:
    """Brace-wrapped vars like ${result} in [ -z ... ] || should also be flagged."""
    bad = '[ -z "${result}" ] || [ "$result" = "SUCCESS" ]'
    path = write_contract(tmp_path, bad)
    findings = linter.lint_contract(path)
    assert findings, f"Expected findings for: {bad}"
    assert any("empty-permissive" in label for _, label, _ in findings)


@pytest.mark.unit
def test_2_dev_null_mid_fragment_not_flagged(tmp_path: Path) -> None:
    """2>/dev/null at a line boundary with a valid exit check should NOT be flagged.

    Uses \\Z (absolute fragment end) instead of MULTILINE $ to avoid false positives.
    """
    good = 'cmd 2>/dev/null\n[ "$result" = "SUCCESS" ]'
    path = write_contract(tmp_path, good)
    findings = linter.lint_contract(path)
    assert not findings, (
        f"Unexpected findings for valid multi-line fragment: {findings}"
    )


# ---------------------------------------------------------------------------
# Clean / fail-closed patterns — should produce zero findings
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fail_closed_success_check_is_clean(tmp_path: Path) -> None:
    """Canonical fail-closed pattern should not be flagged."""
    check_name = "Validate Contract YAML (OMN-8808)"
    good = (
        "result=$(gh pr view {pr} --repo {repo} --json statusCheckRollup "
        f'-q \'[.statusCheckRollup[] | select(.name == "{check_name}") '
        "| .conclusion] | first // empty'); "
        '[ "$result" = "SUCCESS" ]'
    )
    path = write_contract(tmp_path, good)
    findings = linter.lint_contract(path)
    assert not findings, f"Unexpected findings: {findings}"


@pytest.mark.unit
def test_clean_pr_state_check(tmp_path: Path) -> None:
    """PR state + baseRefName check should not be flagged."""
    good = (
        "state=$(gh pr view {pr} --repo {repo} --json state,baseRefName "
        "-q '[.state, .baseRefName] | @tsv'); "
        '[ "$(echo "$state" | cut -f1)" = "OPEN" ] && '
        '[ "$(echo "$state" | cut -f2)" = "main" ]'
    )
    path = write_contract(tmp_path, good)
    findings = linter.lint_contract(path)
    assert not findings, f"Unexpected findings: {findings}"


@pytest.mark.unit
def test_file_existence_check_is_clean(tmp_path: Path) -> None:
    """test -f ... is fail-closed and should not be flagged."""
    good = "test -f contracts/OMN-TEST.yaml"
    path = write_contract(tmp_path, good)
    findings = linter.lint_contract(path)
    assert not findings, f"Unexpected findings: {findings}"


# ---------------------------------------------------------------------------
# Edge-case and structural tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_dod_evidence_is_clean(tmp_path: Path) -> None:
    """Contract with no dod_evidence should be clean."""
    data = {"schema_version": "1.0.0", "ticket_id": "OMN-EMPTY", "dod_evidence": []}
    p = tmp_path / "OMN-EMPTY.yaml"
    p.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True), encoding="utf-8"
    )
    findings = linter.lint_contract(p)
    assert not findings


@pytest.mark.unit
def test_yaml_parse_error_reported(tmp_path: Path) -> None:
    """Malformed YAML should produce a yaml-parse-error finding (not crash)."""
    p = tmp_path / "BAD.yaml"
    p.write_text("key: [\n  unclosed bracket\n", encoding="utf-8")
    findings = linter.lint_contract(p)
    assert findings
    assert any("yaml-parse-error" in label for _, label, _ in findings)


@pytest.mark.unit
def test_flat_check_value_at_item_level_is_scanned(tmp_path: Path) -> None:
    """Legacy flat check_value at item level should also be scanned."""
    bad = '[ -z "$result" ] || [ "$result" = "SUCCESS" ]'
    path = write_flat_contract(tmp_path, bad)
    findings = linter.lint_contract(path)
    assert findings, "Flat check_value at item level should be scanned"


# ---------------------------------------------------------------------------
# main() exit-code tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_returns_1_for_bad_contract(tmp_path: Path) -> None:
    """main() should return 1 when a bad contract is passed."""
    bad = '[ -z "$result" ] || [ "$result" = "SUCCESS" ]'
    path = write_contract(tmp_path, bad)
    rc = linter.main(["lint_contract_check_values.py", str(path)])
    assert rc == 1


@pytest.mark.unit
def test_main_returns_0_for_clean_contract(tmp_path: Path) -> None:
    """main() should return 0 when no bad patterns are present."""
    good = '[ "$result" = "SUCCESS" ]'
    path = write_contract(tmp_path, good)
    rc = linter.main(["lint_contract_check_values.py", str(path)])
    assert rc == 0


@pytest.mark.unit
def test_main_returns_2_with_no_args() -> None:
    """main() with no file arguments should return 2 (usage error)."""
    rc = linter.main(["lint_contract_check_values.py"])
    assert rc == 2


@pytest.mark.unit
def test_main_reports_all_files(tmp_path: Path) -> None:
    """main() should report findings across multiple files."""
    bad = '[ -z "$result" ] || [ "$result" = "SUCCESS" ]'
    p1 = tmp_path / "A.yaml"
    p2 = tmp_path / "B.yaml"
    for p in (p1, p2):
        data = {
            "dod_evidence": [
                {
                    "id": "dod-003",
                    "checks": [{"check_type": "command", "check_value": bad}],
                }
            ]
        }
        p.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
    rc = linter.main(["lint_contract_check_values.py", str(p1), str(p2)])
    assert rc == 1
