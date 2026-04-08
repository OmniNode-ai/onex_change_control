# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_env_var_contract pre-commit hook."""

from __future__ import annotations

import typing

import pytest

from onex_change_control.scripts.check_env_var_contract import (
    EnvContract,
    scan_file,
)

if typing.TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def contract() -> EnvContract:
    """Standard test contract."""
    return EnvContract(
        allowed=frozenset(
            {"POSTGRES_PASSWORD", "KAFKA_BOOTSTRAP_SERVERS", "GITHUB_TOKEN"}
        ),
        blocked={"ANTHROPIC_API_KEY": "Claude Code uses OAuth"},
    )


@pytest.fixture
def tmp_py(tmp_path: Path) -> Path:
    return tmp_path / "test_file.py"


# -----------------------------------------------------------------------
# Blocked var detection
# -----------------------------------------------------------------------


class TestBlockedVars:
    """Blocked env vars must always be caught."""

    def test_bracket_access_blocked(self, tmp_py: Path, contract: EnvContract) -> None:
        tmp_py.write_text('key = os.environ["ANTHROPIC_API_KEY"]\n')
        result = scan_file(str(tmp_py), contract)
        assert len(result.violations) == 1
        assert result.violations[0].kind == "blocked"
        assert result.violations[0].var_name == "ANTHROPIC_API_KEY"

    def test_getenv_no_default_blocked(
        self, tmp_py: Path, contract: EnvContract
    ) -> None:
        tmp_py.write_text('key = os.getenv("ANTHROPIC_API_KEY")\n')
        result = scan_file(str(tmp_py), contract)
        assert len(result.violations) == 1
        assert result.violations[0].kind == "blocked"

    def test_environ_get_no_default_blocked(
        self, tmp_py: Path, contract: EnvContract
    ) -> None:
        tmp_py.write_text('key = os.environ.get("ANTHROPIC_API_KEY")\n')
        result = scan_file(str(tmp_py), contract)
        assert len(result.violations) == 1
        assert result.violations[0].kind == "blocked"

    def test_markdown_required_table_blocked(
        self, tmp_path: Path, contract: EnvContract
    ) -> None:
        md = tmp_path / "README.md"
        md.write_text("| `ANTHROPIC_API_KEY` | API key | Required for headless |\n")
        result = scan_file(str(md), contract)
        assert len(result.violations) == 1
        assert result.violations[0].kind == "blocked"


# -----------------------------------------------------------------------
# Allowed var detection — should NOT trigger
# -----------------------------------------------------------------------


class TestAllowedVars:
    """Allowed required vars should pass cleanly."""

    def test_allowed_bracket_access(self, tmp_py: Path, contract: EnvContract) -> None:
        tmp_py.write_text('pw = os.environ["POSTGRES_PASSWORD"]\n')
        result = scan_file(str(tmp_py), contract)
        assert len(result.violations) == 0

    def test_allowed_getenv_no_default(
        self, tmp_py: Path, contract: EnvContract
    ) -> None:
        tmp_py.write_text('servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS")\n')
        result = scan_file(str(tmp_py), contract)
        assert len(result.violations) == 0

    def test_allowed_markdown_required(
        self, tmp_path: Path, contract: EnvContract
    ) -> None:
        md = tmp_path / "README.md"
        md.write_text("| `GITHUB_TOKEN` | GH auth | Required for PRs |\n")
        result = scan_file(str(md), contract)
        assert len(result.violations) == 0


# -----------------------------------------------------------------------
# Unlisted var detection — should warn
# -----------------------------------------------------------------------


class TestUnlistedVars:
    """Vars not in allowlist or blocklist should trigger unlisted warning."""

    def test_unlisted_bracket_access(self, tmp_py: Path, contract: EnvContract) -> None:
        tmp_py.write_text('val = os.environ["MY_SECRET_THING"]\n')
        result = scan_file(str(tmp_py), contract)
        assert len(result.violations) == 1
        assert result.violations[0].kind == "unlisted"
        assert result.violations[0].var_name == "MY_SECRET_THING"

    def test_unlisted_getenv_no_default(
        self, tmp_py: Path, contract: EnvContract
    ) -> None:
        tmp_py.write_text('val = os.getenv("UNKNOWN_VAR")\n')
        result = scan_file(str(tmp_py), contract)
        assert len(result.violations) == 1
        assert result.violations[0].kind == "unlisted"


# -----------------------------------------------------------------------
# Optional reads — should NOT trigger
# -----------------------------------------------------------------------


class TestOptionalReads:
    """Env var reads with defaults are optional and should not trigger."""

    def test_getenv_with_default(self, tmp_py: Path, contract: EnvContract) -> None:
        tmp_py.write_text('val = os.getenv("ANTHROPIC_API_KEY", "")\n')
        result = scan_file(str(tmp_py), contract)
        assert len(result.violations) == 0

    def test_environ_get_with_default(
        self, tmp_py: Path, contract: EnvContract
    ) -> None:
        tmp_py.write_text('val = os.environ.get("UNKNOWN_VAR", "fallback")\n')
        result = scan_file(str(tmp_py), contract)
        assert len(result.violations) == 0


# -----------------------------------------------------------------------
# Exemptions
# -----------------------------------------------------------------------


class TestExemptions:
    """Exemption markers and path-based exemptions."""

    def test_exempt_marker(self, tmp_py: Path, contract: EnvContract) -> None:
        tmp_py.write_text(
            'key = os.environ["ANTHROPIC_API_KEY"]'
            "  # env-contract-ok: direct API usage in demo\n"
        )
        result = scan_file(str(tmp_py), contract)
        assert len(result.violations) == 0

    def test_comment_lines_skipped(self, tmp_py: Path, contract: EnvContract) -> None:
        tmp_py.write_text('# key = os.environ["ANTHROPIC_API_KEY"]\n')
        result = scan_file(str(tmp_py), contract)
        assert len(result.violations) == 0

    def test_env_example_exempt(self, tmp_path: Path, contract: EnvContract) -> None:
        env = tmp_path / ".env.example"
        env.write_text("ANTHROPIC_API_KEY=sk-ant-...\n")
        result = scan_file(str(env), contract)
        assert len(result.violations) == 0

    def test_log_sanitizer_exempt(self, tmp_path: Path, contract: EnvContract) -> None:
        f = tmp_path / "log_sanitizer.py"
        f.write_text("r'(OPENAI_API_KEY|ANTHROPIC_API_KEY)'\n")
        result = scan_file(str(f), contract)
        assert len(result.violations) == 0

    def test_validate_env_exempt(self, tmp_path: Path, contract: EnvContract) -> None:
        f = tmp_path / "validate_env.py"
        f.write_text('"ANTHROPIC_API_KEY", "GEMINI_API_KEY",\n')
        result = scan_file(str(f), contract)
        assert len(result.violations) == 0


# -----------------------------------------------------------------------
# Contract loading (fallback)
# -----------------------------------------------------------------------


class TestContractLoading:
    """Test contract loading fallback behavior."""

    def test_missing_contract_uses_hardcoded_blocklist(self, tmp_py: Path) -> None:
        """With no contract file, ANTHROPIC_API_KEY is still blocked."""
        from onex_change_control.scripts.check_env_var_contract import (
            load_contract,
        )

        contract = load_contract(tmp_py.parent / "nonexistent.yaml")
        assert "ANTHROPIC_API_KEY" in contract.blocked
