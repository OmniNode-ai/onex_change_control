# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Per-leg unit tests for the contract-shape-v1 gate (OMN-15669, R-0802-9).

These are the leg-level RED/GREEN discriminators. The end-to-end cases the
contract itself declares live in ``tests/test_contract_shape_v1_cases.py``;
this module proves each primitive those cases stand on.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from onex_change_control.testing.seam_binding import (
    SEAM_BINDINGS,
    SeamShapeError,
    assert_seam_shape,
    binding_params,
    resolve_seam_schema,
)
from onex_change_control.validation.contract_shape_v1 import (
    CONTRACT_BLOCK_HEADING,
    SCHEMA_PATH,
    Finding,
    LinearUnreachableError,
    PytestCollector,
    canonicalize,
    check_identity,
    check_schema,
    enumerate_case_space,
    extract_contract_block,
    load_schema,
    load_ticket_bodies,
    main,
    make_ticket_body_reader,
    parse_contract_trailers,
    select_scope,
    sha256_block,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANT_ROOT = REPO_ROOT / "tests" / "fixtures" / "contract_shape_v1" / "conformant"


class _Reader:
    def __init__(self, table: dict[str, str | None]) -> None:
        self.table = table

    def read(self, path: str) -> str | None:
        return self.table.get(path)


# ---------------------------------------------------------------------------
# P1 — exactly one schema artifact.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_exactly_one_contract_schema_artifact() -> None:
    """One-model-per-shape: the repo carries exactly one v1 contract schema.

    A second artifact is the defect this rule exists to prevent, so the count
    is asserted rather than assumed.
    """
    assert SCHEMA_PATH.exists()
    matches = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in REPO_ROOT.glob("**/occ_contract_v*.schema.yaml")
        if ".venv" not in p.parts
    )
    assert matches == ["schemas/occ_contract_v1.schema.yaml"], matches


@pytest.mark.unit
def test_schema_declares_the_p3_p6_blocks() -> None:
    """The one schema is what makes cases/dependencies/taxonomy mandatory."""
    schema = load_schema()
    assert set(schema["required"]) >= {
        "schema_version",
        "ticket_id",
        "interface",
        "dependencies",
        "cases",
        "dod_evidence",
    }
    assert schema["properties"]["cases"]["minItems"] == 1
    interface = schema["properties"]["interface"]
    assert interface["properties"]["error_taxonomy"]["minItems"] == 1
    case = schema["$defs"]["case"]
    assert set(case["properties"]["class"]["enum"]) == {
        "unit",
        "golden_chain",
        "error_chain",
    }
    assert set(case["properties"]["bindings"]["enum"]) == {"mock", "real", "both"}
    assert schema["$defs"]["dependency"]["properties"]["injectable"]["const"] is True


@pytest.mark.unit
def test_contract_without_cases_is_malformed() -> None:
    """P3: no cases block => MALFORMED, so cases cannot be written after code."""
    contract = yaml.safe_load(
        (CONFORMANT_ROOT / "contracts" / "v1" / "OMN-99999.yaml").read_text(
            encoding="utf-8"
        )
    )
    del contract["cases"]
    assert [f.rule for f in check_schema(contract, "x")] == ["shape_invalid"]


# ---------------------------------------------------------------------------
# P2 — canonicalization, extraction, trailers, identity blindness.
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("a: 1\n", "a: 1"),
        ("a: 1\n", "a: 1\n\n\n"),
        ("a: 1\r\n", "a: 1\n"),
        ("a: 1\r", "a: 1\n"),
    ],
)
def test_canonicalize_collapses_line_ending_noise(left: str, right: str) -> None:
    assert canonicalize(left) == canonicalize(right)
    assert sha256_block(left) == sha256_block(right)


@pytest.mark.unit
def test_canonicalize_does_not_collapse_real_differences() -> None:
    assert sha256_block("a: 1\n") != sha256_block("a: 2\n")
    assert sha256_block("a: 1\nb: 2\n") != sha256_block("a: 1\n\nb: 2\n")


