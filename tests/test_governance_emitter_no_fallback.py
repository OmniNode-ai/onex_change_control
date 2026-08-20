# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Tests for governance_emitter.py — no-localhost-fallback behavior (OMN-10737)."""

from __future__ import annotations

import pytest

from onex_change_control.kafka.governance_emitter import (
    _get_bootstrap_servers,
    emit_governance_check_completed,
)


@pytest.mark.unit
def test_get_bootstrap_servers_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    assert _get_bootstrap_servers() is None


@pytest.mark.unit
def test_get_bootstrap_servers_returns_env_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "192.168.86.201:19092")
    assert _get_bootstrap_servers() == "192.168.86.201:19092"


@pytest.mark.unit
def test_emit_skips_when_kafka_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emission must be a no-op (not raise) when KAFKA_BOOTSTRAP_SERVERS is unset."""
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    # Should complete without raising — best-effort emission
    emit_governance_check_completed(
        check_type="test",
        target="src/",
        passed=True,
        violation_count=0,
    )


@pytest.mark.unit
def test_no_localhost_default_in_source() -> None:
    """Validator: no os.environ.get(..., 'localhost...') in src/."""
    import re
    from pathlib import Path

    pattern = re.compile(r'os\.environ\.get\([^)]*,\s*"(localhost|http://localhost)')
    src_root = Path(__file__).resolve().parent.parent / "src"
    violations = []
    for py_file in sorted(src_root.rglob("*.py")):
        if "test" in py_file.parts:
            continue
        for lineno, line in enumerate(py_file.read_text().splitlines(), start=1):
            if pattern.search(line):
                violations.append(f"{py_file}:{lineno}: {line.strip()}")

    assert violations == [], "localhost fallbacks found in src/:\n" + "\n".join(
        violations
    )
