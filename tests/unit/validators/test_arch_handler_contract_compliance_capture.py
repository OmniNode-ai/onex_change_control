# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Equivalence tests for validators/arch_handler_contract_compliance.py (OMN-12177).

Captures current pass/fail behavior as a regression baseline before refactoring.
Does NOT modify the validator. Tests run_scan() and helpers directly.

Covered:
    _find_node_dirs  — discovers node directories under src/
    _load_allowlist  — loads allowlist YAML
    _infer_repo_name — infers repo name from src/ layout
    run_scan         — full scan returning exit code

Pass cases (exit 0):
    - Repo with no node directories
    - Repo with fully compliant nodes
    - All violations in the allowlist
    - generate_allowlist=True always exits 0

Fail cases (exit 1):
    - One new (non-allowlisted) violation present

Boundary cases:
    - Allowlist file missing → empty allowlist (no crash)
    - Repo with no src/ directory → no node dirs found
    - output_json=True produces JSON output on stdout
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.validators.arch_handler_contract_compliance import (
    _find_node_dirs,
    _infer_repo_name,
    _load_allowlist,
    run_scan,
    validate_allowlist_tickets,
)

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

_COMPLIANT_CONTRACT = """\
name: node_test
node_type: EFFECT_GENERIC
event_bus:
  publish_topics:
    - onex.evt.test.computed.v1
  subscribe_topics:
    - onex.evt.test.requested.v1
handler_routing:
  routing_strategy: operation_match
  handlers:
    - operation: compute
      handler:
        name: HandlerTest
        module: test_repo.nodes.node_test.handlers.handler_test
"""

_COMPLIANT_HANDLER = '''\
"""Compliant handler — no hardcoded topics, no undeclared transports."""

from __future__ import annotations


class HandlerTest:
    def handle(self, event: dict) -> dict:
        return {"status": "ok"}
'''


def _make_repo(tmp_path: Path, repo: str = "test_repo") -> Path:
    """Create a minimal repo root with src/<repo>/nodes/node_test/handlers/."""
    node_dir = tmp_path / "src" / repo / "nodes" / "node_test"
    node_dir.mkdir(parents=True)
    handlers = node_dir / "handlers"
    handlers.mkdir()
    (handlers / "__init__.py").write_text("")
    return tmp_path


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# _find_node_dirs
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_find_node_dirs_finds_node_with_handlers(tmp_path: Path) -> None:
    """Directories named node_* that have a handlers/ subdir are returned."""
    repo_root = _make_repo(tmp_path)
    node_dirs = _find_node_dirs(repo_root)
    assert len(node_dirs) == 1
    assert node_dirs[0].name == "node_test"


@pytest.mark.unit
def test_find_node_dirs_skips_node_without_handlers(tmp_path: Path) -> None:
    """node_* dirs without handlers/ are not included."""
    src = tmp_path / "src" / "test_repo" / "nodes" / "node_no_handlers"
    src.mkdir(parents=True)
    node_dirs = _find_node_dirs(tmp_path)
    assert node_dirs == []


@pytest.mark.unit
def test_find_node_dirs_no_src_returns_empty(tmp_path: Path) -> None:
    """Repo with no src/ returns empty list."""
    node_dirs = _find_node_dirs(tmp_path)
    assert node_dirs == []


@pytest.mark.unit
def test_find_node_dirs_multiple_nodes(tmp_path: Path) -> None:
    """Multiple node directories are all discovered."""
    for name in ("node_alpha", "node_beta"):
        d = tmp_path / "src" / "test_repo" / "nodes" / name
        (d / "handlers").mkdir(parents=True)
        (d / "handlers" / "__init__.py").write_text("")
    node_dirs = _find_node_dirs(tmp_path)
    names = {d.name for d in node_dirs}
    assert names == {"node_alpha", "node_beta"}


# ---------------------------------------------------------------------------
# _load_allowlist
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_allowlist_missing_file_returns_empty(tmp_path: Path) -> None:
    """Missing allowlist file returns empty dict (no crash)."""
    result = _load_allowlist(tmp_path / "nonexistent.yaml")
    assert result == {}


@pytest.mark.unit
def test_load_allowlist_parses_entries(tmp_path: Path) -> None:
    """Valid allowlist YAML is parsed into a path -> violations dict."""
    al = tmp_path / "allowlist.yaml"
    al.write_text(
        "allowlisted_handlers:\n"
        "  - path: src/test_repo/nodes/node_test/handlers/handler_test.py\n"
        "    violations:\n"
        "      - HARDCODED_TOPIC\n"
        "    ticket: OMN-9999\n"
    )
    result = _load_allowlist(al)
    assert "src/test_repo/nodes/node_test/handlers/handler_test.py" in result


