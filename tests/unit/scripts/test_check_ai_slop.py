# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest


def _load_checker() -> Any:
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "validation" / "check_ai_slop.py"
    spec = importlib.util.spec_from_file_location("check_ai_slop", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_file_reports_docstring_errors_and_warnings(tmp_path: Path) -> None:
    checker = _load_checker()
    source_file = tmp_path / "sloppy.py"
    source_file.write_text(
        '''
def eager_helper() -> None:
    """Absolutely! Here's a helper."""


def rest_helper() -> None:
    """
    Helper function.

    :param value: ignored
    """


def boilerplate_helper() -> None:
    """This function handles the specified input."""
''',
        encoding="utf-8",
    )

    violations = checker.check_file(source_file)
    found = {(violation.check, violation.severity) for violation in violations}

    assert (checker.CHECK_SYCOPHANCY, checker.SEVERITY_ERROR) in found
    assert (checker.CHECK_REST_DOCSTRING, checker.SEVERITY_ERROR) in found
    assert (checker.CHECK_BOILERPLATE_DOCSTRING, checker.SEVERITY_WARNING) in found


def test_suppression_marker_ignores_next_docstring_violation(tmp_path: Path) -> None:
    checker = _load_checker()
    source_file = tmp_path / "suppressed.py"
    source_file.write_text(
        '''
# ai-slop-ok
def preserved_phrase() -> None:
    """Absolutely! Here's a preserved fixture phrase."""
''',
        encoding="utf-8",
    )

    assert checker.check_file(source_file) == []


def test_markdown_step_narration_skips_fenced_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _load_checker()
    markdown_file = tmp_path / "notes.md"
    markdown_file.write_text(
        """
# Notes

## Step 1: Replace boilerplate prose

```python
# Step 2: This is a legitimate code comment.
```
""",
        encoding="utf-8",
    )

    exit_code = checker.main(["--report", "--json", str(markdown_file)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert [violation["check"] for violation in output] == [
        checker.CHECK_STEP_NARRATION
    ]
    assert "Step 1" in output[0]["message"]


def test_python_step_comments_are_allowed(tmp_path: Path) -> None:
    checker = _load_checker()
    source_file = tmp_path / "ordered_steps.py"
    source_file.write_text(
        """
def deploy() -> None:
    # Step 1: validate local state.
    # Step 2: publish evidence.
    return None
""",
        encoding="utf-8",
    )

    assert checker.check_file(source_file) == []


def test_strict_mode_blocks_warnings(tmp_path: Path) -> None:
    checker = _load_checker()
    source_file = tmp_path / "warning_only.py"
    source_file.write_text(
        '''
def boilerplate_helper() -> None:
    """This function handles the specified input."""
''',
        encoding="utf-8",
    )

    assert checker.main(["--strict", str(source_file)]) == 2
    assert checker.main([str(source_file)]) == 0
