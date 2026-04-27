# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for scripts/validation/validate_string_versions.py.

Covers:
- Python __init__.py hardcoded __version__ detection
- YAML string version detection
- Ticket-contract exemption (OMN-9593): contracts/OMN-*.yaml files are exempt
  because their schema_version is validated by ModelTicketContract.field_validator
"""

from pathlib import Path

from scripts.validation.validate_string_versions import (
    _has_hardcoded_version,
    _has_string_version_in_yaml,
    _is_ticket_contract,
)

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts" / "validation"


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


class TestPythonInitVersion:
    def test_detects_hardcoded_version(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "__init__.py", '__version__ = "1.2.3"\n')
        violations = _has_hardcoded_version(p)
        assert len(violations) == 1
        assert "1.2.3" in violations[0][1]

    def test_clean_init_passes(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "__init__.py",
            'from importlib.metadata import version\n__version__ = version("pkg")\n',
        )
        assert _has_hardcoded_version(p) == []

    def test_except_fallback_allowed(self, tmp_path: Path) -> None:
        src = (
            "try:\n"
            "    from importlib.metadata import version\n"
            "    __version__ = version('pkg')\n"
            "except Exception:\n"
            "    __version__ = '0.0.0'\n"
        )
        p = _write(tmp_path, "__init__.py", src)
        assert _has_hardcoded_version(p) == []


class TestYamlStringVersion:
    def test_detects_string_version_in_yaml(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "config.yaml", 'version: "1.0.0"\n')
        violations = _has_string_version_in_yaml(p)
        assert len(violations) == 1
        assert "ModelSemVer" in violations[0][1]

    def test_detects_schema_version_in_generic_yaml(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "service.yaml", 'schema_version: "2.3.1"\n')
        violations = _has_string_version_in_yaml(p)
        assert len(violations) == 1

    def test_mapping_version_passes(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path, "config.yaml", "version:\n  major: 1\n  minor: 0\n  patch: 0\n"
        )
        assert _has_string_version_in_yaml(p) == []


class TestTicketContractExemption:
    def test_contracts_dir_exempt(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "contracts/OMN-9593.yaml",
            'schema_version: "1.0.0"\nticket_id: OMN-9593\n',
        )
        assert _is_ticket_contract(p) is True
        assert _has_string_version_in_yaml(p) == []

    def test_non_contract_yaml_not_exempt(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "deploy.yaml", 'schema_version: "1.0.0"\n')
        assert _is_ticket_contract(p) is False
        violations = _has_string_version_in_yaml(p)
        assert len(violations) == 1

    def test_template_not_exempt(self, tmp_path: Path) -> None:
        p = _write(
            tmp_path,
            "templates/ticket_contract.yaml",
            'schema_version: "1.0.0"\n',
        )
        assert _is_ticket_contract(p) is False
        assert len(_has_string_version_in_yaml(p)) == 1