@pytest.mark.unit
def test_extract_contract_block_takes_exactly_the_first_fence() -> None:
    body = (
        "intro\n\n"
        f"{CONTRACT_BLOCK_HEADING}\n\n```yaml\nticket_id: OMN-1\n```\n\n"
        "outro\n\n```yaml\nnot: the contract\n```\n"
    )
    assert extract_contract_block(body) == "ticket_id: OMN-1"


@pytest.mark.unit
def test_extract_contract_block_absent_returns_none() -> None:
    assert extract_contract_block("no heading here\n```yaml\na: 1\n```") is None
    assert extract_contract_block("") is None


@pytest.mark.unit
def test_parse_contract_trailers() -> None:
    sha = "a" * 64
    body = f"prose\n\nContract-Ticket-Hash: OMN-15669={sha}\nmore\n"
    assert parse_contract_trailers(body) == {"OMN-15669": sha}
    assert parse_contract_trailers("Contract-Ticket-Hash: OMN-1=short") == {}
    assert parse_contract_trailers("") == {}


@pytest.mark.unit
def test_identity_is_blind_to_actor() -> None:
    """The verdict is a pure function of the three artifacts.

    ``check_identity`` takes no author, approver, or bot parameter, so there is
    no input on which an actor-conditional branch could exist.
    """
    import inspect

    params = set(inspect.signature(check_identity).parameters)
    assert params == {"ticket_id", "contract_text", "pr_body", "ticket_body"}
    assert not (
        params & {"actor", "author", "approved_by", "login", "user", "bot", "identity"}
    )


@pytest.mark.unit
def test_identity_green_when_all_three_agree() -> None:
    text = "ticket_id: OMN-1\nvalue: 7\n"
    sha = sha256_block(text)
    body = f"{CONTRACT_BLOCK_HEADING}\n\n```yaml\n{text.rstrip()}\n```\n"
    assert (
        check_identity("OMN-1", text, f"Contract-Ticket-Hash: OMN-1={sha}\n", body)
        == []
    )


@pytest.mark.unit
def test_identity_missing_trailer_is_red() -> None:
    text = "ticket_id: OMN-1\n"
    body = f"{CONTRACT_BLOCK_HEADING}\n\n```yaml\n{text.rstrip()}\n```\n"
    rules = [f.rule for f in check_identity("OMN-1", text, "no trailer here", body)]
    assert rules == ["identity_trailer_missing"]


@pytest.mark.unit
def test_identity_missing_block_is_red() -> None:
    text = "ticket_id: OMN-1\n"
    sha = sha256_block(text)
    rules = [
        f.rule
        for f in check_identity(
            "OMN-1", text, f"Contract-Ticket-Hash: OMN-1={sha}\n", "no block"
        )
    ]
    assert rules == ["identity_block_missing"]


