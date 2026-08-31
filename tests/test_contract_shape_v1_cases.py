# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Declared cases of contracts/OMN-15669.yaml — written before the code.

OMN-15669, operator ruling R-0802-9 (2026-08-02).

Every ``test_<case_id>`` here is DECLARED in the contract's ``cases`` block and
is asserted by the gate to exist and to be collected by the real runner. The
binding axis is the single ``binding`` parameter: a case declared
``bindings: both`` runs the IDENTICAL body mock-bound and real-bound, and the
gate reads the collected param ids back so the declaration cannot drift from
the wiring.

Seam schemas cited here (both mock and real payloads validate against them):
  onex_change_control.models.model_seam_binding.ModelCollectorSeam
  onex_change_control.models.model_seam_binding.ModelBaseBlobSeam
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from onex_change_control.testing.seam_binding import assert_seam_shape, binding_params
from onex_change_control.validation.contract_shape_v1 import (
    CONTRACT_BLOCK_HEADING,
    GitBaseReader,
    IdentityInputs,
    PytestCollector,
    check_bindings,
    check_case_space,
    check_cases,
    check_dependencies,
    check_evidence_falsifiability,
    check_identity,
    check_schema,
    evaluate_contract,
    load_schema,
    select_scope,
    sha256_block,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "contract_shape_v1"
CONFORMANT_ROOT = FIXTURES / "conformant"
CONFORMANT_CONTRACT = "contracts/v1/OMN-99999.yaml"

COLLECTOR_SEAM = "onex_change_control.models.model_seam_binding.ModelCollectorSeam"
BASE_BLOB_SEAM = "onex_change_control.models.model_seam_binding.ModelBaseBlobSeam"

# The two real historical blobs of contracts/OMN-15413.yaml, extracted from this
# repo's own history. acc18c627 is the state the receipts were minted against
# ("the embed"); f6197f0b3 is the later in-place rewrite ("the landed file").
EMBED_AT_ACC18C627 = FIXTURES / "OMN-15413.at-acc18c627.embed.yaml.txt"
REWRITE_AT_F6197F0B3 = FIXTURES / "OMN-15413.at-f6197f0b3.rewrite.yaml.txt"

# The node ids the conformant fixture really collects. The mock binding replays
# exactly this table, so mock and real drive the identical assertions.
_ALL_WIDGET_NODE_IDS = {
    "tests/test_widget_cases.py": [
        "tests/test_widget_cases.py::test_widget_store_seam[mock]",
        "tests/test_widget_cases.py::test_widget_store_seam[real]",
        "tests/test_widget_cases.py::test_widget_id_empty[mock]",
        "tests/test_widget_cases.py::test_weight_negative[mock]",
    ]
}


# ---------------------------------------------------------------------------
# The two injectable dependencies, in both bindings.
# ---------------------------------------------------------------------------
class MockCollector:
    """Mock binding of the collector seam. Canned node ids, no subprocess."""

    def __init__(self, table: dict[str, list[str]]) -> None:
        self.table = table

    def collect(self, test_path: str) -> list[str]:
        return self.table.get(test_path, [])


class MockBaseReader:
    """Mock binding of the base-ref seam. Dict-backed, no git."""

    def __init__(self, table: dict[str, str | None]) -> None:
        self.table = table

    def read(self, path: str) -> str | None:
        return self.table.get(path)


def _collector(binding: str, table: dict[str, list[str]]) -> Any:
    """Resolve the collector dependency for this binding, seam-validated."""
    collector: Any = (
        MockCollector(table)
        if binding == "mock"
        else PytestCollector(root=CONFORMANT_ROOT)
    )
    probe = "tests/test_widget_cases.py"
    assert_seam_shape(
        {"test_path": probe, "node_ids": collector.collect(probe)},
        COLLECTOR_SEAM,
        binding=binding,
    )
    return collector


def _base_reader(binding: str, table: dict[str, str | None]) -> Any:
    """Resolve the base-ref reader dependency for this binding, seam-validated."""
    reader: Any = (
        MockBaseReader(table)
        if binding == "mock"
        else GitBaseReader(root=REPO_ROOT, base_ref="HEAD")
    )
    probe = "contracts/OMN-15413.yaml" if binding == "real" else next(iter(table), "x")
    text = reader.read(probe)
    assert_seam_shape(
        {"path": probe, "exists_on_base": text is not None, "text": text},
        BASE_BLOB_SEAM,
        binding=binding,
    )
    return reader


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _conformant() -> dict[str, Any]:
    return _load(CONFORMANT_ROOT / CONFORMANT_CONTRACT)


def _ticket_body(contract_text: str) -> str:
    return (
        "Some prose.\n\n"
        f"{CONTRACT_BLOCK_HEADING}\n\n```yaml\n{contract_text.rstrip()}\n```\n"
    )


# ---------------------------------------------------------------------------
# GREEN — case: conformant_fixture_green (bindings: both)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("both"))
def test_conformant_fixture_green(binding: str) -> None:
    """A fully conformant contract passes every leg, in both bindings."""
    collector = _collector(binding, _ALL_WIDGET_NODE_IDS)
    contract_text = (CONFORMANT_ROOT / CONFORMANT_CONTRACT).read_text(encoding="utf-8")
    findings = evaluate_contract(
        CONFORMANT_CONTRACT,
        CONFORMANT_ROOT,
        collector,
        IdentityInputs(
            pr_body=(
                "Body.\n\nContract-Ticket-Hash: "
                f"OMN-99999={sha256_block(contract_text)}\n"
            ),
            ticket_body_reader=lambda _tid: _ticket_body(contract_text),
        ),
    )
    assert findings == [], [f.render() for f in findings]


# ---------------------------------------------------------------------------
# case: identity_not_evaluated (bindings: mock)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("mock"))
def test_identity_not_evaluated(binding: str) -> None:
    """Dropping the P2 leg silently is RED; declaring the exclusion is not.

    REMEDIATION r1r. The adversarial verifier found that this contract's own
    ``dod-omn15669-gate-self-applies`` check ran the gate WITHOUT a PR body, so
    ``check_identity`` never ran — the DoD check certifying the gate was weaker
    than the gate, and could be receipted PASS while the required CI job was RED
    on the same contract for an identity finding. Absence of the leg is now a
    finding unless the caller states the exclusion.
    """
    assert binding == "mock"
    collector = _collector(binding, _ALL_WIDGET_NODE_IDS)

    silent = evaluate_contract(CONFORMANT_CONTRACT, CONFORMANT_ROOT, collector)
    assert [f.rule for f in silent] == ["identity_not_evaluated"]

    declared = evaluate_contract(
        CONFORMANT_CONTRACT,
        CONFORMANT_ROOT,
        collector,
        IdentityInputs(waived=True),
    )
    assert declared == [], [f.render() for f in declared]


# ---------------------------------------------------------------------------
# case: shape_invalid (bindings: mock)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("mock"))
def test_shape_invalid(binding: str) -> None:
    """A contract missing a required v1 block is shape-RED against the ONE schema."""
    assert binding == "mock"
    schema = load_schema()
    contract = _conformant()
    del contract["cases"]
    contract["interface"]["error_taxonomy"] = []
    rules = {f.rule for f in check_schema(contract, "x.yaml", schema)}
    assert rules == {"shape_invalid"}

    # GREEN control: the untouched fixture validates cleanly.
    assert check_schema(_conformant(), "x.yaml", schema) == []


# ---------------------------------------------------------------------------
# case: case_space_unmapped (bindings: mock)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("mock"))
def test_case_space_unmapped(binding: str) -> None:
    """An error class mapped to neither a case nor an exclusion is shape-RED."""
    assert binding == "mock"
    contract = _conformant()
    contract["interface"]["error_taxonomy"].append(
        {"code": "WIDGET_VANISHED", "when": "the store returns nothing at all"}
    )
    findings = check_case_space(contract, "x.yaml")
    assert [f.rule for f in findings] == ["case_space_unmapped"]
    assert "error:WIDGET_VANISHED" in findings[0].subject

    # A labeled exclusion maps it, and the RED clears.
    contract["exclusions"] = [
        {
            "target": "error:WIDGET_VANISHED",
            "reason": "unreachable without a store outage; excluded deliberately",
        }
    ]
    assert check_case_space(contract, "x.yaml") == []


# ---------------------------------------------------------------------------
# case: evidence_unfalsifiable_overclaim (bindings: mock)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("mock"))
def test_evidence_unfalsifiable_overclaim(binding: str) -> None:
    """Open-state self-bind evidence + a proof claim is shape-RED.

    The probe is the live autobind self-bind shape: it passes identically
    whether the work is correct or catastrophically broken.
    """
    assert binding == "mock"
    contract = _conformant()
    contract["dod_evidence"] = [
        {
            "id": "occ-self-bind-pr-5999",
            "status": "PASS",
            "checks": [
                {
                    "check_type": "command",
                    "check_value": (
                        "PR_NUMBER=5999 REPO=OmniNode-ai/onex_change_control "
                        "gh pr view ${PR_NUMBER} --repo ${REPO} --json number,state"
                    ),
                }
            ],
        }
    ]
    findings = check_evidence_falsifiability(contract, "x.yaml")
    assert [f.rule for f in findings] == ["evidence_unfalsifiable_overclaim"]
    assert "L0" in findings[0].diff

    # REMEDIATION r1: the four always-true forms that survived the OMN-14409
    # family-level derivation and reached GREEN in the first build. Each is a
    # single-check contract, so a PASS here would mean the whole floor is inert.
    always_true: list[tuple[str, str, str]] = [
        (
            "self_referential",
            "command",
            "grep -q '^status: PASS$' drift/dod_receipts/OMN-99999/dod-1/command.yaml",
        ),
        ("vacuous_pattern_empty", "command", "grep -c '' README.md"),
        ("vacuous_pattern_dot", "command", "rg -q . README.md"),
        (
            "prose_only_target",
            "command",
            "grep -q 'the gate is wired at the required path' "
            "docs/standards/DUAL_BINDING_CASES.md",
        ),
        (
            "check_type_label_only",
            "test_passes",
            "the declared cases all pass locally on the .200 gate host",
        ),
    ]
    for label, check_type, check_value in always_true:
        probe = _conformant()
        probe["dod_evidence"] = [
            {
                "id": f"dod-{label}",
                "status": "PASS",
                "checks": [{"check_type": check_type, "check_value": check_value}],
            }
        ]
        rules = [f.rule for f in check_evidence_falsifiability(probe, "x.yaml")]
        assert rules == ["evidence_unfalsifiable_overclaim"], (label, rules)

    # GREEN control: the conformant fixture's test_passes check is falsifiable.
    assert check_evidence_falsifiability(_conformant(), "x.yaml") == []

    # GREEN control 2: a NON-vacuous grep over source still counts. The rules
    # above reject arguments, not the verb — rejecting honest static assertions
    # is the perverse incentive OMN-14409 warns about.
    honest = _conformant()
    honest["dod_evidence"] = [
        {
            "id": "dod-static-assert",
            "status": "PASS",
            "checks": [
                {
                    "check_type": "command",
                    "check_value": (
                        "grep -q '\"Contract Shape v1 (OMN-15669)\"' "
                        "scripts/ci/ci_summary_gate.py"
                    ),
                }
            ],
        }
    ]
    assert check_evidence_falsifiability(honest, "x.yaml") == []


# ---------------------------------------------------------------------------
# case: identity_block_divergence (bindings: mock)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("mock"))
def test_identity_block_divergence(binding: str) -> None:
    """Replay of the real f6197f0b3 rewrite against the acc18c627 embed.

    The ticket body carries contracts/OMN-15413.yaml as it stood at acc18c627
    (when its receipts were minted); the landed file is the f6197f0b3 in-place
    rewrite. Identity must be RED, with a diff, and identity-blind: the same
    human authored both commits.
    """
    assert binding == "mock"
    embed = EMBED_AT_ACC18C627.read_text(encoding="utf-8")
    landed = REWRITE_AT_F6197F0B3.read_text(encoding="utf-8")
    assert sha256_block(embed) != sha256_block(landed)

    pr_body = f"Body.\n\nContract-Ticket-Hash: OMN-15413={sha256_block(landed)}\n"
    findings = check_identity("OMN-15413", landed, pr_body, _ticket_body(embed))
    rules = [f.rule for f in findings]
    assert "identity_block_divergence" in rules
    diff = next(f.diff for f in findings if f.rule == "identity_block_divergence")
    assert diff.startswith("---")
    assert "ACCEPTANCE BULLET 6" in diff

    # GREEN control: same bytes on all three sides is clean.
    assert (
        check_identity(
            "OMN-15413",
            landed,
            f"Contract-Ticket-Hash: OMN-15413={sha256_block(landed)}\n",
            _ticket_body(landed),
        )
        == []
    )


# ---------------------------------------------------------------------------
# case: identity_trailer_block_divergence (bindings: mock)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("mock"))
def test_identity_trailer_block_divergence(binding: str) -> None:
    """A ticket edited AFTER the PR was opened is trailer-RED."""
    assert binding == "mock"
    landed = REWRITE_AT_F6197F0B3.read_text(encoding="utf-8")
    pr_body = f"Contract-Ticket-Hash: OMN-15413={sha256_block(landed)}\n"
    edited_body = _ticket_body(landed + "\n# edited after the PR was opened\n")
    rules = [f.rule for f in check_identity("OMN-15413", landed, pr_body, edited_body)]
    assert "identity_block_divergence" in rules
    assert "identity_trailer_block_divergence" in rules


# ---------------------------------------------------------------------------
# case: identity_linear_unreachable (bindings: mock)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("mock"))
def test_identity_linear_unreachable(binding: str) -> None:
    """An unreadable ticket body is RED, never a skip."""
    assert binding == "mock"
    landed = REWRITE_AT_F6197F0B3.read_text(encoding="utf-8")
    pr_body = f"Contract-Ticket-Hash: OMN-15413={sha256_block(landed)}\n"
    findings = check_identity("OMN-15413", landed, pr_body, None)
    assert [f.rule for f in findings] == ["identity_linear_unreachable"]


# ---------------------------------------------------------------------------
# case: case_test_absent (bindings: both)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("both"))
def test_case_test_absent(binding: str) -> None:
    """A declared case whose file is not in the PR tree is RED."""
    collector = _collector(binding, _ALL_WIDGET_NODE_IDS)
    contract = _conformant()
    contract["cases"] = contract["cases"][:1]
    contract["cases"][0]["test_path"] = "tests/test_never_written.py"
    findings = check_cases(contract, "x.yaml", CONFORMANT_ROOT, collector)
    assert [f.rule for f in findings] == ["case_test_absent"]


# ---------------------------------------------------------------------------
# case: case_not_collected (bindings: both)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("both"))
def test_case_not_collected(binding: str) -> None:
    """A case whose file exists but which the REAL runner does not collect is RED."""
    collector = _collector(binding, _ALL_WIDGET_NODE_IDS)
    contract = _conformant()
    contract["cases"][0]["id"] = "widget_store_seam_renamed_but_never_written"
    findings = check_cases(contract, "x.yaml", CONFORMANT_ROOT, collector)
    assert [f.rule for f in findings] == ["case_not_collected"]

    # GREEN control: the real case id IS collected.
    assert check_cases(_conformant(), "x.yaml", CONFORMANT_ROOT, collector) == []


# ---------------------------------------------------------------------------
# case: binding_axis_mismatch (bindings: both)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("both"))
def test_binding_axis_mismatch(binding: str) -> None:
    """`bindings: both` with only one leg wired is RED."""
    collector = _collector(binding, _ALL_WIDGET_NODE_IDS)
    contract = _conformant()
    # widget_id_empty is wired mock-only; over-declare it as dual-bound.
    contract["cases"][1]["bindings"] = "both"
    findings = check_bindings(contract, "x.yaml", CONFORMANT_ROOT, collector)
    assert [f.rule for f in findings] == ["binding_axis_mismatch"]
    assert "['mock']" in findings[0].message

    # GREEN control: the honest declaration matches the wired axis.
    assert check_bindings(_conformant(), "x.yaml", CONFORMANT_ROOT, collector) == []


# ---------------------------------------------------------------------------
# case: seam_schema_unresolvable (bindings: mock)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("mock"))
def test_seam_schema_unresolvable(binding: str) -> None:
    """A dependency whose seam schema does not resolve is RED."""
    assert binding == "mock"
    contract = _conformant()
    contract["dependencies"][0]["seam_schema"] = "schemas/does_not_exist.schema.yaml"
    rules = [f.rule for f in check_dependencies(contract, "x.yaml", CONFORMANT_ROOT)]
    assert "seam_schema_unresolvable" in rules


# ---------------------------------------------------------------------------
# case: seam_schema_not_cited (bindings: mock)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("mock"))
def test_seam_schema_not_cited(binding: str, tmp_path: Path) -> None:
    """A seam case whose test file never names the seam schema is RED."""
    assert binding == "mock"
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "widget_seam.schema.yaml").write_text("type: object\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_widget_cases.py").write_text(
        "def test_widget_store_seam():\n    assert_seam_shape({}, 'other')\n"
    )
    rules = [f.rule for f in check_dependencies(_conformant(), "x.yaml", tmp_path)]
    assert "seam_schema_not_cited" in rules
    assert "seam_validation_not_executed" not in rules

    # REMEDIATION r1: a ref that appears ONLY in a docstring or a comment is not
    # a citation. It is never handed to the validator, so under the previous
    # substring test it bought silence it had not earned.
    for prose in (
        '"""Uses schemas/widget_seam.schema.yaml."""\n',
        "# validated against schemas/widget_seam.schema.yaml\n",
    ):
        (tmp_path / "tests" / "test_widget_cases.py").write_text(
            prose + "def test_widget_store_seam():\n"
            "    assert_seam_shape({}, 'other')\n"
        )
        rules = [f.rule for f in check_dependencies(_conformant(), "x.yaml", tmp_path)]
        assert "seam_schema_not_cited" in rules, prose

    # GREEN control: the same ref as a real string literal IS a citation.
    (tmp_path / "tests" / "test_widget_cases.py").write_text(
        "SEAM = 'schemas/widget_seam.schema.yaml'\n"
        "def test_widget_store_seam():\n    assert_seam_shape({}, SEAM)\n"
    )
    rules = [f.rule for f in check_dependencies(_conformant(), "x.yaml", tmp_path)]
    assert "seam_schema_not_cited" not in rules
    assert "seam_validation_not_executed" not in rules


