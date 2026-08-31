# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-16824 -- one semantic for ``check_type: test_passes``.

Before this ticket, ``test_passes`` meant two incompatible things:

* ``node_dod_verify`` (omnimarket) EXECUTED ``check_value`` and honoured the
  check's ``cwd``;
* the hosted ``Contract Compliance Check`` in this repo IGNORED ``check_value``
  entirely and asked instead whether the PR's own CI was green.

The same contract entry was therefore a behaviour proof to one gate and a
PR-status proxy to the other, and a cross-repo behaviour check was structurally
unrunnable in the hosted gate because ``_check_command`` passed
``cwd=workspace`` unconditionally.

AC1 option (a) is implemented: the hosted runner EXECUTES the check the same
way ``node_dod_verify`` does, honours the declared ``cwd``, and DECLINES loudly
(``NOT_EVALUATED``) when a declared ``cwd`` cannot be resolved in the hosted
checkout rather than silently rerouting the command to its own workspace.

The case table in ``tests/fixtures/check_type_runner_semantics.yaml`` is shared
byte-for-byte with omnimarket and is executed against BOTH runners, one in each
repo -- that is the AC3 ambiguity test.
"""

from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from onex_change_control.scripts.contract_compliance_check import (
    _RESULT_BLOCK,
    _RESULT_NOT_EVALUATED,
    _RESULT_PASS,
    _CheckContext,
    _run,
    _run_single_check,
    run_compliance_check,
)

_CASE_TABLE = Path(__file__).parent / "fixtures" / "check_type_runner_semantics.yaml"

# Pinned so an edit to the shared table is a deliberate, reviewed act in BOTH
# repos. omnimarket pins the identical digest over its identical copy; the two
# constants are the mechanical link between the two halves of this test.
#
# The digest is taken over the PARSED table in canonical JSON, not the file
# bytes: each repo's yamlfmt reflows YAML on commit, and a reflow is not a
# change of meaning. Content edits still break it in both repos.
CASE_TABLE_DIGEST = "338cc633d858d71e19e1fc6b2ac76a54c9596e490f1c6a2fd8211b9e845e79c4"


def _case_table_digest(table: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(table, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _load_cases() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    table = yaml.safe_load(_CASE_TABLE.read_text())
    return table, list(table["cases"])


def _context() -> _CheckContext:
    return _CheckContext(
        pr_number=1,
        repo="OmniNode-ai/onex_change_control",
        ticket_id="OMN-16824",
        contracts_dir=None,
        is_legacy=False,
        changed_paths=frozenset(),
    )


def _materialise(root: Path, table: dict[str, Any]) -> None:
    for rel in table["fixture_files"]:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture\n")


# ---------------------------------------------------------------------------
# AC3 -- the shared cross-runner case table, executed against THIS runner
# ---------------------------------------------------------------------------


def test_case_table_content_is_pinned() -> None:
    """The shared table cannot drift in one repo without failing that repo."""
    table, _ = _load_cases()
    assert _case_table_digest(table) == CASE_TABLE_DIGEST, (
        "tests/fixtures/check_type_runner_semantics.yaml changed. It is shared "
        "with omnimarket: update BOTH copies and BOTH pinned "
        "digests, or the two runners can diverge again silently."
    )


@pytest.mark.parametrize("case", _load_cases()[1], ids=lambda c: str(c["id"]))
def test_hosted_runner_matches_the_shared_semantic(
    case: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table, _ = _load_cases()
    _materialise(tmp_path, table)
    monkeypatch.setenv("OMNI_HOME", str(tmp_path))

    check = {
        "check_type": case["check_type"],
        "check_value": case["check_value"],
    }
    if case.get("cwd"):
        check["cwd"] = case["cwd"]

    _, result, detail = _run_single_check(check, tmp_path, _context())

    observed = "verified" if result == _RESULT_PASS else "refused"
    assert observed == case["expected"], (
        f"case {case['id']}: expected {case['expected']}, got {result} -- {detail}\n"
        f"{case['reason']}"
    )


# ---------------------------------------------------------------------------
# AC1/AC4 -- the runner executes check_value and never substitutes PR CI state
# ---------------------------------------------------------------------------


def test_test_passes_blocks_on_a_failing_check_value_with_green_pr_ci(
    tmp_path: Path,
) -> None:
    """The regression AC4 pins.

    A ``test_passes`` entry whose ``check_value`` fails must BLOCK. Under the
    pre-OMN-16824 behaviour this returned PASS whenever the PR's own CI was
    green -- the runner never ran the command at all.
    """
    calls: list[list[str]] = []

    def _spy(cmd: list[str], *args: Any, **kwargs: Any) -> tuple[int, str, str]:
        calls.append(list(cmd))
        return _run(cmd, *args, **kwargs)

    with patch(
        "onex_change_control.scripts.contract_compliance_check._run",
        side_effect=_spy,
    ):
        check_type, result, detail = _run_single_check(
            {"check_type": "test_passes", "check_value": "false"},
            tmp_path,
            _context(),
        )

    assert check_type == "test_passes"
    assert result == _RESULT_BLOCK, detail
    # ...and it did not answer a different question on the way.
    flat = [" ".join(c) for c in calls]
    assert not any("pr checks" in c or ("pr" in c and "checks" in c) for c in flat), (
        f"the runner consulted the PR's CI state instead of executing "
        f"check_value: {flat}"
    )


def test_test_passes_passes_on_a_succeeding_check_value(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("x\n")
    _, result, detail = _run_single_check(
        {"check_type": "test_passes", "check_value": "test -f marker.txt"},
        tmp_path,
        _context(),
    )
    assert result == _RESULT_PASS, detail


# ---------------------------------------------------------------------------
# AC2 -- cwd is read, and an unresolvable cwd declines instead of relocating
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("check_type", ["command", "test_passes"])
def test_declared_cwd_is_honoured(check_type: str, tmp_path: Path) -> None:
    sub = tmp_path / "product" / "sub"
    sub.mkdir(parents=True)
    (sub / "only_here.txt").write_text("x\n")

    _, result, detail = _run_single_check(
        {
            "check_type": check_type,
            "check_value": "test -f only_here.txt",
            "cwd": str(sub),
        },
        tmp_path,
        _context(),
    )
    assert result == _RESULT_PASS, detail


@pytest.mark.parametrize("check_type", ["command", "test_passes"])
def test_unresolvable_cwd_declines_without_executing_in_the_workspace(
    check_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cross-repo cwd absent from the hosted checkout must not be rerouted.

    The command would create ``ran.txt`` wherever it executed; the runner must
    decline, so no such file exists anywhere afterwards.
    """
    monkeypatch.setenv("OMNI_HOME", str(tmp_path))
    _, result, detail = _run_single_check(
        {
            "check_type": check_type,
            "check_value": "touch ran.txt",
            "cwd": "${OMNI_HOME}/omnimarket",
        },
        tmp_path,
        _context(),
    )
    assert result == _RESULT_NOT_EVALUATED, detail
    assert "local_done_gate" in detail
    assert not (tmp_path / "ran.txt").exists()
    assert not list(tmp_path.rglob("ran.txt"))


