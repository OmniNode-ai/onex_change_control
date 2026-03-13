# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Tests for check_db_boundary validator script."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest
import yaml

from onex_change_control.scripts.check_db_boundary import (
    check_file_for_cross_service_env,
    check_file_for_cross_service_imports,
    validate_exceptions_yaml,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_py(tmp_path: Path, code: str) -> Path:
    """Write a Python file and return its path."""
    p = tmp_path / "test_file.py"
    p.write_text(code, encoding="utf-8")
    return p


@pytest.mark.unit
class TestCrossServiceEnvDetection:
    """Tests for cross-service DB env var detection."""

    def test_clean_file_own_service_env(
        self,
        tmp_path: Path,
    ) -> None:
        """Own service env var produces 0 violations."""
        p = _write_py(
            tmp_path,
            'import os\ndb = os.getenv("OMNIINTELLIGENCE_DB_URL")\n',
        )
        violations = check_file_for_cross_service_env(
            p,
            "omniintelligence",
        )
        assert len(violations) == 0

    def test_cross_service_env_detected(
        self,
        tmp_path: Path,
    ) -> None:
        """Cross-service env var produces 1 violation."""
        p = _write_py(
            tmp_path,
            'import os\ndb = os.getenv("OMNIMEMORY_DB_URL")\n',
        )
        violations = check_file_for_cross_service_env(
            p,
            "omniintelligence",
        )
        assert len(violations) == 1
        assert "OMNIMEMORY_DB_URL" in violations[0].message

    def test_shared_env_var_not_flagged(
        self,
        tmp_path: Path,
    ) -> None:
        """Shared env vars (POSTGRES_PASSWORD) produce 0 violations."""
        p = _write_py(
            tmp_path,
            'import os\npw = os.getenv("POSTGRES_PASSWORD")\n',
        )
        violations = check_file_for_cross_service_env(
            p,
            "omniintelligence",
        )
        assert len(violations) == 0

    def test_own_service_env_var_not_flagged(
        self,
        tmp_path: Path,
    ) -> None:
        """Own service DB URL produces 0 violations."""
        p = _write_py(
            tmp_path,
            'import os\ndb = os.getenv("OMNIBASE_INFRA_DB_URL")\n',
        )
        violations = check_file_for_cross_service_env(
            p,
            "omnibase_infra",
        )
        assert len(violations) == 0


@pytest.mark.unit
class TestCrossServiceImportDetection:
    """Tests for cross-service import detection."""

    def test_clean_import(self, tmp_path: Path) -> None:
        """Same-service import produces 0 violations."""
        p = _write_py(
            tmp_path,
            "from omnibase_infra.runtime import get_session\n",
        )
        violations = check_file_for_cross_service_imports(
            p,
            "omnibase_infra",
        )
        assert len(violations) == 0

    def test_cross_service_model_import(
        self,
        tmp_path: Path,
    ) -> None:
        """Cross-service model import produces 1 violation."""
        p = _write_py(
            tmp_path,
            "from omnimemory.models import DocumentModel\n",
        )
        violations = check_file_for_cross_service_imports(
            p,
            "omniintelligence",
        )
        assert len(violations) == 1

    def test_shared_library_import_not_flagged(
        self,
        tmp_path: Path,
    ) -> None:
        """Shared library (omnibase_core) imports produce 0 violations.

        omnibase_core, omnibase_spi, and onex_change_control are shared
        packages with no DB boundary -- importing from them is always allowed.
        """
        p = _write_py(
            tmp_path,
            "from omnibase_core.models.contracts import ModelContract\n",
        )
        violations = check_file_for_cross_service_imports(
            p,
            "omniintelligence",
        )
        assert len(violations) == 0

    def test_type_checking_import_exempted(
        self,
        tmp_path: Path,
    ) -> None:
        """TYPE_CHECKING imports produce 0 violations."""
        code = (
            "from __future__ import annotations\n"
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from omnimemory.models import DocumentModel\n"
        )
        p = _write_py(tmp_path, code)
        violations = check_file_for_cross_service_imports(
            p,
            "omniintelligence",
        )
        assert len(violations) == 0


@pytest.mark.unit
class TestExceptionsYamlValidation:
    """Tests for exceptions YAML validation."""

    def test_valid_registry(self, tmp_path: Path) -> None:
        """Valid registry produces 0 violations."""
        p = tmp_path / "exceptions.yaml"
        p.write_text("exceptions: []\n", encoding="utf-8")
        violations = validate_exceptions_yaml(p)
        assert len(violations) == 0

    def test_expired_review_by(self, tmp_path: Path) -> None:
        """Expired review_by produces 1 violation."""
        data = {
            "exceptions": [
                {
                    "repo": "omnimemory",
                    "file": "src/test.py",
                    "usage": "test",
                    "reason_category": "TEST_ONLY",
                    "justification": "test",
                    "owner": "jonah",
                    "approved_by": "jonah",
                    "review_by": "2020-01",
                    "status": "APPROVED",
                },
            ],
        }
        p = tmp_path / "exceptions.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        violations = validate_exceptions_yaml(p)
        assert len(violations) >= 1
        assert any("expired" in v.message.lower() for v in violations)

    def test_missing_required_field(
        self,
        tmp_path: Path,
    ) -> None:
        """Missing required field produces violations."""
        data = {
            "exceptions": [
                {
                    "repo": "omnimemory",
                    # missing most required fields
                },
            ],
        }
        p = tmp_path / "exceptions.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        violations = validate_exceptions_yaml(p)
        assert len(violations) >= 1


@pytest.mark.unit
class TestCheckDbBoundaryCli:
    """Tests for check-db-boundary CLI entry point."""

    def test_help_exits_zero(self) -> None:
        """check-db-boundary --help exits 0."""
        result = subprocess.run(
            ["uv", "run", "check-db-boundary", "--help"],  # noqa: S607
            capture_output=True,
            text=True,
            cwd="/Volumes/PRO-G40/Code/omni_worktrees/OMN-4815/onex_change_control",
            check=False,
        )
        assert result.returncode == 0