@pytest.mark.unit
def test_ticket_body_absent_is_unreachable_not_skip(tmp_path: Path) -> None:
    """A missing/empty/unparseable body map is RED, never a silent pass."""
    assert load_ticket_bodies(None) == {}
    assert load_ticket_bodies(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all")
    assert load_ticket_bodies(bad) == {}
    good = tmp_path / "good.json"
    good.write_text('{"OMN-1": "body text", "OMN-2": 7}')
    assert load_ticket_bodies(good) == {"OMN-1": "body text"}

    reader = make_ticket_body_reader(load_ticket_bodies(good))
    assert reader("OMN-1") == "body text"
    with pytest.raises(LinearUnreachableError):
        reader("OMN-2")
    with pytest.raises(LinearUnreachableError):
        reader("OMN-404")


# ---------------------------------------------------------------------------
# P4 — the case space is enumerable from the shape alone.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_enumerate_case_space_walks_errors_constraints_and_deps() -> None:
    contract: dict[str, Any] = {
        "interface": {
            "inputs": [
                {
                    "name": "a",
                    "type": "str",
                    "required": True,
                    "constraints": {"min_length": 1},
                },
                {"name": "b", "type": "int", "required": False},
            ],
            "outputs": [{"name": "o", "type": "int", "required": True}],
            "error_taxonomy": [{"code": "E_ONE", "when": "something goes wrong"}],
        },
        "dependencies": [{"name": "dep", "seam_schema": "x", "injectable": True}],
    }
    assert enumerate_case_space(contract) == [
        "error:E_ONE",
        "input:a.min_length",
        "dependency:dep",
    ]


@pytest.mark.unit
def test_case_space_of_the_live_contract_is_fully_mapped() -> None:
    """The mechanism's own contract walks its whole space (dogfood)."""
    from onex_change_control.validation.contract_shape_v1 import check_case_space

    contract = yaml.safe_load(
        (REPO_ROOT / "contracts" / "v1" / "OMN-15669.yaml").read_text(encoding="utf-8")
    )
    assert enumerate_case_space(contract)
    assert check_case_space(contract, "contracts/v1/OMN-15669.yaml") == []


# ---------------------------------------------------------------------------
# P5/P6 — the seam harness itself.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_binding_params_ids_match_the_declared_bindings() -> None:
    assert [p.id for p in binding_params("mock")] == ["mock"]
    assert [p.id for p in binding_params("real")] == ["real"]
    assert [p.id for p in binding_params("both")] == ["mock", "real"]
    assert SEAM_BINDINGS == ("mock", "real")
    with pytest.raises(ValueError, match=r"mock\|real\|both"):
        binding_params("sometimes")


@pytest.mark.unit
def test_assert_seam_shape_rejects_a_mock_the_real_dep_could_not_produce() -> None:
    """The OMN-15598 class: a mock shaped unlike the real seam fails EARLY."""
    seam = "onex_change_control.models.model_seam_binding.ModelCollectorSeam"
    assert_seam_shape({"test_path": "t.py", "node_ids": []}, seam, binding="mock")
    with pytest.raises(SeamShapeError, match="does not match"):
        assert_seam_shape({"test_path": "t.py", "nodes": []}, seam, binding="mock")
    with pytest.raises(SeamShapeError, match="does not match"):
        assert_seam_shape({"test_path": 7, "node_ids": []}, seam, binding="mock")


@pytest.mark.unit
def test_resolve_seam_schema_both_forms_and_failure() -> None:
    from_file = resolve_seam_schema("schemas/widget_seam.schema.yaml", CONFORMANT_ROOT)
    assert from_file["required"] == ["widget_id", "weight_g"]
    from_model = resolve_seam_schema(
        "onex_change_control.models.model_seam_binding.ModelBaseBlobSeam"
    )
    assert "path" in from_model["properties"]
    with pytest.raises(SeamShapeError):
        resolve_seam_schema("does.not.Exist")


# ---------------------------------------------------------------------------
# The REAL runner — collection is not simulated.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_pytest_collector_reads_the_real_parameterized_axis() -> None:
    node_ids = PytestCollector(root=CONFORMANT_ROOT).collect(
        "tests/test_widget_cases.py"
    )
    assert "tests/test_widget_cases.py::test_widget_store_seam[mock]" in node_ids
    assert "tests/test_widget_cases.py::test_widget_store_seam[real]" in node_ids
    assert "tests/test_widget_cases.py::test_widget_id_empty[mock]" in node_ids
    assert "tests/test_widget_cases.py::test_widget_id_empty[real]" not in node_ids


# ---------------------------------------------------------------------------
# Scope — grandfather / per-touch migration / no backfill sweep.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_scope_added_v1_contract_is_in_scope(tmp_path: Path) -> None:
    (tmp_path / "contracts" / "v1").mkdir(parents=True)
    (tmp_path / "contracts" / "v1" / "OMN-1.yaml").write_text("ticket_id: OMN-1\n")
    assert select_scope(["contracts/v1/OMN-1.yaml"], tmp_path, _Reader({})) == [
        "contracts/v1/OMN-1.yaml"
    ]


@pytest.mark.unit
def test_scope_v1_marked_base_stays_in_scope_forever(tmp_path: Path) -> None:
    """Migration is one-way: dropping the marker does not drop out of scope."""
    base = "schema_version: occ-contract/v1\nticket_id: OMN-1\n"
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "OMN-1.yaml").write_text("ticket_id: OMN-1\n")
    reader = _Reader({"contracts/OMN-1.yaml": base})
    assert select_scope(["contracts/OMN-1.yaml"], tmp_path, reader) == [
        "contracts/OMN-1.yaml"
    ]


