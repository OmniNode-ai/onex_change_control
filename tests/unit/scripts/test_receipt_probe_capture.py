# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-20370: a hand-authored receipt's probe_stdout comes from a captured run.

RED/GREEN controls for the PROBE_CAPTURE leg of
``scripts/validation/check_receipt_hardening.py``. The gate is proven against
the live defect it was built for, the receipt onex_change_control#12323 merged
for omnibase_infra#4462 (its probe_stdout names a test file absent at the
commit it pins), not only against synthetic fixtures.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml
from omnibase_core.models.contracts.ticket.model_dod_receipt import ModelDodReceipt

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LIVE_FABRICATED = Path(
    "drift/dod_receipts/OMN-19212/dod-OmniNode-ai-omnibase_infra-pr-4462-ci/command.yaml"
)
_GREEN = "a" * 40
_RED = "b" * 40


def _load_gate() -> Any:
    """Load the validator by path (``scripts/validation`` is not a package)."""
    script_path = _REPO_ROOT / "scripts" / "validation" / "check_receipt_hardening.py"
    spec = importlib.util.spec_from_file_location(
        "check_receipt_hardening_probe_capture", script_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate: Any = _load_gate()


def _receipt(probe_command: str, *, status: str = "PASS") -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "ticket_id": "OMN-20370",
        "evidence_item_id": "dod-capture-fixture",
        "check_type": "command",
        "check_value": probe_command,
        "contract_entry_sha256": "sha256:" + "0" * 64,
        "status": status,
        "run_timestamp": "2026-10-02T00:00:00Z",
        "commit_sha": _GREEN,
        "runner": "receipt-audit-0c2f",
        "verifier": "hand-typed",
        "probe_command": probe_command,
        "probe_stdout": "typed by hand",
        "exit_code": 0,
        "pr_number": 1,
    }


def _write(tmp_path: Path, data: dict[str, Any], name: str = "command.yaml") -> Path:
    receipts = tmp_path / "drift" / "dod_receipts" / "OMN-20370" / "dod-capture-fixture"
    receipts.mkdir(parents=True, exist_ok=True)
    path = receipts / name
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _relative(path: Path, root: Path) -> Path:
    return path.relative_to(root)


def test_capture_writes_fields_from_the_run(tmp_path: Path) -> None:
    path = _write(tmp_path, _receipt("printf 'first  \\nsecond\\n'"))
    written = gate.capture_probe(path, tmp_path)

    assert written["probe_stdout"] == "first\nsecond"
    assert written["exit_code"] == 0
    assert written["verifier"] == gate.PROBE_CAPTURE_VERIFIER
    assert isinstance(written["duration_ms"], int)
    assert written["artifact_sha256"] == gate.probe_capture_record(
        written["probe_command"], "first\nsecond"
    )
    assert "probe_stdout: |" in path.read_text()  # a literal block survives yamlfmt
    on_disk = yaml.safe_load(path.read_text())
    assert on_disk["probe_stdout"] == "first\nsecond"
    assert on_disk["run_timestamp"] != "2026-10-02T00:00:00Z"
    ModelDodReceipt.model_validate(on_disk)


def test_capture_refuses_a_probe_that_prints_nothing(tmp_path: Path) -> None:
    path = _write(tmp_path, _receipt("true"))
    with pytest.raises(gate.ProbeCaptureError, match="printed nothing"):
        gate.capture_probe(path, tmp_path)
    assert yaml.safe_load(path.read_text())["probe_stdout"] == "typed by hand"


def test_capture_refuses_a_pass_receipt_whose_probe_fails(tmp_path: Path) -> None:
    path = _write(tmp_path, _receipt("echo partial; exit 3"))
    with pytest.raises(gate.ProbeCaptureError, match="exited 3"):
        gate.capture_probe(path, tmp_path)


def test_capture_records_a_red_green_proof_from_two_runs(tmp_path: Path) -> None:
    probe = f"echo {_GREEN} | grep -c ^a"
    path = _write(tmp_path, _receipt(probe))
    written = gate.capture_probe(path, tmp_path, red_ref=_RED)

    assert gate._red_green_proof(written["probe_stdout"]) == {
        "evidence_ref": _GREEN,
        "green_exit": 0,
        "red_exit": 1,
        "red_ref": _RED,
    }
    assert gate.probe_capture_violations(written, "fixture") == []


