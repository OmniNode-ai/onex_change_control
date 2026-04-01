# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Eval Suite Manager.

Load, validate, and version eval suites from YAML files.
Detects version changes via content hashing for reproducibility.

Related:
    - OMN-6783: Build eval suite versioning and history
    - OMN-6775: Standard eval suite
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from pathlib import Path
from pydantic import ValidationError

from onex_change_control.models.model_eval_task import ModelEvalSuite

logger = logging.getLogger(__name__)


class SuiteManager:
    """Manages eval suite loading, validation, and version tracking.

    Args:
        suites_dir: Directory containing eval suite YAML files.
    """

    def __init__(self, suites_dir: Path) -> None:
        self._suites_dir = suites_dir
        self._hash_cache: dict[str, str] = {}

    @property
    def suites_dir(self) -> Path:
        return self._suites_dir

    def load_suite(self, filename: str) -> ModelEvalSuite:
        """Load and validate an eval suite from a YAML file.

        Args:
            filename: Name of the YAML file (relative to suites_dir).

        Returns:
            Validated ModelEvalSuite instance.

        Raises:
            FileNotFoundError: If the suite file does not exist.
            ValueError: If the YAML is invalid or fails schema validation.
        """
        path = self._suites_dir / filename
        if not path.exists():
            msg = f"Suite file not found: {path}"
            raise FileNotFoundError(msg)

        raw = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            msg = f"Invalid YAML in {path}: {exc}"
            raise ValueError(msg) from exc

        if not isinstance(data, dict):
            msg = f"Suite file must contain a YAML mapping, got {type(data).__name__}"
            raise TypeError(msg)

        try:
            suite = ModelEvalSuite.model_validate(data)
        except ValidationError as exc:
            msg = f"Suite validation failed for {path}: {exc}"
            raise ValueError(msg) from exc

        # Cache the content hash
        self._hash_cache[filename] = self._compute_hash(raw)

        # Validate uniqueness of task IDs
        task_ids = [t.task_id for t in suite.tasks]
        if len(task_ids) != len(set(task_ids)):
            duplicates = [tid for tid in task_ids if task_ids.count(tid) > 1]
            msg = f"Duplicate task_ids in suite: {set(duplicates)}"
            raise ValueError(msg)

        logger.info(
            "Loaded suite %s v%s (%d tasks, hash=%s)",
            suite.suite_id,
            suite.version,
            len(suite.tasks),
            self._hash_cache[filename][:12],
        )
        return suite

    def list_suites(self) -> list[str]:
        """List all YAML suite files in the suites directory."""
        if not self._suites_dir.exists():
            return []
        return sorted(
            f.name for f in self._suites_dir.iterdir() if f.suffix in (".yaml", ".yml")
        )

    def get_suite_hash(self, filename: str) -> str:
        """Get the content hash for a loaded suite.

        If the suite hasn't been loaded yet, loads and hashes it.
        """
        if filename not in self._hash_cache:
            path = self._suites_dir / filename
            if not path.exists():
                msg = f"Suite file not found: {path}"
                raise FileNotFoundError(msg)
            raw = path.read_text(encoding="utf-8")
            self._hash_cache[filename] = self._compute_hash(raw)
        return self._hash_cache[filename]

    def has_changed(self, filename: str) -> bool:
        """Check if a suite file has changed since last load.

        Returns True if the file content differs from the cached hash,
        or if no cached hash exists.
        """
        path = self._suites_dir / filename
        if not path.exists():
            return False
        current_hash = self._compute_hash(
            path.read_text(encoding="utf-8"),
        )
        cached = self._hash_cache.get(filename)
        if cached is None:
            return True
        return current_hash != cached

    @staticmethod
    def _compute_hash(content: str) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


__all__: list[str] = ["SuiteManager"]
