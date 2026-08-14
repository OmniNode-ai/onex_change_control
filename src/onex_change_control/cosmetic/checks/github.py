# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""GitHub templates and workflow file extension check.

Verifies required templates exist in ``.github/`` and that workflow files
use the canonical extension (``.yml``).  Fix mode renames ``.yaml`` workflow
files to ``.yml``.  Template violations are NOT fixable (templates need
human content).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from onex_change_control.cosmetic.cli import Violation
from onex_change_control.cosmetic.config import load_spec

if TYPE_CHECKING:
    from pathlib import Path


def run_check(
    target: Path,
    spec: dict[str, Any] | None = None,
    *,
    fix: bool = False,
) -> list[Violation]:
    """Run the GitHub templates/workflows check (and optional fix) on *target*.

    Args:
        target: Root directory to scan.
        spec: Parsed cosmetic spec dict.  If *None*, loads the default.
        fix: When *True*, rename ``.yaml`` workflow files to ``.yml``.

    Returns:
        List of violations found.

    """
    if spec is None:
        spec = load_spec()

    github_dir = target / ".github"
    if not github_dir.is_dir():
        return []

    spec_github: dict[str, Any] = spec.get("github", {})
    violations: list[Violation] = []

    # Check required templates.
    for template in spec_github.get("required_templates", []):
        template_path = github_dir / template
        if not template_path.exists():
            violations.append(
                Violation(
                    check="github",
                    path=f".github/{template}",
                    line=0,
                    message=f"Missing required template: {template}",
                    fixable=False,
                )
            )

    # Check workflow file extensions.
    expected_ext = spec_github.get("workflow_extension", ".yml")
    wrong_ext = ".yaml" if expected_ext == ".yml" else ".yml"
    workflows_dir = github_dir / "workflows"
    if workflows_dir.is_dir():
        for wf in sorted(workflows_dir.iterdir()):
            if wf.suffix == wrong_ext:
                violations.append(
                    Violation(
                        check="github",
                        path=str(wf.relative_to(target)),
                        line=0,
                        message=(
                            f"Workflow uses {wrong_ext} extension, "
                            f"expected {expected_ext}"
                        ),
                        fixable=True,
                    )
                )
                if fix:
                    new_path = wf.with_suffix(expected_ext)
                    wf.rename(new_path)

    return violations
