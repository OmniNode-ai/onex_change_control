# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for the scripts/** canonical-form scanner (DEFAULT-DENY, OMN-14475).

The imperative-contract scanner only sees ``src/``; this scanner governs
``scripts/**`` under a deterministic deny-new inventory policy. A new script
passes only if it is in the frozen baseline or the CODEOWNERS-approved
exceptions registry. The AST scorer is advisory-only, with exactly one binary
hard check: a ``node-backed`` registry entry with no dispatch is a false claim.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from onex_change_control.enums.enum_script_canonical_verdict import (
    EnumScriptCanonicalVerdict,
)
from onex_change_control.enums.enum_script_exception_disposition import (
    EnumScriptExceptionDisposition,
)
from onex_change_control.enums.enum_script_file_kind import EnumScriptFileKind
from onex_change_control.models.model_script_exception import ModelScriptException
from onex_change_control.scanners.script_canonical_form import (
    DEFAULT_SHIM_CEILING,
    classify_script,
    is_governed_script,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def _exception(
    disposition: EnumScriptExceptionDisposition,
    *,
    path: str = "scripts/p.py",
) -> ModelScriptException:
    return ModelScriptException(
        path=path,
        repo="occ",
        disposition=disposition,
        ticket="OMN-14475",
        reason="test",
        approved_by="reviewer",
    )


# --- is_governed_script ------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "governed"),
    [
        ("deploy.py", True),
        ("deploy.sh", True),
        ("deploy.bash", True),
        ("__init__.py", False),
        ("config.yaml", False),
        ("notes.txt", False),
        ("README.md", False),
    ],
)
def test_is_governed_script(tmp_path: Path, name: str, *, governed: bool) -> None:
    assert is_governed_script(tmp_path / name) is governed


# --- baseline = pre-existing debt -------------------------------------------


def test_baselined_script_passes_regardless_of_logic(tmp_path: Path) -> None:
    script = _write(tmp_path / "s.py", "x = 0\nfor i in range(9):\n    x += i * 2\n")
    result = classify_script(
        script, repo="occ", in_baseline=True, rel_path="scripts/s.py"
    )
    assert result.verdict is EnumScriptCanonicalVerdict.ALLOWLISTED
    assert result.blocking is False
    assert result.is_new is False


# --- deny-new: the default -------------------------------------------------


def test_new_unregistered_script_is_blocked(tmp_path: Path) -> None:
    script = _write(tmp_path / "s.py", '"""thin."""\nimport sys\nsys.exit(0)\n')
    result = classify_script(
        script, repo="occ", in_baseline=False, exception=None, rel_path="scripts/s.py"
    )
    assert result.verdict is EnumScriptCanonicalVerdict.NEW_UNREGISTERED
    assert result.blocking is True
    assert result.is_new is True


def test_shell_new_unregistered_is_blocked(tmp_path: Path) -> None:
    script = _write(tmp_path / "s.sh", "#!/usr/bin/env bash\necho hi\n")
    result = classify_script(
        script, repo="occ", in_baseline=False, exception=None, rel_path="scripts/s.sh"
    )
    assert result.file_kind is EnumScriptFileKind.SHELL
    assert result.verdict is EnumScriptCanonicalVerdict.NEW_UNREGISTERED
    assert result.blocking is True


# --- node-backed: the one binary hard check ---------------------------------


def test_node_backed_with_dispatch_passes(tmp_path: Path) -> None:
    script = _write(
        tmp_path / "s.py",
        """
        import subprocess

        subprocess.run(["onex", "run-node", "node_x"], check=True)
        """,
    )
    result = classify_script(
        script,
        repo="occ",
        in_baseline=False,
        exception=_exception(EnumScriptExceptionDisposition.NODE_BACKED),
        rel_path="scripts/p.py",
    )
    assert result.has_dispatch is True
    assert result.verdict is EnumScriptCanonicalVerdict.EXCEPTION_GRANTED
    assert result.blocking is False


def test_node_backed_without_dispatch_is_blocked(tmp_path: Path) -> None:
    """A node-backed claim with no dispatch is a false claim — the hard check."""
    script = _write(
        tmp_path / "s.py",
        "x = 0\nfor i in range(9):\n    if i > 3:\n        x += i * 2\n",
    )
    result = classify_script(
        script,
        repo="occ",
        in_baseline=False,
        exception=_exception(EnumScriptExceptionDisposition.NODE_BACKED),
        rel_path="scripts/p.py",
    )
    assert result.has_dispatch is False
    assert result.verdict is EnumScriptCanonicalVerdict.FALSE_NODE_BACKED
    assert result.blocking is True


def test_node_backed_dispatch_via_import(tmp_path: Path) -> None:
    script = _write(
        tmp_path / "s.py",
        "from omnibase_infra.nodes.node_x.node import NodeX\n\nNodeX().run()\n",
    )
    result = classify_script(
        script,
        repo="occ",
        in_baseline=False,
        exception=_exception(EnumScriptExceptionDisposition.NODE_BACKED),
        rel_path="scripts/p.py",
    )
    assert result.has_dispatch is True
    assert result.blocking is False


def test_shell_node_backed_dispatch_via_text(tmp_path: Path) -> None:
    script = _write(
        tmp_path / "s.sh",
        "#!/usr/bin/env bash\nonex run-node node_x --arg 1\n",
    )
    result = classify_script(
        script,
        repo="occ",
        in_baseline=False,
        exception=_exception(
            EnumScriptExceptionDisposition.NODE_BACKED, path="scripts/p.sh"
        ),
        rel_path="scripts/p.sh",
    )
    assert result.has_dispatch is True
    assert result.blocking is False


# --- permanent: never blocked; ceiling is a loud advisory only --------------


def test_permanent_under_ceiling_passes_no_advisory(tmp_path: Path) -> None:
    script = _write(tmp_path / "s.py", '"""glue."""\nimport sys\nsys.exit(0)\n')
    result = classify_script(
        script,
        repo="occ",
        in_baseline=False,
        exception=_exception(EnumScriptExceptionDisposition.PERMANENT),
        rel_path="scripts/p.py",
    )
    assert result.verdict is EnumScriptCanonicalVerdict.EXCEPTION_GRANTED
    assert result.logic_advisory is False
    assert result.blocking is False


def test_permanent_over_ceiling_passes_with_loud_advisory(tmp_path: Path) -> None:
    """A permanent entry over the ceiling is NOT blocked — advisory only.

    CODEOWNERS approval is the authority for a permanent entry; the AST score
    must never override an approved human decision.
    """
    # Build a script that clears the ceiling: many magnitude compares + loops.
    body = "x = 0\n" + "".join(
        f"for i{n} in range(9):\n    if i{n} > 3:\n        x += i{n} * 2\n"
        for n in range(6)
    )
    script = _write(tmp_path / "s.py", body)
    result = classify_script(
        script,
        repo="occ",
        in_baseline=False,
        exception=_exception(EnumScriptExceptionDisposition.PERMANENT),
        rel_path="scripts/p.py",
    )
    assert result.logic_score >= DEFAULT_SHIM_CEILING
    assert result.logic_advisory is True
    assert result.verdict is EnumScriptCanonicalVerdict.EXCEPTION_GRANTED
    assert result.blocking is False  # advisory, NOT a block


# --- convert: logic expected, no ceiling ------------------------------------


def test_convert_heavy_logic_passes(tmp_path: Path) -> None:
    body = "x = 0\n" + "".join(
        f"for i{n} in range(9):\n    if i{n} > 3:\n        x += i{n} * 2\n"
        for n in range(6)
    )
    script = _write(tmp_path / "s.py", body)
    result = classify_script(
        script,
        repo="occ",
        in_baseline=False,
        exception=_exception(EnumScriptExceptionDisposition.CONVERT),
        rel_path="scripts/p.py",
    )
    assert result.verdict is EnumScriptCanonicalVerdict.EXCEPTION_GRANTED
    assert result.logic_advisory is False  # convert is exempt from the advisory
    assert result.blocking is False


# --- falsifiable negative control: score cannot decide pass -----------------


def test_git_orchestration_scores_low_but_is_still_blocked(tmp_path: Path) -> None:
    """deploy_source_ref-style git orchestration scores ~0 under the heuristic.

    This is the evidence the inventory gate — not the score — must decide: a
    heavy subprocess-orchestration script looks like a shim to the scorer, yet
    the deny-new gate blocks it because it is new and unregistered.
    """
    script = _write(
        tmp_path / "s.py",
        """
        import subprocess

        def checkout(repo: str, ref: str) -> None:
            subprocess.run(["git", "-C", repo, "fetch", "--prune"], check=True)
            subprocess.run(["git", "-C", repo, "checkout", ref], check=True)
            subprocess.run(["git", "-C", repo, "reset", "--hard", ref], check=True)
            subprocess.run(["git", "-C", repo, "clean", "-ffdx"], check=True)
        """,
    )
    result = classify_script(
        script, repo="occ", in_baseline=False, exception=None, rel_path="scripts/p.py"
    )
    assert result.logic_score < DEFAULT_SHIM_CEILING  # the scorer under-counts it
    assert (
        result.verdict is EnumScriptCanonicalVerdict.NEW_UNREGISTERED
    )  # blocked anyway
    assert result.blocking is True