def test_capture_refuses_a_red_green_proof_whose_red_leg_passes(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, _receipt(f"echo {_GREEN}"))
    with pytest.raises(gate.ProbeCaptureError, match="red leg"):
        gate.capture_probe(path, tmp_path, red_ref=_RED)


def test_gate_refuses_the_live_fabricated_receipt_from_12323(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(_REPO_ROOT)
    found = gate.run_probe_capture_gate([_LIVE_FABRICATED], "omn-19212-companion")
    assert len(found) == 1
    assert gate.PROBE_CAPTURE_RULE in found[0]
    assert "--capture-probe" in found[0]


def test_gate_refuses_added_receipt_without_capture_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path, _receipt("echo 4"))
    violations = gate.run_probe_capture_gate(
        [_relative(path, tmp_path)], "jonah/omn-1-companion"
    )
    assert len(violations) == 1
    assert "verifier is not" in violations[0]


def test_gate_admits_a_captured_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path, _receipt("echo 4"))
    gate.capture_probe(path, tmp_path)
    assert gate.run_probe_capture_gate([_relative(path, tmp_path)], "jonah/omn-1") == []


def test_gate_refuses_stdout_edited_after_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #12266 shape: the run printed 4 and the receipt says 1."""
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path, _receipt("echo 4"))
    gate.capture_probe(path, tmp_path)
    data = yaml.safe_load(path.read_text())
    data["probe_stdout"] = "1"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    violations = gate.run_probe_capture_gate([_relative(path, tmp_path)], "jonah/x")
    assert len(violations) == 1
    assert "artifact_sha256" in violations[0]


def test_gate_tolerates_formatter_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path, _receipt("printf '3 passed\\n'"))
    gate.capture_probe(path, tmp_path)
    data = yaml.safe_load(path.read_text())
    data["probe_stdout"] = data["probe_stdout"] + "   \n\n"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    assert gate.run_probe_capture_gate([_relative(path, tmp_path)], "jonah/x") == []


def test_gate_exempts_the_autobind_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path, _receipt("echo 4"))
    branch = "auto/window-omninode-ai-omnibase_infra-occ-autobind"
    assert gate.run_probe_capture_gate([_relative(path, tmp_path)], branch) == []


def test_gate_checks_a_supersede_replacement_and_skips_a_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    base = "drift/dod_receipts/OMN-20370/dod-capture-fixture/command.yaml"
    supersede = _write(
        tmp_path,
        {"supersedes": base, "replacement": _receipt("echo 4"), "tombstone": False},
        "command.supersede.1.yaml",
    )
    tombstone = _write(
        tmp_path,
        {"supersedes": base, "tombstone": True},
        "command.supersede.2.yaml",
    )
    refused = gate.run_probe_capture_gate(
        [_relative(supersede, tmp_path), _relative(tombstone, tmp_path)], "jonah/x"
    )
    assert len(refused) == 1
    assert "supersede.1" in refused[0]

    gate.capture_probe(supersede, tmp_path)
    assert (
        gate.run_probe_capture_gate([_relative(supersede, tmp_path)], "jonah/x") == []
    )


def test_ci_wiring_carries_the_added_receipt_check() -> None:
    failures = gate.check_commit_sha_wiring(
        _REPO_ROOT / ".pre-commit-config.yaml",
        _REPO_ROOT / ".github" / "workflows" / "ci.yml",
    )
    assert failures == []


def test_ci_wiring_refuses_removal_of_the_added_receipt_check(tmp_path: Path) -> None:
    ci_text = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    stripped = tmp_path / "ci.yml"
    stripped.write_text(
        ci_text.replace('--probe-capture-added-file0 "$added_paths_file"', "")
    )
    failures = gate.check_commit_sha_wiring(
        _REPO_ROOT / ".pre-commit-config.yaml", stripped
    )
    assert any("--probe-capture-added-file0" in failure for failure in failures)