# ---------------------------------------------------------------------------
# case: seam_validation_not_executed (bindings: mock)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("mock"))
def test_seam_validation_not_executed(binding: str, tmp_path: Path) -> None:
    """Citing the seam schema without EXECUTING the validation is RED.

    REMEDIATION r1: the first build enforced this leg with the substring test
    ``"assert_seam_shape(" not in source``. The adversarial replay defeated it
    with a file whose only occurrences of the symbol were a ``#`` comment and an
    ``if False:`` branch — the gate AND pytest both went green while the seam
    validation never ran.

    REMEDIATION r1r: compile-time reachability was still too weak. A second
    replay drove five RUNTIME-dead forms past it, two of which are green in
    pytest as well (``pytest.skip(...)`` and an early ``return`` before the
    call). The bar is now UNCONDITIONAL execution with a verdict that can still
    fail the test, which is what P5 actually claims. One consequence is
    deliberate and visible below: ``live_branch`` — a call guarded by a runtime
    ``if`` — moved from a GREEN control to a RED form. There is no way to
    distinguish ``if flag:`` from ``if os.environ.get('NEVER_SET'):`` statically,
    so the convention is that the seam validator sits on the test's straight-line
    path.
    """
    assert binding == "mock"
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "widget_seam.schema.yaml").write_text("type: object\n")
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "test_widget_cases.py"
    cite = "SEAM = 'schemas/widget_seam.schema.yaml'\n"

    never_executes: dict[str, str] = {
        # The declared symbol appears nowhere executable at all.
        "absent": cite + "def test_widget_store_seam():\n    assert True\n",
        # THE ADVERSARIAL FORM: a comment plus a dead branch. Substring-passes.
        "comment_plus_dead_branch": (
            cite + "def test_widget_store_seam():\n"
            "    # assert_seam_shape(payload, SEAM)\n"
            "    if False:\n"
            "        assert_seam_shape({}, SEAM)\n"
            "    assert True\n"
        ),
        # Same idea via `while 0:`.
        "dead_while": (
            cite + "def test_widget_store_seam():\n"
            "    while 0:\n"
            "        assert_seam_shape({}, SEAM)\n"
            "    assert True\n"
        ),
        # Dead `else` arm of a constant-true `if`.
        "dead_else": (
            cite + "def test_widget_store_seam():\n"
            "    if True:\n"
            "        assert True\n"
            "    else:\n"
            "        assert_seam_shape({}, SEAM)\n"
        ),
        # Quoted inside a docstring — text, not code.
        "docstring_only": (
            cite + "def test_widget_store_seam():\n"
            '    """Calls assert_seam_shape(payload, SEAM)."""\n'
            "    assert True\n"
        ),
        # Executed, but from an UNRELATED function this case never reaches.
        "wrong_function": (
            cite + "def helper_never_called():\n"
            "    assert_seam_shape({}, SEAM)\n"
            "def test_widget_store_seam():\n"
            "    assert True\n"
        ),
        # --- r1r: RUNTIME-dead forms. Compile-time pruning missed every one. --
        # The exact replay: a condition that is never true at runtime.
        "runtime_conditional_never_true": (
            "import os\n" + cite + "def test_widget_store_seam():\n"
            "    if os.environ.get('NEVER_SET'):\n"
            "        assert_seam_shape({}, SEAM)\n"
        ),
        # Gate-GREEN *and* pytest-GREEN in r1: the test is skipped before the call.
        "pytest_skip_before_the_call": (
            "import pytest\n" + cite + "def test_widget_store_seam():\n"
            "    pytest.skip('not today')\n"
            "    assert_seam_shape({}, SEAM)\n"
        ),
        # Likewise green in both: nothing after the return runs.
        "early_return_before_the_call": (
            cite + "def test_widget_store_seam():\n"
            "    return\n"
            "    assert_seam_shape({}, SEAM)\n"
        ),
        # The call RUNS, but its verdict is discarded — it cannot fail the test.
        "verdict_swallowed_by_except": (
            cite + "def test_widget_store_seam():\n"
            "    try:\n"
            "        assert_seam_shape({}, SEAM)\n"
            "    except Exception:\n"
            "        pass\n"
        ),
        "verdict_swallowed_by_bare_except": (
            cite + "def test_widget_store_seam():\n"
            "    try:\n"
            "        assert_seam_shape({}, SEAM)\n"
            "    except BaseException:\n"
            "        pass\n"
        ),
        # Constructed by extending the same attack: a zero-iteration loop, a
        # skip MARK, and a conditional hidden one hop away in a helper.
        "loop_that_may_not_iterate": (
            cite + "def test_widget_store_seam(items=()):\n"
            "    for _ in items:\n"
            "        assert_seam_shape({}, SEAM)\n"
        ),
        "skip_mark_on_the_test": (
            "import pytest\n" + cite + "@pytest.mark.skip(reason='parked')\n"
            "def test_widget_store_seam():\n"
            "    assert_seam_shape({}, SEAM)\n"
        ),
        "conditional_inside_the_helper": (
            "import os\n" + cite + "def _resolve(binding):\n"
            "    if os.environ.get('NEVER_SET'):\n"
            "        assert_seam_shape({}, SEAM)\n"
            "def test_widget_store_seam():\n"
            "    _resolve('mock')\n"
        ),
        # r1's GREEN control, now RED by design: a runtime `if` is statically
        # indistinguishable from the never-true form two entries above.
        "live_branch": (
            cite + "def test_widget_store_seam(flag=True):\n"
            "    if flag:\n"
            "        assert_seam_shape({}, SEAM)\n"
        ),
    }
    for label, source in never_executes.items():
        test_file.write_text(source)
        rules = [f.rule for f in check_dependencies(_conformant(), "x.yaml", tmp_path)]
        assert "seam_validation_not_executed" in rules, (label, rules)
        assert "seam_schema_not_cited" not in rules, (label, rules)

    # GREEN controls: a live call in the body, and a live call reached through a
    # module-local helper (the shape this very module uses).
    really_executes: dict[str, str] = {
        "inline": (
            cite + "def test_widget_store_seam():\n    assert_seam_shape({}, SEAM)\n"
        ),
        "via_helper": (
            cite + "def _resolve(binding):\n"
            "    assert_seam_shape({}, SEAM)\n"
            "def test_widget_store_seam():\n    _resolve('mock')\n"
        ),
        # A `with` body always runs, and a handler that re-raises preserves the
        # verdict — neither is a way to duck the validation.
        "inside_a_with_block": (
            cite + "def test_widget_store_seam():\n"
            "    with open('/dev/null') as fh:\n"
            "        assert_seam_shape({}, SEAM)\n"
        ),
        "try_that_reraises": (
            cite + "def test_widget_store_seam():\n"
            "    try:\n"
            "        assert_seam_shape({}, SEAM)\n"
            "    except Exception:\n"
            "        raise\n"
        ),
    }
    for label, source in really_executes.items():
        test_file.write_text(source)
        rules = [f.rule for f in check_dependencies(_conformant(), "x.yaml", tmp_path)]
        assert "seam_validation_not_executed" not in rules, (label, rules)

    # A file that does not parse fails CLOSED — no substring fallback.
    test_file.write_text("def test_widget_store_seam(:\n")
    rules = [f.rule for f in check_dependencies(_conformant(), "x.yaml", tmp_path)]
    assert "case_source_unparseable" in rules


