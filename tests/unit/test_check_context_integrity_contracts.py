# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Unit tests for check_context_integrity_contracts.py (OMN-5243)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# The script lives outside the installed package tree in scripts/ at the repo root.
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from check_context_integrity_contracts import (  # noqa: E402
    _build_contract_registry,
    _contract_has_context_integrity,
    _extract_contract_id,
    _load_registry_list,
    run_check,
    scan_agent_configs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    """Write a YAML file under tmp_path and return its path."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _make_contract(
    tmp_path: Path,
    node_name: str,
    *,
    with_context_integrity: bool = True,
) -> Path:
    """Create a minimal contract.yaml under tmp_path/node_name/ and return it."""
    node_dir = tmp_path / node_name
    node_dir.mkdir(parents=True, exist_ok=True)
    ci_section = (
        "\ncontext_integrity:\n  enforcement_level: WARN\n"
        if with_context_integrity
        else ""
    )
    content = f"name: {node_name}\ncontract_name: {node_name}\n{ci_section}"
    path = node_dir / "contract.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def _make_agent(
    tmp_path: Path,
    name: str,
    *,
    contract_id: str | None = None,
) -> Path:
    """Create a minimal agent YAML config and return its path."""
    if contract_id is not None:
        metadata_block = f"metadata:\n  context_integrity_contract_id: {contract_id}\n"
    else:
        metadata_block = ""
    content = f"agent_type: {name}\n{metadata_block}"
    path = tmp_path / f"{name}.yaml"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _extract_contract_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractContractId:
    """Tests for _extract_contract_id."""

    def test_returns_contract_id_when_present(self, tmp_path: Path) -> None:
        agent = _make_agent(tmp_path, "my_agent", contract_id="node_foo")
        assert _extract_contract_id(agent) == "node_foo"

    def test_returns_none_when_no_metadata(self, tmp_path: Path) -> None:
        agent = _make_agent(tmp_path, "my_agent")
        assert _extract_contract_id(agent) is None

    def test_returns_none_when_metadata_missing_key(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, "a.yaml", "metadata:\n  other_key: value\n")
        assert _extract_contract_id(path) is None

    def test_returns_none_for_invalid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("{{{not valid yaml", encoding="utf-8")
        assert _extract_contract_id(path) is None

    def test_strips_whitespace_from_contract_id(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "a.yaml",
            "metadata:\n  context_integrity_contract_id: '  node_bar  '\n",
        )
        assert _extract_contract_id(path) == "node_bar"

    def test_returns_none_for_empty_string_contract_id(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path, "a.yaml", "metadata:\n  context_integrity_contract_id: ''\n"
        )
        assert _extract_contract_id(path) is None


# ---------------------------------------------------------------------------
# _contract_has_context_integrity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContractHasContextIntegrity:
    """Tests for _contract_has_context_integrity."""

    def test_top_level_key_detected(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path,
            "contract.yaml",
            "name: foo\ncontext_integrity:\n  enforcement_level: WARN\n",
        )
        assert _contract_has_context_integrity(path) is True

    def test_subcontracts_dict_detected(self, tmp_path: Path) -> None:
        content = (
            "name: foo\nsubcontracts:\n"
            "  context_integrity:\n    enforcement_level: STRICT\n"
        )
        path = _write_yaml(tmp_path, "contract.yaml", content)
        assert _contract_has_context_integrity(path) is True

    def test_subcontracts_list_detected(self, tmp_path: Path) -> None:
        content = "name: foo\nsubcontracts:\n  - context_integrity\n  - observability\n"
        path = _write_yaml(tmp_path, "contract.yaml", content)
        assert _contract_has_context_integrity(path) is True

    def test_handlers_list_detected(self, tmp_path: Path) -> None:
        content = (
            "name: foo\nhandlers:\n  - name: h1\n"
            "    context_integrity:\n      enforcement_level: PARANOID\n"
        )
        path = _write_yaml(
            tmp_path,
            "contract.yaml",
            content,
        )
        assert _contract_has_context_integrity(path) is True

    def test_absent_returns_false(self, tmp_path: Path) -> None:
        path = _write_yaml(
            tmp_path, "contract.yaml", "name: foo\nevent_bus:\n  subscribe_topics: []\n"
        )
        assert _contract_has_context_integrity(path) is False

    def test_invalid_yaml_returns_false(self, tmp_path: Path) -> None:
        path = tmp_path / "contract.yaml"
        path.write_text("{{{not valid", encoding="utf-8")
        assert _contract_has_context_integrity(path) is False


# ---------------------------------------------------------------------------
# _build_contract_registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildContractRegistry:
    """Tests for _build_contract_registry."""

    def test_indexes_by_contract_name(self, tmp_path: Path) -> None:
        _make_contract(tmp_path, "node_alpha", with_context_integrity=True)
        registry = _build_contract_registry(tmp_path)
        assert "node_alpha" in registry

    def test_indexes_by_directory_stem(self, tmp_path: Path) -> None:
        node_dir = tmp_path / "node_beta"
        node_dir.mkdir()
        (node_dir / "contract.yaml").write_text("other_key: value\n", encoding="utf-8")
        registry = _build_contract_registry(tmp_path)
        assert "node_beta" in registry

    def test_empty_directory_returns_empty_registry(self, tmp_path: Path) -> None:
        registry = _build_contract_registry(tmp_path)
        assert registry == {}

    def test_nested_contracts_are_found(self, tmp_path: Path) -> None:
        nested = tmp_path / "group" / "node_gamma"
        nested.mkdir(parents=True)
        (nested / "contract.yaml").write_text("name: node_gamma\n", encoding="utf-8")
        registry = _build_contract_registry(tmp_path)
        assert "node_gamma" in registry


# ---------------------------------------------------------------------------
# scan_agent_configs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScanAgentConfigs:
    """Tests for scan_agent_configs."""

    def test_finds_yaml_files(self, tmp_path: Path) -> None:
        _make_agent(tmp_path, "agent_a")
        _make_agent(tmp_path, "agent_b")
        found = scan_agent_configs(tmp_path)
        names = {p.name for p in found}
        assert "agent_a.yaml" in names
        assert "agent_b.yaml" in names

    def test_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        assert scan_agent_configs(tmp_path) == []


# ---------------------------------------------------------------------------
# run_check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunCheck:
    """Tests for run_check."""

    def test_clean_when_no_agents_have_contract_id(self, tmp_path: Path) -> None:
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        _make_agent(agents_root, "agent_noci")

        contracts_root = tmp_path / "contracts"
        contracts_root.mkdir()

        result = run_check(agents_root=agents_root, contracts_root=contracts_root)
        assert result.is_clean
        assert result.agents_scanned == 1
        assert result.agents_with_contract_id == 0

    def test_clean_when_contract_id_resolves_with_context_integrity(
        self, tmp_path: Path
    ) -> None:
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        _make_agent(agents_root, "agent_good", contract_id="node_handler")

        contracts_root = tmp_path / "contracts"
        contracts_root.mkdir()
        _make_contract(contracts_root, "node_handler", with_context_integrity=True)

        result = run_check(agents_root=agents_root, contracts_root=contracts_root)
        assert result.is_clean
        assert result.agents_with_contract_id == 1

    def test_missing_contract_reported(self, tmp_path: Path) -> None:
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        _make_agent(agents_root, "agent_bad", contract_id="node_missing")

        contracts_root = tmp_path / "contracts"
        contracts_root.mkdir()

        result = run_check(agents_root=agents_root, contracts_root=contracts_root)
        assert not result.is_clean
        assert len(result.issues) == 1
        assert result.issues[0].issue_type == "missing_contract"
        assert result.issues[0].contract_id == "node_missing"

    def test_no_context_integrity_reported(self, tmp_path: Path) -> None:
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        _make_agent(agents_root, "agent_noci", contract_id="node_plain")

        contracts_root = tmp_path / "contracts"
        contracts_root.mkdir()
        _make_contract(contracts_root, "node_plain", with_context_integrity=False)

        result = run_check(agents_root=agents_root, contracts_root=contracts_root)
        assert not result.is_clean
        assert result.issues[0].issue_type == "no_context_integrity"

    def test_stale_reference_reported_when_registry_ids_provided(
        self, tmp_path: Path
    ) -> None:
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        _make_agent(agents_root, "agent_stale", contract_id="node_old")

        contracts_root = tmp_path / "contracts"
        contracts_root.mkdir()
        # Contract exists but ID is not in the canonical list
        _make_contract(contracts_root, "node_old", with_context_integrity=True)

        result = run_check(
            agents_root=agents_root,
            contracts_root=contracts_root,
            registry_ids=["node_current"],
        )
        assert not result.is_clean
        assert result.issues[0].issue_type == "stale_reference"

    def test_no_stale_when_registry_ids_none(self, tmp_path: Path) -> None:
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        _make_agent(agents_root, "agent_ok", contract_id="node_any")

        contracts_root = tmp_path / "contracts"
        contracts_root.mkdir()
        _make_contract(contracts_root, "node_any", with_context_integrity=True)

        result = run_check(
            agents_root=agents_root, contracts_root=contracts_root, registry_ids=None
        )
        assert result.is_clean

    def test_valid_id_in_registry_ids_is_clean(self, tmp_path: Path) -> None:
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        _make_agent(agents_root, "agent_valid", contract_id="node_known")

        contracts_root = tmp_path / "contracts"
        contracts_root.mkdir()
        _make_contract(contracts_root, "node_known", with_context_integrity=True)

        result = run_check(
            agents_root=agents_root,
            contracts_root=contracts_root,
            registry_ids=["node_known"],
        )
        assert result.is_clean


# ---------------------------------------------------------------------------
# _load_registry_list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadRegistryList:
    """Tests for _load_registry_list."""

    def test_loads_plain_list(self, tmp_path: Path) -> None:
        path = tmp_path / "ids.txt"
        path.write_text("node_alpha\nnode_beta\n", encoding="utf-8")
        assert _load_registry_list(path) == ["node_alpha", "node_beta"]

    def test_ignores_blank_lines_and_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "ids.txt"
        path.write_text("# comment\n\nnode_gamma\n\n", encoding="utf-8")
        assert _load_registry_list(path) == ["node_gamma"]


# ---------------------------------------------------------------------------
# Dry-run CLI smoke test
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDryRunCli:
    """Dry-run CLI smoke tests for check_context_integrity_contracts.py."""

    def test_dry_run_clean(self, tmp_path: Path) -> None:
        """--dry-run exits 0 even when the contracts root is empty."""
        agents_root = tmp_path / "agents"
        agents_root.mkdir()

        contracts_root = tmp_path / "contracts"
        contracts_root.mkdir()

        result = subprocess.run(
            [
                sys.executable,
                "scripts/check_context_integrity_contracts.py",
                "--agents-root",
                str(agents_root),
                "--contracts-root",
                str(contracts_root),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_dry_run_with_issues_still_exits_0(self, tmp_path: Path) -> None:
        """--dry-run exits 0 even when issues are found."""
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        _make_agent(agents_root, "bad_agent", contract_id="nonexistent_contract")

        contracts_root = tmp_path / "contracts"
        contracts_root.mkdir()

        result = subprocess.run(
            [
                sys.executable,
                "scripts/check_context_integrity_contracts.py",
                "--agents-root",
                str(agents_root),
                "--contracts-root",
                str(contracts_root),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert (
            "missing_contract" in result.stdout.lower()
            or "issue" in result.stdout.lower()
        )

    def test_non_dry_run_exits_1_on_issues(self, tmp_path: Path) -> None:
        """Without --dry-run, exits 1 when issues are found."""
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        _make_agent(agents_root, "bad_agent", contract_id="ghost_contract")

        contracts_root = tmp_path / "contracts"
        contracts_root.mkdir()

        result = subprocess.run(
            [
                sys.executable,
                "scripts/check_context_integrity_contracts.py",
                "--agents-root",
                str(agents_root),
                "--contracts-root",
                str(contracts_root),
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
            check=False,
        )
        assert result.returncode == 1

    def test_missing_agents_root_exits_2(self, tmp_path: Path) -> None:
        """--agents-root pointing to nonexistent dir exits 2."""
        contracts_root = tmp_path / "contracts"
        contracts_root.mkdir()

        result = subprocess.run(
            [
                sys.executable,
                "scripts/check_context_integrity_contracts.py",
                "--agents-root",
                str(tmp_path / "no_such_dir"),
                "--contracts-root",
                str(contracts_root),
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
            check=False,
        )
        assert result.returncode == 2
