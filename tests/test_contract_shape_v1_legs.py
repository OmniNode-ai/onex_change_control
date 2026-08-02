# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Per-leg unit tests for the contract-shape-v1 gate (OMN-15669, R-0802-9).

These are the leg-level RED/GREEN discriminators. The end-to-end cases the
contract itself declares live in ``tests/test_contract_shape_v1_cases.py``;
this module proves each primitive those cases stand on.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
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
    SEAM_ASSERT_NAME,
    V1_MARKER,
    Finding,
    LinearUnreachableError,
    PytestCollector,
    canonicalize,
    check_evidence_falsifiability,
    check_identity,
    check_schema,
    enumerate_case_space,
    extract_contract_block,
    load_schema,
    load_ticket_bodies,
    main,
    make_ticket_body_reader,
    parse_contract_trailers,
    parse_source_facts,
    select_scope,
    sha256_block,
    v1_vacuity_reason,
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
_SCAN_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        ".uv-cache",
    }
)
# A JSON Schema document larger than this is not a plausible second authority
# for one contract shape; the cap bounds the whole-tree scan. Byte-identical
# copies are caught by digest regardless of size.
_MAX_SCANNED_BYTES = 1_000_000


def _constrains_the_marker(node: Any) -> bool:
    """True when ``node`` constrains a value to the v1 marker, anywhere in it.

    REMEDIATION r1r: reading only ``const``/``enum`` was defeated by a schema
    that used ``pattern: occ-contract/v1`` instead — the same constraint spelled
    differently. This recurses over the whole ``schema_version`` subschema, so
    ``const``, ``enum``, ``pattern``, and any ``allOf``/``anyOf``/``oneOf``/
    ``$defs`` nesting of them are all covered.
    """
    if isinstance(node, str):
        return V1_MARKER in node
    if isinstance(node, dict):
        return any(_constrains_the_marker(value) for value in node.values())
    if isinstance(node, list):
        return any(_constrains_the_marker(item) for item in node)
    return False


def _declares_the_v1_shape(doc: dict[str, Any], canonical: dict[str, Any]) -> str:
    """Why ``doc`` is a SECOND authority for the v1 shape, or the empty string."""
    if str(doc.get("$id", "")) == str(canonical["$id"]):
        return "declares the canonical $id"
    if not str(doc.get("$schema", "")).startswith("https://json-schema.org/"):
        return ""  # a contract INSTANCE carries the marker; that is correct
    if str(doc.get("title", "")) == str(canonical["title"]):
        return "declares the canonical title"
    version_prop = (doc.get("properties") or {}).get("schema_version")
    if isinstance(version_prop, dict) and _constrains_the_marker(version_prop):
        return f"a second schema constraining {V1_MARKER}"
    return ""


def second_shape_authorities(root: Path, canonical_path: Path) -> list[str]:
    """Every file under ``root`` that is a second authority for the v1 shape.

    Stated over CONTENT and IDENTITY, never over filename OR EXTENSION — see
    :func:`test_exactly_one_contract_schema_artifact` for why. REMEDIATION r1r:
    the r1 scan skipped everything whose suffix was not ``.yaml``/``.yml``/
    ``.json``, so a byte-identical copy saved as ``.txt`` evaded it. Every
    regular file under ``root`` is now digested; the size check makes that cheap
    (a copy of the canonical schema has the canonical schema's byte length), and
    byte needles gate the more expensive parse.
    """
    canonical_bytes = canonical_path.read_bytes()
    canonical_sha = hashlib.sha256(canonical_bytes).hexdigest()
    canonical_doc = yaml.safe_load(canonical_bytes.decode("utf-8"))
    needles = (
        str(canonical_doc["$id"]).encode("utf-8"),
        str(canonical_doc["title"]).encode("utf-8"),
        V1_MARKER.encode("utf-8"),
    )
    found: list[str] = []
    for path in root.rglob("*"):
        if _SCAN_SKIP_DIRS & set(path.parts) or not path.is_file():
            continue
        if path.resolve() == canonical_path.resolve():
            continue
        reason = _second_authority_reason(
            path, canonical_bytes, canonical_sha, canonical_doc, needles
        )
        if reason:
            found.append(f"{path.relative_to(root).as_posix()} ({reason})")
    return found