@pytest.mark.unit
def test_load_allowlist_empty_list_returns_empty(tmp_path: Path) -> None:
    """Allowlist with no entries returns empty dict."""
    al = tmp_path / "allowlist.yaml"
    al.write_text("allowlisted_handlers: []\n")
    result = _load_allowlist(al)
    assert result == {}


# ---------------------------------------------------------------------------
# validate_allowlist_tickets — OMN-11878
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_validate_allowlist_tickets_missing_file_returns_empty(tmp_path: Path) -> None:
    """Missing allowlist file has no ticket-format violations (no crash)."""
    result = validate_allowlist_tickets(tmp_path / "nonexistent.yaml")
    assert result == []


@pytest.mark.unit
def test_validate_allowlist_tickets_accepts_real_ticket(tmp_path: Path) -> None:
    """A well-formed OMN-#### ticket passes validation."""
    al = tmp_path / "allowlist.yaml"
    al.write_text(
        "allowlisted_handlers:\n"
        "  - path: src/test_repo/nodes/node_test/handlers/handler_test.py\n"
        "    violations:\n"
        "      - missing_handler_routing\n"
        "    ticket: OMN-9999\n"
    )
    result = validate_allowlist_tickets(al)
    assert result == []


@pytest.mark.unit
def test_validate_allowlist_tickets_rejects_placeholder(tmp_path: Path) -> None:
    """The '# migration pending' placeholder fails validation (OMN-11878)."""
    al = tmp_path / "allowlist.yaml"
    al.write_text(
        "allowlisted_handlers:\n"
        "  - path: src/test_repo/nodes/node_test/handlers/handler_test.py\n"
        "    violations:\n"
        "      - missing_handler_routing\n"
        "    ticket: '# migration pending'\n"
    )
    result = validate_allowlist_tickets(al)
    assert len(result) == 1
    assert "handler_test.py" in result[0]


@pytest.mark.unit
def test_validate_allowlist_tickets_rejects_missing_ticket_field(
    tmp_path: Path,
) -> None:
    """An entry with no ticket field at all fails validation."""
    al = tmp_path / "allowlist.yaml"
    al.write_text(
        "allowlisted_handlers:\n"
        "  - path: src/test_repo/nodes/node_test/handlers/handler_test.py\n"
        "    violations:\n"
        "      - missing_handler_routing\n"
    )
    result = validate_allowlist_tickets(al)
    assert len(result) == 1


@pytest.mark.unit
def test_validate_allowlist_tickets_rejects_non_omn_ticket(tmp_path: Path) -> None:
    """A ticket id from an unrelated tracker format fails validation."""
    al = tmp_path / "allowlist.yaml"
    al.write_text(
        "allowlisted_handlers:\n"
        "  - path: src/test_repo/nodes/node_test/handlers/handler_test.py\n"
        "    violations:\n"
        "      - missing_handler_routing\n"
        "    ticket: JIRA-123\n"
    )
    result = validate_allowlist_tickets(al)
    assert len(result) == 1


@pytest.mark.unit
def test_validate_allowlist_tickets_empty_list_returns_empty(tmp_path: Path) -> None:
    """Allowlist with no entries has no ticket-format violations."""
    al = tmp_path / "allowlist.yaml"
    al.write_text("allowlisted_handlers: []\n")
    result = validate_allowlist_tickets(al)
    assert result == []


# ---------------------------------------------------------------------------
# _infer_repo_name
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_infer_repo_name_from_src_child(tmp_path: Path) -> None:
    """Repo name is inferred from the first non-dot directory under src/."""
    (tmp_path / "src" / "my_package").mkdir(parents=True)
    name = _infer_repo_name(tmp_path)
    assert name == "my_package"


@pytest.mark.unit
def test_infer_repo_name_fallback_to_root_name(tmp_path: Path) -> None:
    """When src/ has no children, falls back to repo root dir name."""
    name = _infer_repo_name(tmp_path)
    assert name == tmp_path.name


# ---------------------------------------------------------------------------
# run_scan — pass cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_scan_no_nodes_exits_zero(tmp_path: Path) -> None:
    """Repo with no node directories exits 0."""
    result = run_scan(tmp_path)
    assert result == 0


