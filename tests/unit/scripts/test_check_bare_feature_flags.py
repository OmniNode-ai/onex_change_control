# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_bare_feature_flags pre-commit hook."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from onex_change_control.scripts.check_bare_feature_flags import check_file

# ---------------------------------------------------------------------------
# Python getenv with ENABLE_ prefix
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_python_getenv_enable_prefix_is_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('x = os.getenv("ENABLE_REAL_TIME_EVENTS")\n')
    violations = check_file(str(f))
    assert len(violations) == 1
    assert "ENABLE_REAL_TIME_EVENTS" in violations[0]
    assert "bare feature flag" in violations[0]


# ---------------------------------------------------------------------------
# Python environ.get with ENABLE_ prefix
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_python_environ_get_enable_prefix_is_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('x = os.environ.get("ENABLE_PATTERN_ENFORCEMENT")\n')
    violations = check_file(str(f))
    assert len(violations) == 1
    assert "ENABLE_PATTERN_ENFORCEMENT" in violations[0]


# ---------------------------------------------------------------------------
# Python environ bracket with ENABLE_ prefix
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_python_environ_bracket_enable_prefix_is_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('x = os.environ["ENABLE_FOO"]\n')
    violations = check_file(str(f))
    assert len(violations) == 1
    assert "ENABLE_FOO" in violations[0]


# ---------------------------------------------------------------------------
# TypeScript: process.env.ENABLE_*
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ts_process_env_enable_prefix_is_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.ts"
    f.write_text("const x = process.env.ENABLE_KAFKA;\n")
    violations = check_file(str(f))
    assert len(violations) == 1
    assert "ENABLE_KAFKA" in violations[0]


# ---------------------------------------------------------------------------
# *_ENABLED suffix
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_python_enabled_suffix_is_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('x = os.getenv("KAFKA_ENABLED")\n')
    violations = check_file(str(f))
    assert len(violations) == 1
    assert "KAFKA_ENABLED" in violations[0]


@pytest.mark.unit
def test_ts_enabled_suffix_is_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.ts"
    f.write_text("const x = process.env.FEATURE_ENABLED;\n")
    violations = check_file(str(f))
    assert len(violations) == 1
    assert "FEATURE_ENABLED" in violations[0]


# ---------------------------------------------------------------------------
# Approved basenames — never flagged
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_approved_basename_feature_flag_resolver_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "feature_flag_resolver.py"
    f.write_text('x = os.getenv("ENABLE_FOO")\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_approved_basename_contract_yaml_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "contract.yaml"
    f.write_text('env: "ENABLE_FOO"\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_approved_basename_self_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "check_bare_feature_flags.py"
    f.write_text('PYTHON_ENABLE_PATTERN = re.compile(r"""os.getenv("ENABLE_FOO")""")\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_approved_basename_model_contract_feature_flag_no_violation(
    tmp_path: Path,
) -> None:
    f = tmp_path / "model_contract_feature_flag.py"
    f.write_text('x = os.getenv("ENABLE_FOO")\n')
    violations = check_file(str(f))
    assert violations == []


# ---------------------------------------------------------------------------
# Test files — never flagged
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_test_file_path_no_violation(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    f = tests_dir / "test_flags.py"
    f.write_text('x = os.getenv("ENABLE_FOO")\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_relative_tests_path_no_violation(tmp_path: Path) -> None:
    """Pre-commit passes relative paths like tests/unit/..."""
    tests_dir = tmp_path / "tests" / "unit"
    tests_dir.mkdir(parents=True)
    f = tests_dir / "test_flags.py"
    f.write_text('x = os.getenv("ENABLE_FOO")\n')
    violations = check_file(f"tests/unit/{f.name}")
    assert violations == []


@pytest.mark.unit
def test_dunder_tests_path_no_violation(tmp_path: Path) -> None:
    """TypeScript __tests__ directories are also exempt."""
    tests_dir = tmp_path / "__tests__"
    tests_dir.mkdir()
    f = tests_dir / "index.test.ts"
    f.write_text("const x = process.env.ENABLE_FOO;\n")
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_test_basename_no_violation(tmp_path: Path) -> None:
    """Files named test_* are exempt regardless of directory."""
    f = tmp_path / "test_feature_flags.py"
    f.write_text('x = os.getenv("ENABLE_FOO")\n')
    violations = check_file(str(f))
    assert violations == []


# ---------------------------------------------------------------------------
# Approved path patterns
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_capabilities_path_no_violation(tmp_path: Path) -> None:
    caps_dir = tmp_path / "capabilities"
    caps_dir.mkdir()
    f = caps_dir / "extractor.py"
    f.write_text('x = os.getenv("ENABLE_FOO")\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_config_discovery_path_no_violation(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config_discovery"
    cfg_dir.mkdir()
    f = cfg_dir / "scanner.py"
    f.write_text('x = os.getenv("ENABLE_FOO")\n')
    violations = check_file(str(f))
    assert violations == []


# ---------------------------------------------------------------------------
# Comments — never flagged
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_python_comment_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('# x = os.getenv("ENABLE_FOO")\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_ts_comment_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.ts"
    f.write_text("// const x = process.env.ENABLE_FOO;\n")
    violations = check_file(str(f))
    assert violations == []


# ---------------------------------------------------------------------------
# Exemption marker
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_exemption_marker_with_reason_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('x = os.getenv("ENABLE_FOO")  # ONEX_FLAG_EXEMPT: legacy bootstrap\n')
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_ts_exemption_marker_with_reason_no_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.ts"
    f.write_text("const x = process.env.ENABLE_FOO; // ONEX_FLAG_EXEMPT: legacy\n")
    violations = check_file(str(f))
    assert violations == []


@pytest.mark.unit
def test_exemption_marker_without_reason_is_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('x = os.getenv("ENABLE_FOO")  # ONEX_FLAG_EXEMPT:\n')
    violations = check_file(str(f))
    assert len(violations) == 1
    assert "ONEX_FLAG_EXEMPT" in violations[0]


@pytest.mark.unit
def test_exemption_marker_empty_reason_is_violation(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('x = os.getenv("ENABLE_FOO")  # ONEX_FLAG_EXEMPT:   \n')
    violations = check_file(str(f))
    assert len(violations) == 1


# ---------------------------------------------------------------------------
# Summary output
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_summary_includes_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() prints summary with violation and exemption counts."""
    import sys

    from onex_change_control.scripts.check_bare_feature_flags import main

    f1 = tmp_path / "bad.py"
    f1.write_text('x = os.getenv("ENABLE_FOO")\n')
    f2 = tmp_path / "ok.py"
    f2.write_text('x = os.getenv("ENABLE_BAR")  # ONEX_FLAG_EXEMPT: legacy bootstrap\n')

    orig = sys.argv
    sys.argv = ["check-bare-feature-flags", str(f1), str(f2)]
    try:
        rc = main()
    finally:
        sys.argv = orig

    assert rc == 1
    captured = capsys.readouterr().out
    assert "1 violation" in captured
    assert "1 exemption" in captured
    assert "legacy bootstrap" in captured


# ---------------------------------------------------------------------------
# Clean file — no violations
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_clean_file_no_violations(tmp_path: Path) -> None:
    f = tmp_path / "service.py"
    f.write_text('import os\nx = os.getenv("DATABASE_URL")\n')
    violations = check_file(str(f))
    assert violations == []
