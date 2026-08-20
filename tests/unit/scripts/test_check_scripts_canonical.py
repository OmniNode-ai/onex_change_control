# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""CLI + loader tests for the scripts/** canonical-form guard (OMN-14475).

Covers the deny-new inventory gate end-to-end: baseline loading, the
CODEOWNERS-approved exceptions registry loader (fail-closed on malformed
entries), the per-repo scan, and the ``--scan-scripts`` exit codes.
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
from onex_change_control.scripts.check_imperative_contracts import (
    main,
    scan_repo_scripts,
)
from onex_change_control.validators.arch_handler_contract_compliance import (
    _load_scripts_baseline,
    _load_scripts_exceptions,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _make_repo(tmp_path: Path, scripts: dict[str, str]) -> Path:
    """Create a minimal repo tree with a src/ package and scripts/."""
    repo = tmp_path / "occ"
    (repo / "src" / "occ").mkdir(parents=True)
    (repo / "src" / "occ" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "scripts").mkdir()
    for rel, body in scripts.items():
        target = repo / "scripts" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(body), encoding="utf-8")
    return repo


# --- baseline loader --------------------------------------------------------


def test_load_scripts_baseline_reads_allowlisted_scripts(tmp_path: Path) -> None:
    allowlist = tmp_path / "occ.yaml"
    allowlist.write_text(
        textwrap.dedent(
            """
            allowlisted_scripts:
              - path: scripts/a.py
                reason: debt
                ticket: OMN-14475
              - path: scripts/b.sh
                reason: debt
                ticket: OMN-14475
            """
        ),
        encoding="utf-8",
    )
    baseline = _load_scripts_baseline(allowlist)
    assert baseline == frozenset({"scripts/a.py", "scripts/b.sh"})


def test_load_scripts_baseline_missing_file_is_empty(tmp_path: Path) -> None:
    assert _load_scripts_baseline(tmp_path / "nope.yaml") == frozenset()


# --- exceptions registry loader (fail-closed) -------------------------------


def test_load_scripts_exceptions_parses_valid_entries(tmp_path: Path) -> None:
    registry = tmp_path / "scripts_exceptions.yaml"
    registry.write_text(
        textwrap.dedent(
            """
            entries:
              - path: scripts/ci/x.py
                repo: omnibase_infra
                disposition: permanent
                ticket: OMN-14479
                reason: CI publish retry wrapper
                approved_by: reviewer
            """
        ),
        encoding="utf-8",
    )
    registry_map = _load_scripts_exceptions(registry)
    key = ("omnibase_infra", "scripts/ci/x.py")
    assert key in registry_map
    assert registry_map[key].disposition is EnumScriptExceptionDisposition.PERMANENT


def test_load_scripts_exceptions_skips_malformed_entries(tmp_path: Path) -> None:
    """A malformed entry grants NOTHING (fail-closed)."""
    registry = tmp_path / "scripts_exceptions.yaml"
    registry.write_text(
        textwrap.dedent(
            """
            entries:
              - path: scripts/bad_ticket.py
                repo: occ
                disposition: permanent
                ticket: NOT-A-TICKET
                reason: bad
              - path: scripts/missing_disposition.py
                repo: occ
                ticket: OMN-1
                reason: bad
              - path: scripts/good.py
                repo: occ
                disposition: convert
                ticket: OMN-14475
                reason: ok
            """
        ),
        encoding="utf-8",
    )
    registry_map = _load_scripts_exceptions(registry)
    # Only the well-formed entry survives.
    assert list(registry_map.keys()) == [("occ", "scripts/good.py")]


def test_load_scripts_exceptions_missing_file_is_empty(tmp_path: Path) -> None:
    assert _load_scripts_exceptions(tmp_path / "nope.yaml") == {}


# --- scan_repo_scripts end-to-end -------------------------------------------


def test_scan_repo_scripts_blocks_new_and_passes_baseline(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        {
            "old.py": '"""old."""\nimport sys\nsys.exit(0)\n',
            "new.py": '"""new."""\nimport sys\nsys.exit(0)\n',
        },
    )
    allowlists = tmp_path / "allowlists"
    allowlists.mkdir()
    (allowlists / "occ.yaml").write_text(
        textwrap.dedent(
            """
            allowlisted_scripts:
              - path: scripts/old.py
                reason: debt
                ticket: OMN-14475
            """
        ),
        encoding="utf-8",
    )
    summary = scan_repo_scripts(repo, allowlists_dir=allowlists)
    verdicts = {r.script_path: r.verdict for r in summary.results}
    assert verdicts["scripts/old.py"] is EnumScriptCanonicalVerdict.ALLOWLISTED
    assert verdicts["scripts/new.py"] is EnumScriptCanonicalVerdict.NEW_UNREGISTERED
    assert summary.blocking_count == 1


def test_scan_repo_scripts_registry_grants_new(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"new.py": '"""new."""\nimport sys\nsys.exit(0)\n'})
    allowlists = tmp_path / "allowlists"
    allowlists.mkdir()
    (allowlists / "occ.yaml").write_text("allowlisted_scripts: []\n", encoding="utf-8")
    (allowlists / "scripts_exceptions.yaml").write_text(
        textwrap.dedent(
            """
            entries:
              - path: scripts/new.py
                repo: occ
                disposition: permanent
                ticket: OMN-14479
                reason: CI glue
                approved_by: reviewer
            """
        ),
        encoding="utf-8",
    )
    summary = scan_repo_scripts(repo, allowlists_dir=allowlists)
    result = summary.results[0]
    assert result.verdict is EnumScriptCanonicalVerdict.EXCEPTION_GRANTED
    assert result.disposition is EnumScriptExceptionDisposition.PERMANENT
    assert summary.blocking_count == 0


# --- CLI exit codes ---------------------------------------------------------


def test_main_scan_scripts_exit_zero_when_clean(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"old.py": "import sys\nsys.exit(0)\n"})
    allowlists = tmp_path / "allowlists"
    allowlists.mkdir()
    (allowlists / "occ.yaml").write_text(
        "allowlisted_scripts:\n  - path: scripts/old.py\n    ticket: OMN-14475\n",
        encoding="utf-8",
    )
    code = main(
        [
            "--repo-root",
            str(repo),
            "--allowlists-dir",
            str(allowlists),
            "--scan-scripts",
        ]
    )
    assert code == 0


def test_main_scan_scripts_exit_one_when_new_script(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, {"new.py": "import sys\nsys.exit(0)\n"})
    allowlists = tmp_path / "allowlists"
    allowlists.mkdir()
    (allowlists / "occ.yaml").write_text("allowlisted_scripts: []\n", encoding="utf-8")
    code = main(
        [
            "--repo-root",
            str(repo),
            "--allowlists-dir",
            str(allowlists),
            "--scan-scripts",
        ]
    )
    assert code == 1


def test_main_generate_scripts_baseline_exit_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path, {"a.py": "x = 1\n", "b.sh": "echo hi\n"})
    code = main(["--repo-root", str(repo), "--generate-scripts-baseline"])
    assert code == 0
    out = capsys.readouterr().out
    assert "allowlisted_scripts:" in out
    assert "scripts/a.py" in out
    assert "scripts/b.sh" in out