# ---------------------------------------------------------------------------
# case: legacy_contract_grandfathered (bindings: both)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("both"))
def test_legacy_contract_grandfathered(binding: str, tmp_path: Path) -> None:
    """An unmarked legacy contract is out of scope; a v1 path is in scope.

    Migration is one-way: a legacy contract that HAS taken the marker stays in
    scope forever, even though its unmarked siblings are never read.
    """
    base_text = "ticket_id: OMN-15413\ndod_evidence:\n  - id: a\n"
    reader = _base_reader(binding, {"contracts/OMN-15413.yaml": base_text})
    if binding == "real":
        # Real git binding: a HEAD-resident path reads, an unknown path is None.
        assert reader.read("contracts/OMN-15413.yaml") is not None
        assert reader.read("contracts/OMN-00000000.yaml") is None
        reader = MockBaseReader({"contracts/OMN-15413.yaml": base_text})

    (tmp_path / "contracts" / "v1").mkdir(parents=True)
    legacy = tmp_path / "contracts" / "OMN-15413.yaml"

    # Touched legacy contract, no marker anywhere: never opened.
    legacy.write_text(base_text + "  - id: b\n")
    assert select_scope(["contracts/OMN-15413.yaml"], tmp_path, reader) == []

    # Same file once it carries the marker: in scope, one-way.
    legacy.write_text("schema_version: occ-contract/v1\n" + base_text)
    assert select_scope(["contracts/OMN-15413.yaml"], tmp_path, reader) == [
        "contracts/OMN-15413.yaml"
    ]

    # Anything under contracts/v1/ is in scope on any touch.
    (tmp_path / "contracts" / "v1" / "OMN-1.yaml").write_text("ticket_id: OMN-1\n")
    assert select_scope(["contracts/v1/OMN-1.yaml"], tmp_path, reader) == [
        "contracts/v1/OMN-1.yaml"
    ]


# ---------------------------------------------------------------------------
# case: non_contract_pr_untouched (bindings: both)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding", binding_params("both"))
def test_non_contract_pr_untouched(binding: str, tmp_path: Path) -> None:
    """A PR touching no contract is a byte-identical no-op for this gate."""
    reader = _base_reader(binding, {"contracts/OMN-15413.yaml": "ticket_id: X\n"})
    changed = [
        "src/onex_change_control/scripts/validate_yaml.py",
        "docs/INDEX.md",
        ".github/workflows/ci.yml",
        "drift/dod_receipts/OMN-1/dod-1/command.yaml",
    ]
    assert select_scope(changed, tmp_path, reader) == []