@pytest.mark.unit
def test_scope_deleted_and_unmarked_legacy(tmp_path: Path) -> None:
    (tmp_path / "contracts").mkdir()
    # Deleted by the PR: nothing to validate.
    assert select_scope(["contracts/gone.yaml"], tmp_path, _Reader({})) == []
    # Touched legacy contract with no marker on either side: never opened.
    (tmp_path / "contracts" / "OMN-2.yaml").write_text("ticket_id: OMN-2\n")
    reader = _Reader({"contracts/OMN-2.yaml": "ticket_id: OMN-2\n"})
    assert select_scope(["contracts/OMN-2.yaml"], tmp_path, reader) == []


@pytest.mark.unit
def test_scope_never_enumerates_the_corpus(tmp_path: Path) -> None:
    """No backfill sweep: an empty changed-file list reads nothing."""
    assert select_scope([], tmp_path, _Reader({})) == []


@pytest.mark.unit
def test_main_is_a_noop_when_no_contract_is_touched(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--changed-files", "docs/INDEX.md", "--root", str(REPO_ROOT)])
    assert code == 0
    assert "no in-scope contract" in capsys.readouterr().out


@pytest.mark.unit
def test_finding_render_includes_the_diff() -> None:
    finding = Finding(rule="r", subject="s", message="m", diff="--- a\n+++ b")
    rendered = finding.render()
    assert "[r] s: m" in rendered
    assert "--- a" in rendered


# ---------------------------------------------------------------------------
# Required-path wiring — the anti-removal anchor.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_gate_is_wired_at_the_required_path() -> None:
    """The gate reaches branch protection through the required `CI Summary`.

    OCC dev's required contexts are ["CI Summary",
    "required-check-skip-guard / check-skip-vectors", "verify / verify",
    "occ-preflight / eligibility"] (live readback 2026-08-02), so a job is
    enforced only if ci-summary BOTH needs it and strict-checks its result.
    A rule is not a mechanism: this asserts the mechanism.
    """
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "\n  contract-shape-v1:\n" in ci, "job missing from ci.yml"
    assert "check-contract-shape-v1" in ci, "gate CLI is never invoked"
    needs_line = next(
        line
        for line in ci.splitlines()
        if line.strip().startswith("needs: [zone-filter, pre-commit")
    )
    assert "contract-shape-v1" in needs_line, "ci-summary does not need the gate"
    assert 'needs.contract-shape-v1.result }}" != "success"' in ci, (
        "ci-summary has no strict success-only check for the gate — a SKIPPED "
        "job would pass the generic rollup and the gate would enforce nothing"
    )


# ---------------------------------------------------------------------------
# The gate applied to its own contract — dogfood, end to end, real runner.
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_gate_self_applies_to_its_own_contract_cleanly() -> None:
    """contracts/OMN-15669.yaml passes every non-identity leg of its own gate."""
    proc = subprocess.run(
        [
            "python",
            "-m",
            "onex_change_control.validation.contract_shape_v1",
            "--changed-files",
            "contracts/v1/OMN-15669.yaml",
            "--root",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