def _second_authority_reason(
    path: Path,
    canonical_bytes: bytes,
    canonical_sha: str,
    canonical_doc: dict[str, Any],
    needles: tuple[bytes, ...],
) -> str:
    """Why this one file is a second authority for the v1 shape, or ``""``."""
    try:
        size = path.stat().st_size
    except OSError:  # pragma: no cover - races on transient files
        return ""
    if size == len(canonical_bytes) and (
        hashlib.sha256(path.read_bytes()).hexdigest() == canonical_sha
    ):
        return "byte-identical copy of the canonical schema"
    if size > _MAX_SCANNED_BYTES:
        return ""
    raw = path.read_bytes()
    if not any(needle in raw for needle in needles):
        return ""
    doc = _parse_document(raw)
    return _declares_the_v1_shape(doc, canonical_doc) if doc is not None else ""


def _parse_document(raw: bytes) -> dict[str, Any] | None:
    """Parse ``raw`` as a YAML/JSON mapping, or None when it is neither."""
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return None
    return doc if isinstance(doc, dict) else None


@pytest.mark.unit
def test_exactly_one_contract_schema_artifact() -> None:
    """One-model-per-shape: the repo carries exactly one v1 contract schema.

    REMEDIATION r1: the first build globbed ``**/occ_contract_v*.schema.yaml``,
    so the ratchet was a FILENAME convention, not a uniqueness proof — an
    adversarial replay dropped a byte-identical copy at
    ``schemas/contract_shape.schema.yaml`` and the ratchet stayed green while
    the repo carried two authorities for one shape. The rule is now stated over
    CONTENT and IDENTITY, which is what one-model-per-shape actually means:

      * no other file in the tree, at ANY path and ANY extension, is
        byte-identical to the canonical schema;
      * no other file declares the canonical ``$id`` or ``title``;
      * no other JSON Schema document constrains ``schema_version`` to a string
        CONTAINING the v1 marker — ``const``, ``enum``, ``pattern``, or any
        nesting of them.

    Neither filename nor extension is part of the test, so renaming the copy
    cannot evade it. REMEDIATION r1r closed the two evasions an adversarial
    replay found in the r1 version: ``pattern:`` in place of ``const:`` (r1 read
    only ``const``/``enum``), and a byte-identical copy with a ``.txt`` suffix
    (r1 scanned only ``.yaml``/``.yml``/``.json``).

    NOT covered, by name: a schema that constrains ``schema_version`` without
    ever naming the marker string — e.g. ``pattern: "^occ-contract/v[0-9]+$"``,
    or a ``$ref`` to an external document. This is a content ratchet over the
    marker literal, not a semantic equivalence checker.
    """
    assert SCHEMA_PATH.exists()
    duplicates = second_shape_authorities(REPO_ROOT, SCHEMA_PATH)
    assert duplicates == [], (
        "one-model-per-shape: a second v1 contract schema artifact exists. "
        "Extend schemas/occ_contract_v1.schema.yaml instead.\n  "
        + "\n  ".join(duplicates)
    )


