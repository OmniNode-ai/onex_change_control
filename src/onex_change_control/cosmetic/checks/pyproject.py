# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""pyproject.toml field check and fix.

Validates author, license format, requires-python, classifiers, URL keys,
and ruff configuration against the canonical spec.  Fix mode performs a
structural rewrite using ``tomli_w`` (comments are not preserved).
"""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import tomli_w

from onex_change_control.cosmetic.cli import Violation
from onex_change_control.cosmetic.config import load_spec


def _check_author(
    project: dict[str, Any], spec_pyproject: dict[str, Any]
) -> list[Violation]:
    """Check that authors list contains the canonical author."""
    violations: list[Violation] = []
    authors = project.get("authors", [])
    expected = spec_pyproject["author"]
    if not any(
        a.get("name") == expected["name"] and a.get("email") == expected["email"]
        for a in authors
    ):
        violations.append(
            Violation(
                check="pyproject",
                path="pyproject.toml",
                line=0,
                message=(
                    f'Expected author {{name = "{expected["name"]}", '
                    f'email = "{expected["email"]}"}}'
                ),
                fixable=True,
            )
        )
    return violations


def _check_license(
    project: dict[str, Any], spec_pyproject: dict[str, Any]
) -> list[Violation]:
    """Check license is in table format ``{text = 'MIT'}``."""
    violations: list[Violation] = []
    lic = project.get("license")
    expected_text = spec_pyproject.get("license_text", "MIT")
    expected_format = spec_pyproject.get("license_format", "table")

    if lic is None:
        violations.append(
            Violation(
                check="pyproject",
                path="pyproject.toml",
                line=0,
                message="Missing license field",
                fixable=True,
            )
        )
    elif expected_format == "table":
        if isinstance(lic, str):
            violations.append(
                Violation(
                    check="pyproject",
                    path="pyproject.toml",
                    line=0,
                    message=(
                        f"License should be table format "
                        f'{{text = "{expected_text}"}}, got bare string "{lic}"'
                    ),
                    fixable=True,
                )
            )
        elif isinstance(lic, dict) and lic.get("text") != expected_text:
            violations.append(
                Violation(
                    check="pyproject",
                    path="pyproject.toml",
                    line=0,
                    message=(
                        f'Expected license text "{expected_text}", '
                        f'got "{lic.get("text")}"'
                    ),
                    fixable=True,
                )
            )
    return violations


def _check_requires_python(
    project: dict[str, Any], spec_pyproject: dict[str, Any]
) -> list[Violation]:
    """Check requires-python matches spec."""
    violations: list[Violation] = []
    expected = spec_pyproject.get("requires_python", ">=3.12")
    actual = project.get("requires-python", "")
    if actual != expected:
        violations.append(
            Violation(
                check="pyproject",
                path="pyproject.toml",
                line=0,
                message=f'Expected requires-python "{expected}", got "{actual}"',
                fixable=True,
            )
        )
    return violations


def _check_classifiers(
    project: dict[str, Any], spec_pyproject: dict[str, Any]
) -> list[Violation]:
    """Check required classifiers are present (additive — extra ones are OK)."""
    violations: list[Violation] = []
    actual = set(project.get("classifiers", []))
    required = spec_pyproject.get("classifiers", {}).get("required", [])
    for classifier in required:
        if classifier not in actual:
            violations.append(
                Violation(
                    check="pyproject",
                    path="pyproject.toml",
                    line=0,
                    message=f'Missing required classifier: "{classifier}"',
                    fixable=True,
                )
            )
    return violations


def _check_urls(
    project: dict[str, Any], spec_pyproject: dict[str, Any]
) -> list[Violation]:
    """Check required URL keys are present (additive)."""
    violations: list[Violation] = []
    urls = project.get("urls", {})
    url_keys = spec_pyproject.get("url_keys", {})
    for key in url_keys.get("required", []):
        if key not in urls:
            violations.append(
                Violation(
                    check="pyproject",
                    path="pyproject.toml",
                    line=0,
                    message=f'Missing required URL key: "{key}"',
                    fixable=False,
                )
            )
    return violations


def _check_ruff(
    data: dict[str, Any], spec_pyproject: dict[str, Any]
) -> list[Violation]:
    """Check ruff configuration exists with correct target-version."""
    violations: list[Violation] = []
    ruff_spec = spec_pyproject.get("ruff", {})
    if not ruff_spec.get("required", False):
        return violations

    ruff = data.get("tool", {}).get("ruff")
    if ruff is None:
        violations.append(
            Violation(
                check="pyproject",
                path="pyproject.toml",
                line=0,
                message="Missing [tool.ruff] section",
                fixable=False,
            )
        )
        return violations

    expected_target = ruff_spec.get("target_version", "py312")
    actual_target = ruff.get("target-version", "")
    if actual_target != expected_target:
        violations.append(
            Violation(
                check="pyproject",
                path="pyproject.toml",
                line=0,
                message=(
                    f'Expected ruff target-version "{expected_target}", '
                    f'got "{actual_target}"'
                ),
                fixable=False,
            )
        )
    return violations


def _apply_fixes(
    pyproject_path: Path,
    data: dict[str, Any],
    spec_pyproject: dict[str, Any],
) -> None:
    """Apply fixable corrections and rewrite pyproject.toml."""
    project = data.setdefault("project", {})

    # Fix author
    expected_author = spec_pyproject["author"]
    project["authors"] = [
        {"name": expected_author["name"], "email": expected_author["email"]}
    ]

    # Fix license
    expected_text = spec_pyproject.get("license_text", "MIT")
    project["license"] = {"text": expected_text}

    # Fix requires-python
    expected_rp = spec_pyproject.get("requires_python", ">=3.12")
    project["requires-python"] = expected_rp

    # Fix classifiers (additive)
    existing_classifiers = list(project.get("classifiers", []))
    required = spec_pyproject.get("classifiers", {}).get("required", [])
    for classifier in required:
        if classifier not in existing_classifiers:
            existing_classifiers.append(classifier)
    if existing_classifiers:
        project["classifiers"] = sorted(existing_classifiers)

    pyproject_path.write_bytes(tomli_w.dumps(data).encode("utf-8"))


def run_check(
    target: Path,
    spec: dict[str, Any] | None = None,
    *,
    fix: bool = False,
) -> list[Violation]:
    """Run the pyproject.toml check (and optional fix) on *target*.

    Args:
        target: Root directory to scan.
        spec: Parsed cosmetic spec dict.  If *None*, loads the default.
        fix: When *True*, rewrite pyproject.toml with canonical values.

    Returns:
        List of violations found.

    """
    if spec is None:
        spec = load_spec()

    pyproject_path = target / "pyproject.toml"
    if not pyproject_path.exists():
        return []

    raw = pyproject_path.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    project: dict[str, Any] = data.get("project", {})
    spec_pyproject: dict[str, Any] = spec.get("pyproject", {})

    violations: list[Violation] = []
    violations.extend(_check_author(project, spec_pyproject))
    violations.extend(_check_license(project, spec_pyproject))
    violations.extend(_check_requires_python(project, spec_pyproject))
    violations.extend(_check_classifiers(project, spec_pyproject))
    violations.extend(_check_urls(project, spec_pyproject))
    violations.extend(_check_ruff(data, spec_pyproject))

    if fix and any(v.fixable for v in violations):
        _apply_fixes(pyproject_path, data, spec_pyproject)

    return violations