@pytest.mark.parametrize("check_type", ["command", "test_passes"])
def test_cwd_path_traversal_is_refused(check_type: str, tmp_path: Path) -> None:
    _, result, detail = _run_single_check(
        {
            "check_type": check_type,
            "check_value": "true",
            "cwd": "../../etc",
        },
        tmp_path,
        _context(),
    )
    assert result == _RESULT_NOT_EVALUATED, detail
    assert "traversal" in detail.lower()


@pytest.mark.parametrize("check_type", ["command", "test_passes"])
def test_unresolved_cwd_template_token_is_refused(
    check_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMNI_HOME", raising=False)
    _, result, detail = _run_single_check(
        {
            "check_type": check_type,
            "check_value": "true",
            "cwd": "${OMNI_HOME}/omnimarket",
        },
        tmp_path,
        _context(),
    )
    assert result == _RESULT_NOT_EVALUATED, detail


@pytest.mark.parametrize("check_type", ["command", "test_passes"])
def test_empty_supported_cwd_template_value_is_refused(
    check_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMNI_HOME", raising=False)
    marker = tmp_path / "omnimarket" / "wrong-workspace-marker.txt"
    marker.parent.mkdir()
    marker.write_text("x\n")

    _, result, detail = _run_single_check(
        {
            "check_type": check_type,
            "check_value": "test -f wrong-workspace-marker.txt",
            "cwd": "${OMNI_HOME}/omnimarket",
        },
        tmp_path,
        _CheckContext(
            pr_number=0,
            repo="",
            ticket_id="OMN-16824",
            contracts_dir=None,
            is_legacy=False,
            changed_paths=frozenset(),
        ),
    )
    assert result == _RESULT_NOT_EVALUATED, detail
    assert "OMNI_HOME" in detail
    assert "empty" in detail


# ---------------------------------------------------------------------------
# AC4 -- falsifiability through the gate's own entry point, not a helper
# ---------------------------------------------------------------------------


def _write_contract(contracts_dir: Path, body: str) -> None:
    contracts_dir.mkdir(parents=True, exist_ok=True)
    (contracts_dir / "OMN-16824.yaml").write_text(textwrap.dedent(body))


@pytest.fixture
def green_pr_ci() -> Any:
    """Every ``gh`` call the gate makes reports a fully green PR."""
    pr_json = (
        '{"title": "feat(OMN-16824): x", "headRefName": "jonah/omn-16824", "body": ""}'
    )

    def _fake(cmd: list[str], *args: Any, **kwargs: Any) -> tuple[int, str, str]:
        if cmd[:2] == ["gh", "pr"] and "view" in cmd:
            return 0, pr_json, ""
        if cmd[:2] == ["gh", "pr"] and "checks" in cmd:
            return 0, '[{"name":"CI","state":"SUCCESS"}]', ""
        if cmd[:2] == ["gh", "api"]:
            return 0, "", ""
        return _run(cmd, *args, **kwargs)

    with patch(
        "onex_change_control.scripts.contract_compliance_check._run",
        side_effect=_fake,
    ):
        yield


@pytest.mark.usefixtures("green_pr_ci")
def test_gate_goes_red_on_a_failing_test_passes_entry(tmp_path: Path) -> None:
    """AC4 by execution: the whole gate, not just the check helper."""
    contracts = tmp_path / "contracts"
    _write_contract(
        contracts,
        """
        ticket_id: OMN-16824
        dod_evidence:
          - id: dod-deliberately-failing
            description: A test_passes entry whose check_value fails on purpose
            checks:
              - check_type: test_passes
                check_value: "pytest tests/test_omn16824_no_such_test.py"
        """,
    )
    exit_code = run_compliance_check(
        pr_number=1,
        repo="OmniNode-ai/onex_change_control",
        contracts_dir=contracts,
        workspace=tmp_path,
    )
    assert exit_code == 1


@pytest.mark.usefixtures("green_pr_ci")
def test_gate_is_green_on_a_passing_test_passes_entry(tmp_path: Path) -> None:
    """The discriminating control for the case above."""
    contracts = tmp_path / "contracts"
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "shipped.py").write_text("SENTINEL = 1\n")
    _write_contract(
        contracts,
        """
        ticket_id: OMN-16824
        dod_evidence:
          - id: dod-passing
            description: A test_passes entry whose check_value succeeds
            checks:
              - check_type: test_passes
                check_value: "pytest --version && grep -q SENTINEL src/shipped.py"
        """,
    )
    exit_code = run_compliance_check(
        pr_number=1,
        repo="OmniNode-ai/onex_change_control",
        contracts_dir=contracts,
        workspace=tmp_path,
    )
    assert exit_code == 0
