# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Unit tests for ``scripts/check_dod_evidence_present.py``.

Coverage:
  1. Canonical contract with non-empty ``dod_evidence`` passes (exit 0)
  2. Contract missing ``dod_evidence`` key fails (exit 1)
  3. Contract with empty ``dod_evidence: []`` fails (exit 1)
  4. Non-OMN filenames are skipped silently (exit 0)
  5. Multi-file invocation surfaces every failing path
  6. Malformed YAML is reported as an error
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Add scripts dir to sys.path so we can import the module under test
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import check_dod_evidence_present as guard  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canonical_contract(ticket_id: str = "OMN-9999") -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "ticket_id": ticket_id,
        "summary": "test contract",
        "is_seam_ticket": False,
        "interface_change": False,
        "interfaces_touched": [],
        "evidence_requirements": [],
        "emergency_bypass": {
            "enabled": False,
            "justification": "",
            "follow_up_ticket_id": "",
        },
        "dod_evidence": [
            {
                "id": "dod-001",
                "description": "PR opened",
                "source": "generated",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": "gh pr view --json state -q .state | grep OPEN",
                    }
                ],
            }
        ],
    }


def _write_contract(
    tmp_path: Path,
    name: str,
    overrides: dict[str, Any] | None = None,
    drop_keys: tuple[str, ...] = (),
) -> Path:
    """Materialize a contract YAML and return its on-disk ``contracts/...``
    path so the script's OMN-detection regex matches.
    """
    data = _canonical_contract()
    if overrides:
        data.update(overrides)
    for key in drop_keys:
        data.pop(key, None)

    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    p = contracts_dir / name
    p.write_text(yaml.safe_dump(data, default_flow_style=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_canonical_contract_passes(tmp_path: Path) -> None:
    contract = _write_contract(tmp_path, "OMN-1234.yaml")
    rc = guard.main(["check_dod_evidence_present.py", str(contract)])
    assert rc == 0


@pytest.mark.unit
def test_missing_dod_evidence_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contract = _write_contract(tmp_path, "OMN-1234.yaml", drop_keys=("dod_evidence",))
    rc = guard.main(["check_dod_evidence_present.py", str(contract)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "missing or empty dod_evidence block" in err
    assert "OMN-1234.yaml" in err


@pytest.mark.unit
def test_empty_dod_evidence_list_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contract = _write_contract(
        tmp_path, "OMN-1234.yaml", overrides={"dod_evidence": []}
    )
    rc = guard.main(["check_dod_evidence_present.py", str(contract)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "missing or empty dod_evidence block" in err


@pytest.mark.unit
def test_dod_evidence_wrong_type_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Pydantic would reject a non-list eventually, but the guard should also
    # treat it as failing -- a string or mapping is not a populated list.
    contract = _write_contract(
        tmp_path, "OMN-1234.yaml", overrides={"dod_evidence": "see linear"}
    )
    rc = guard.main(["check_dod_evidence_present.py", str(contract)])
    assert rc == 1
    assert "missing or empty dod_evidence block" in capsys.readouterr().err


@pytest.mark.unit
def test_non_omn_filename_is_skipped(tmp_path: Path) -> None:
    # File lives outside contracts/ -- guard skips it even if dod_evidence
    # is missing. The pre-commit ``files:`` filter is the gate; this test
    # only proves the script itself doesn't accidentally fail clean inputs.
    other = tmp_path / "templates" / "ticket_contract.template.yaml"
    other.parent.mkdir()
    other.write_text("schema_version: '1.0.0'\n", encoding="utf-8")

    rc = guard.main(["check_dod_evidence_present.py", str(other)])
    assert rc == 0


@pytest.mark.unit
def test_non_omn_basename_in_contracts_dir_is_skipped(tmp_path: Path) -> None:
    # contracts/foo.yaml -- not an OMN ticket -- must skip.
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    p = contracts_dir / "foo.yaml"
    p.write_text("schema_version: '1.0.0'\n", encoding="utf-8")

    rc = guard.main(["check_dod_evidence_present.py", str(p)])
    assert rc == 0


@pytest.mark.unit
def test_multiple_files_all_failures_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = _write_contract(tmp_path, "OMN-0001.yaml")
    bad1 = _write_contract(tmp_path, "OMN-0002.yaml", drop_keys=("dod_evidence",))
    bad2 = _write_contract(tmp_path, "OMN-0003.yaml", overrides={"dod_evidence": []})

    rc = guard.main(["check_dod_evidence_present.py", str(good), str(bad1), str(bad2)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "OMN-0002.yaml" in err
    assert "OMN-0003.yaml" in err
    # Passing contract should not appear in the failure report.
    assert "OMN-0001.yaml" not in err


@pytest.mark.unit
def test_malformed_yaml_reports_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    p = contracts_dir / "OMN-9999.yaml"
    p.write_text("schema_version: '1.0.0'\n  bad: [unterminated", encoding="utf-8")

    rc = guard.main(["check_dod_evidence_present.py", str(p)])
    assert rc == 1
    assert "YAML parse error" in capsys.readouterr().err


@pytest.mark.unit
def test_no_arguments_passes() -> None:
    # pre-commit invokes hooks with zero filenames when nothing matches.
    rc = guard.main(["check_dod_evidence_present.py"])
    assert rc == 0