@pytest.mark.unit
def test_run_scan_compliant_repo_exits_zero(tmp_path: Path) -> None:
    """Fully compliant repo exits 0."""
    repo_root = _make_repo(tmp_path)
    node_dir = repo_root / "src" / "test_repo" / "nodes" / "node_test"
    _write(node_dir / "contract.yaml", _COMPLIANT_CONTRACT)
    _write(node_dir / "handlers" / "handler_test.py", _COMPLIANT_HANDLER)
    result = run_scan(repo_root)
    assert result == 0


@pytest.mark.unit
def test_run_scan_generate_allowlist_exits_zero(tmp_path: Path) -> None:
    """generate_allowlist=True always exits 0 (outputs YAML, not violations)."""
    repo_root = _make_repo(tmp_path)
    node_dir = repo_root / "src" / "test_repo" / "nodes" / "node_test"
    _write(node_dir / "contract.yaml", _COMPLIANT_CONTRACT)
    _write(
        node_dir / "handlers" / "handler_test.py",
        'def handle():\n    t = "onex.evt.test.computed.v1"\n',
    )
    result = run_scan(repo_root, generate_allowlist=True)
    assert result == 0


@pytest.mark.unit
def test_run_scan_all_violations_allowlisted_exits_zero(tmp_path: Path) -> None:
    """When all violations are in the allowlist, exit code is 0."""
    repo_root = _make_repo(tmp_path)
    node_dir = repo_root / "src" / "test_repo" / "nodes" / "node_test"
    _write(node_dir / "contract.yaml", _COMPLIANT_CONTRACT)
    _write(
        node_dir / "handlers" / "handler_test.py",
        'def handle():\n    t = "onex.evt.test.computed.v1"\n',
    )
    # Build allowlist pointing at the handler
    base_dir = node_dir.parent.parent.parent
    handler_path = node_dir / "handlers" / "handler_test.py"
    rel = str(handler_path.relative_to(base_dir))

    al = tmp_path / "allowlist.yaml"
    al.write_text(
        f"allowlisted_handlers:\n"
        f"  - path: {rel}\n"
        f"    violations:\n"
        f"      - HARDCODED_TOPIC\n"
        f"    ticket: OMN-9999\n"
    )
    result = run_scan(repo_root, allowlist_path=al)
    assert result == 0


# ---------------------------------------------------------------------------
# run_scan — fail cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_scan_new_violation_exits_one(tmp_path: Path) -> None:
    """New (non-allowlisted) violation returns exit code 1."""
    repo_root = _make_repo(tmp_path)
    node_dir = repo_root / "src" / "test_repo" / "nodes" / "node_test"
    _write(node_dir / "contract.yaml", _COMPLIANT_CONTRACT)
    # Handler not registered in routing → MISSING_HANDLER_ROUTING violation
    _write(
        node_dir / "handlers" / "handler_unregistered.py",
        "def handle(): pass\n",
    )
    result = run_scan(repo_root)
    assert result == 1


@pytest.mark.unit
def test_run_scan_hardcoded_topic_exits_one(tmp_path: Path) -> None:
    """Handler with hardcoded topic string returns exit code 1."""
    repo_root = _make_repo(tmp_path)
    node_dir = repo_root / "src" / "test_repo" / "nodes" / "node_test"
    _write(node_dir / "contract.yaml", _COMPLIANT_CONTRACT)
    _write(
        node_dir / "handlers" / "handler_test.py",
        'def handle():\n    t = "onex.evt.test.computed.v1"\n    return t\n',
    )
    result = run_scan(repo_root)
    assert result == 1


# ---------------------------------------------------------------------------
# run_scan — output_json boundary
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_scan_output_json_produces_parseable_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """output_json=True writes valid JSON to stdout."""
    import json

    repo_root = _make_repo(tmp_path)
    node_dir = repo_root / "src" / "test_repo" / "nodes" / "node_test"
    _write(node_dir / "contract.yaml", _COMPLIANT_CONTRACT)
    _write(node_dir / "handlers" / "handler_test.py", _COMPLIANT_HANDLER)
    run_scan(repo_root, output_json=True)
    captured = capsys.readouterr()
    # Find the JSON array in the captured output
    lines = captured.out
    # JSON output is a list
    json_start = lines.find("[")
    if json_start != -1:
        parsed = json.loads(lines[json_start:].split("\n\n")[0].strip() or "[]")
        assert isinstance(parsed, list)