@pytest.mark.unit
def test_the_schema_uniqueness_ratchet_catches_a_renamed_copy(tmp_path: Path) -> None:
    """RED-before proof for the widened ratchet, against exists-but-wrong.

    The replay that defeated the previous ratchet is reproduced exactly — a
    byte-identical copy under a DIFFERENT filename — and driven through the
    SAME predicate the ratchet above asserts, not a re-implementation of it.
    """
    canonical = tmp_path / "schemas" / "occ_contract_v1.schema.yaml"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(SCHEMA_PATH.read_bytes())
    assert second_shape_authorities(tmp_path, canonical) == []

    # The exact evasion: same bytes, a name the old glob never matched.
    copy = tmp_path / "schemas" / "contract_shape.schema.yaml"
    copy.write_bytes(SCHEMA_PATH.read_bytes())
    assert not copy.name.startswith("occ_contract_v"), (
        "the copy must NOT match the old filename glob, or this proves nothing"
    )
    assert second_shape_authorities(tmp_path, canonical) == [
        "schemas/contract_shape.schema.yaml (byte-identical copy of the "
        "canonical schema)"
    ]

    # A near-copy that only shares the $id is caught by identity, not bytes.
    canonical_doc = yaml.safe_load(canonical.read_text(encoding="utf-8"))
    copy.write_text(
        yaml.safe_dump({"$id": canonical_doc["$id"], "x": 1}), encoding="utf-8"
    )
    assert second_shape_authorities(tmp_path, canonical) == [
        "schemas/contract_shape.schema.yaml (declares the canonical $id)"
    ]

    # REMEDIATION r1r EVASION-2: byte-identical, saved under a suffix the r1
    # scan skipped entirely. Extension is not part of the rule.
    copy.unlink()
    txt_copy = tmp_path / "schemas" / "contract_shape.schema.yaml.txt"
    txt_copy.write_bytes(SCHEMA_PATH.read_bytes())
    assert second_shape_authorities(tmp_path, canonical) == [
        "schemas/contract_shape.schema.yaml.txt (byte-identical copy of the "
        "canonical schema)"
    ]
    txt_copy.unlink()

    # REMEDIATION r1r EVASION-1: the same constraint spelled `pattern:` rather
    # than `const:`, with a different $id AND a different title, so identity
    # cannot catch it. r1 read only const/enum and returned clean here.
    copy = tmp_path / "schemas" / "contract_shape.schema.yaml"
    copy.write_text(
        yaml.safe_dump(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://example.invalid/some-other-id.json",
                "title": "Totally unrelated name",
                "properties": {"schema_version": {"pattern": V1_MARKER}},
            }
        ),
        encoding="utf-8",
    )
    assert second_shape_authorities(tmp_path, canonical) == [
        f"schemas/contract_shape.schema.yaml (a second schema constraining {V1_MARKER})"
    ]

    # A renamed schema that only reuses the structural signature is caught too.
    copy.write_text(
        yaml.safe_dump(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": "Some other name",
                "properties": {"schema_version": {"const": V1_MARKER}},
            }
        ),
        encoding="utf-8",
    )
    assert second_shape_authorities(tmp_path, canonical) == [
        f"schemas/contract_shape.schema.yaml (a second schema constraining {V1_MARKER})"
    ]

    # GREEN control: a contract INSTANCE carrying the marker is not a schema.
    copy.unlink()
    instance = tmp_path / "contracts" / "v1" / "OMN-1.yaml"
    instance.parent.mkdir(parents=True)
    instance.write_text(f"schema_version: {V1_MARKER}\nticket_id: OMN-1\n")
    assert second_shape_authorities(tmp_path, canonical) == []


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
    """contracts/OMN-15669.yaml passes every non-identity leg of its own gate.

    Driven through the SAME argv the ``dod-omn15669-gate-self-applies`` check
    runs, so the receipt and this test cannot disagree. REMEDIATION r1r: that
    argv now carries ``--skip-identity``, and the run WITHOUT it is asserted RED
    below — the DoD check that certifies this gate is no longer allowed to be
    silently weaker than the gate.
    """
    argv = [
        "python",
        "-m",
        "onex_change_control.validation.contract_shape_v1",
        "--skip-identity",
        "contracts/v1/OMN-15669.yaml",
        "--root",
        str(REPO_ROOT),
    ]
    proc = subprocess.run(
        argv, cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    declared = yaml.safe_load(
        (REPO_ROOT / "contracts" / "v1" / "OMN-15669.yaml").read_text(encoding="utf-8")
    )
    self_applies = next(
        item
        for item in declared["dod_evidence"]
        if item["id"] == "dod-omn15669-gate-self-applies"
    )
    assert self_applies["checks"][0]["check_value"] == (
        "uv run check-contract-shape-v1 --skip-identity contracts/v1/OMN-15669.yaml"
    ), "the receipt command and this test must drive the same argv"

    silent = subprocess.run(
        [arg for arg in argv if arg != "--skip-identity"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert silent.returncode == 1, silent.stdout + silent.stderr
    assert "identity_not_evaluated" in silent.stdout


# ---------------------------------------------------------------------------
# REMEDIATION r1 — the conformant fixture must EXECUTE, not merely be collected.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_the_conformant_fixture_is_not_excluded_from_the_outer_suite() -> None:
    """tests/conftest.py must not re-add a collect_ignore for the fixture tree.

    The gate drives that tree with ``pytest --collect-only``, which executes no
    line of it. With the exclusion in place the reference implementation of
    "the mock validates against the real seam schema" ran NOWHERE in CI. This
    is the anti-regression anchor for removing it; the mutation proof below is
    the behavioural half.
    """
    conftest = (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    offenders = [
        line
        for line in conftest.splitlines()
        if line.strip().startswith(("collect_ignore", "collect_ignore_glob"))
    ]
    assert offenders == [], offenders

    module = "tests/fixtures/contract_shape_v1/conformant/tests/test_widget_cases.py"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--no-header",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
            "tests/",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert module in proc.stdout, (
        "the outer suite does not collect the conformant fixture module — its "
        "assert_seam_shape calls execute nowhere\n" + proc.stdout[-2000:]
    )


@pytest.mark.integration
def test_conformant_fixture_executes_and_catches_a_divergent_mock(
    tmp_path: Path,
) -> None:
    """Break the mock's shape; the fixture must go RED.

    RED-against-exists-but-wrong, not RED-against-absent: the file, the schema
    and the assertion all still exist. Only the mock's payload is mutated into a
    shape the seam schema forbids and the real binding never produces — the
    OMN-15598 class the whole P5 leg exists to make unrepresentable.

    The replay that motivated this test mutated ``MockWidgetStore`` in place and
    watched the suite stay 51/51 green.
    """
    tree = tmp_path / "conformant"
    shutil.copytree(CONFORMANT_ROOT, tree)
    module = tree / "tests" / "test_widget_cases.py"

    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--no-header",
                "-p",
                "no:randomly",
                "-p",
                "no:cacheprovider",
                str(module),
            ],
            cwd=tree,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )

    # GREEN control: the untouched copy passes, so a later RED is attributable
    # to the mutation and not to the copy being broken.
    green = _run()
    assert green.returncode == 0, green.stdout + green.stderr

    source = module.read_text(encoding="utf-8")
    mutated = source.replace(
        'return {"widget_id": widget_id, "weight_g": 42}',
        'return {"widget_id": widget_id, "weight_g": "forty-two"}',
    )
    assert mutated != source, "the mock payload line moved; update this mutation"
    module.write_text(mutated, encoding="utf-8")

    red = _run()
    assert red.returncode != 0, (
        "mutating MockWidgetStore into a shape the seam schema forbids left the "
        "fixture green — assert_seam_shape is not executing\n" + red.stdout[-2000:]
    )
    assert "weight_g" in red.stdout


# ---------------------------------------------------------------------------
# REMEDIATION r1 — P5 is enforced semantically (AST), never by substring.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_source_facts_ignore_comments_docstrings_and_dead_branches() -> None:
    """The primitive under the P5 leg: what the module REALLY does."""
    facts = parse_source_facts(
        '"""Module docstring naming assert_seam_shape and schemas/x.yaml."""\n'
        "SEAM = 'schemas/live.yaml'\n"
        "def alive():\n"
        '    """Docstring citing schemas/docstring_only.yaml."""\n'
        "    # assert_seam_shape(payload, SEAM)\n"
        "    if False:\n"
        "        assert_seam_shape({}, 'schemas/dead.yaml')\n"
        "    helper()\n"
        "def helper():\n"
        "    assert_seam_shape({}, SEAM)\n"
    )
    assert facts is not None
    # Literals: only executable, non-docstring strings.
    assert "schemas/live.yaml" in facts.literals
    assert "schemas/dead.yaml" not in facts.literals
    assert "schemas/docstring_only.yaml" not in facts.literals
    # Reachability: through a module-local helper, yes; from the dead branch, no.
    assert facts.reaches(["alive"], SEAM_ASSERT_NAME)
    assert not facts.reaches(["helper_that_does_not_exist"], SEAM_ASSERT_NAME)
    assert facts.calls_by_function["alive"] == frozenset({"helper"})


@pytest.mark.unit
def test_source_facts_return_none_on_unparseable_source() -> None:
    """Unparseable source fails CLOSED — there is no substring fallback."""
    assert parse_source_facts("def broken(:\n") is None


# ---------------------------------------------------------------------------
# REMEDIATION r1 — the v1 falsifiability floor carries no grandfathering.
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize(
    ("check_type", "check_value", "expected"),
    [
        # --- r1: the five forms the first adversarial replay found. ----------
        ("command", "grep -c '' README.md", "vacuous_search_pattern"),
        ("command", "rg -q . README.md", "vacuous_search_pattern"),
        ("command", "grep -q -e '.*' src/app.py", "vacuous_search_pattern"),
        ("command", "grep -q 'shipped' docs/NOTES.md", "prose_satisfiable_search"),
        ("command", "rg -q 'shipped' a.md b.rst", "prose_satisfiable_search"),
        ("test_passes", "the suite is green on .200", "check_type_label_only"),
        # --- r1r: every form the SECOND replay found, verbatim. --------------
        # B1/B12: `.` respelled. The rule now EXECUTES the pattern.
        ("command", "grep -q 'x*' src/foo.py", "vacuous_search_pattern"),
        ("command", "grep -q . src/foo.py", "vacuous_search_pattern"),
        # B2: the `|` lives INSIDE the quoted pattern — tokenization, not regex.
        ("command", "grep -qE '(a|)' src/foo.py", "vacuous_search_pattern"),
        # B3: the target is supplied by an upstream pipeline stage.
        ("command", "cat README.md | grep -q anything", "prose_satisfiable_search"),
        # B4: a prose match ALONE satisfies a multi-target search (r1 used all()).
        ("command", "grep -q 'seam' README.md src/x.py", "prose_satisfiable_search"),
        # B5/B6: prose by unlisted suffix, by directory, and by bare basename.
        ("command", "grep -q 'foo' docs/notes.mdx", "prose_satisfiable_search"),
        ("command", "grep -q 'foo' CHANGELOG", "prose_satisfiable_search"),
        ("command", "grep -q 'foo' docs/whatever", "prose_satisfiable_search"),
        # B8: an inline program with no assertion and no failing exit path.
        ("command", "python -c 'pass'", "no_op_program"),
        ("command", "python3 -c 'print(1)'", "no_op_program"),
        ("command", "sh -c 'true'", "wrapped_no_op_command"),
        # B9: printed, never executed.
        ("command", "make -n help", "dry_run_invocation"),
        ("command", "make --dry-run verify", "dry_run_invocation"),
        # B10: comparing a file with itself.
        ("command", "diff -u README.md README.md", "self_comparison"),
        # GREEN: real assertions keep counting. Rejecting these is the perverse
        # incentive OMN-14409 warns about.
        ("command", "grep -q 'def check_identity' src/x.py", ""),
        ("command", "uv run pytest tests/x.py -q", ""),
        ("command", "grep -q 'needs.x.result' .github/workflows/ci.yml", ""),
        ("test_passes", "uv run pytest tests/x.py -q", ""),
        ("command", "diff -u expected.json actual.json", ""),
        ("command", "python -c 'import onex_change_control'", ""),
        ("command", "make verify", ""),
        ("command", "uv run pre-commit run --all-files", ""),
        ("command", "grep -q 'x' src/a.py src/b.py", ""),
    ],
)
def test_v1_vacuity_rules(check_type: str, check_value: str, expected: str) -> None:
    """Each always-true form is named; each honest form is untouched.

    The r1r rows are the eleven forms an adversarial verifier constructed by
    extending the r1 attack rather than replaying it — `x*` for `.`, a prose
    target reached through `cat`, an extensionless `CHANGELOG`, `uv run true`.
    Each is a NAMED form; see :func:`v1_vacuity_reason` for the residuals this
    list does not close.
    """
    from onex_change_control.validation.contract_shape_v1 import (
        _load_proof_tier_deriver,
    )

    module = _load_proof_tier_deriver()
    derive = module.derive_proof_tier
    reason = v1_vacuity_reason(
        check_type, check_value, derive=derive, floor=module.SUBSTANCE_FLOOR
    )
    if expected:
        assert reason is not None, (check_value, expected)
        assert reason.startswith(expected), reason
    else:
        assert reason is None, reason


@pytest.mark.unit
def test_v1_vacuity_rejects_a_search_of_the_contract_itself() -> None:
    """B7: a probe whose target IS the declaring contract is circular."""
    from onex_change_control.validation.contract_shape_v1 import (
        _load_proof_tier_deriver,
    )

    module = _load_proof_tier_deriver()
    kwargs: dict[str, Any] = {
        "derive": module.derive_proof_tier,
        "floor": module.SUBSTANCE_FLOOR,
    }
    contract = "contracts/v1/OMN-15669.yaml"
    reason = v1_vacuity_reason(
        "command", f"grep -q ticket_id {contract}", contract_path=contract, **kwargs
    )
    assert reason is not None
    assert reason.startswith("self_target_search"), reason

    # A search of a DIFFERENT contract is a real cross-file assertion.
    assert (
        v1_vacuity_reason(
            "command",
            "grep -q ticket_id contracts/v1/OMN-99999.yaml",
            contract_path=contract,
            **kwargs,
        )
        is None
    )


@pytest.mark.unit
def test_v1_vacuity_rejects_a_runner_masked_no_op() -> None:
    """B11: the floor derives `true` to L0; a runner prefix hid it at L1.

    Driven through the REAL deriver so the claim is about the shipped tier
    derivation, not a restatement of it.
    """
    from onex_change_control.validation.contract_shape_v1 import (
        _load_proof_tier_deriver,
    )

    module = _load_proof_tier_deriver()
    derive, floor = module.derive_proof_tier, module.SUBSTANCE_FLOOR

    # RED-before, against exists-but-wrong: the tier derivation alone says L1.
    assert derive("command", "uv run true").satisfies(floor)
    reason = v1_vacuity_reason("command", "uv run true", derive=derive, floor=floor)
    assert reason is not None
    assert reason.startswith("runner_prefix_masks_no_op"), reason

    # GREEN control: the same prefix over a real command is untouched.
    assert (
        v1_vacuity_reason(
            "command", "uv run pytest tests/x.py -q", derive=derive, floor=floor
        )
        is None
    )


@pytest.mark.unit
def test_v1_enabling_the_kill_switch_does_not_leak_into_the_legacy_gate() -> None:
    """v1 flips GATE_SELF_REFERENTIAL on its OWN module instance only.

    The substance floor ships the switch OFF for a measured reason (flipping it
    corpus-wide rejects 98.4% of new legacy contract traffic). v1 turning it on
    must not change one verdict of the corpus gate — so this asserts the source
    is untouched AND that a freshly imported instance still reads False after
    the v1 gate has run.
    """
    floor_source = (
        REPO_ROOT / "scripts" / "validation" / "check_contract_substance_floor.py"
    ).read_text(encoding="utf-8")
    assert "\nGATE_SELF_REFERENTIAL = False\n" in floor_source

    self_referential = {
        "schema_version": V1_MARKER,
        "dod_evidence": [
            {
                "id": "x",
                "checks": [
                    {
                        "check_type": "command",
                        "check_value": (
                            "grep -q '^status: PASS$' "
                            "drift/dod_receipts/OMN-1/dod-1/command.yaml"
                        ),
                    }
                ],
            }
        ],
    }
    rules = [f.rule for f in check_evidence_falsifiability(self_referential, "x.yaml")]
    assert rules == ["evidence_unfalsifiable_overclaim"]

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_legacy_floor_isolation_probe",
        REPO_ROOT / "scripts" / "validation" / "check_contract_substance_floor.py",
    )
    assert spec is not None
    assert spec.loader is not None
    fresh = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fresh
    spec.loader.exec_module(fresh)
    assert fresh.GATE_SELF_REFERENTIAL is False


# ---------------------------------------------------------------------------
# REMEDIATION r1 — CLAUDE.md rule 5: the same check is also a LOCAL gate.
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_gate_is_wired_at_the_local_precommit_path() -> None:
    """A CI-only gate is detection; the pre-commit counterpart is enforcement.

    CLAUDE.md rule 5 and the SHARED SEAM TABLE clause S5(c) both require the
    local hook to ship in the SAME PR as the CI job. This asserts the hook
    exists, runs the SAME entrypoint the CI job runs (so the two verdicts cannot
    diverge on validator logic), and is scoped to the contract paths.
    """
    config = yaml.safe_load(
        (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    )
    hooks = [
        hook
        for repo in config["repos"]
        for hook in repo.get("hooks", [])
        if hook.get("id") == "check-contract-shape-v1"
    ]
    assert len(hooks) == 1, "expected exactly one contract-shape-v1 pre-commit hook"
    hook = hooks[0]
    assert "check-contract-shape-v1" in hook["entry"], hook["entry"]
    assert hook["pass_filenames"] is True
    assert hook["files"] == r"^contracts/.*\.yaml$"
    assert hook["stages"] == ["pre-commit"]

    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "check-contract-shape-v1" in ci, (
        "the CI job and the pre-commit hook must invoke the same entrypoint"
    )


@pytest.mark.unit
def test_the_precommit_entrypoint_accepts_positional_filenames() -> None:
    """pass_filenames passes paths POSITIONALLY; the CLI must accept them.

    Without this the hook would crash on argv rather than gate — the shape of
    "a hook that is wired but has never run".
    """
    code = main(
        ["contracts/v1/OMN-15669.yaml", "--root", str(REPO_ROOT), "--skip-identity"]
    )
    assert code == 0

    # A non-contract path selects an empty scope and reads nothing.
    assert main(["README.md", "--root", str(REPO_ROOT)]) == 0

    # REMEDIATION r1r: the same invocation WITHOUT the declared exclusion is
    # RED. The local hook is a strict subset of the CI gate, and it has to say
    # so — a silent subset is how a DoD check ends up weaker than its gate.
    assert main(["contracts/v1/OMN-15669.yaml", "--root", str(REPO_ROOT)]) == 1
