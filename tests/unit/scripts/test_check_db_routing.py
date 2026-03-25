# SPDX-License-Identifier: MIT
"""Tests for check_db_routing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
def test_detect_wrong_database_default(tmp_path: Path) -> None:
    """Docker-compose with wrong DATABASE_URL default should be flagged."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("""
services:
  intelligence-reducer:
    environment:
      DATABASE_URL: ${DATABASE_URL:-postgresql://postgres:pass@host:5432/omnibase_infra}
""")
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
rules:
  - service_pattern: "intelligence"
    expected_database: omniintelligence
    connection_env: OMNIINTELLIGENCE_DB_URL
""")

    from onex_change_control.scripts.check_db_routing import check_routing

    result = check_routing(compose, rules)
    assert not result.ok
    assert any(
        "omnibase_infra" in f.detail and "omniintelligence" in f.detail
        for f in result.findings
    )


@pytest.mark.unit
def test_correct_database_default_passes(tmp_path: Path) -> None:
    """Docker-compose with correct DATABASE_URL default should pass."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("""
services:
  intelligence-reducer:
    environment:
      DATABASE_URL: ${DATABASE_URL:-postgresql://postgres:pass@host:5432/omniintelligence}
""")
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
rules:
  - service_pattern: "intelligence"
    expected_database: omniintelligence
    connection_env: OMNIINTELLIGENCE_DB_URL
""")

    from onex_change_control.scripts.check_db_routing import check_routing

    result = check_routing(compose, rules)
    assert result.ok


@pytest.mark.unit
def test_service_without_database_url_skipped(tmp_path: Path) -> None:
    """Services without DATABASE_URL should be silently skipped."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("""
services:
  redis:
    image: redis:7
""")
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
rules:
  - service_pattern: "intelligence"
    expected_database: omniintelligence
    connection_env: OMNIINTELLIGENCE_DB_URL
""")

    from onex_change_control.scripts.check_db_routing import check_routing

    result = check_routing(compose, rules)
    assert result.ok
    assert len(result.findings) == 0
