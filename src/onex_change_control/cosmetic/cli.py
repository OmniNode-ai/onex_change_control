# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""CLI entry point for the cosmetic linter.

Provides ``check`` and ``fix`` subcommands with ``--select``, ``--spec``,
and ``-v`` flags.  Exit 0 when no violations remain, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from onex_change_control.cosmetic.config import load_spec

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class Violation:
    """A single cosmetic-lint violation."""

    check: str
    path: str
    line: int
    message: str
    fixable: bool

    def format(self) -> str:
        """Return a human-readable, ruff-style representation."""
        fixable_tag = " [fixable]" if self.fixable else ""
        return f"{self.path}:{self.line}: [{self.check}] {self.message}{fixable_tag}"


# All known check names.
ALL_CHECKS: list[str] = [
    "spdx",
    "pyproject",
    "precommit",
    "readme",
    "github",
]


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add flags shared by both check and fix subcommands."""
    parser.add_argument(
        "--spec",
        type=Path,
        default=None,
        help="Path to a custom spec YAML (default: embedded spec.yaml).",
    )
    parser.add_argument(
        "--select",
        type=str,
        default=None,
        help="Comma-separated list of checks to run (default: all).",
    )
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        default=Path(),
        help="Directory to lint (default: cwd).",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cosmetic-lint",
        description="Cross-repo cosmetic linter for OmniNode platform standards.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose output.",
    )

    subparsers = parser.add_subparsers(dest="command")

    check_parser = subparsers.add_parser("check", help="Report violations.")
    _add_common_args(check_parser)

    fix_parser = subparsers.add_parser(
        "fix", help="Auto-fix violations where possible."
    )
    _add_common_args(fix_parser)

    return parser


def _resolve_selected(raw: str | None) -> list[str]:
    """Return the list of check names to run."""
    if raw is None:
        return list(ALL_CHECKS)
    names = [n.strip() for n in raw.split(",") if n.strip()]
    unknown = [n for n in names if n not in ALL_CHECKS]
    if unknown:
        msg = f"Unknown check(s): {', '.join(unknown)}"
        raise SystemExit(msg)
    return names


def _run_checks(
    target: Path,
    spec: dict[str, Any],
    selected: list[str],
    *,
    fix: bool,
    verbose: bool,
) -> list[Violation]:
    """Dispatch to individual check modules."""
    violations: list[Violation] = []

    if "spdx" in selected:
        from onex_change_control.cosmetic.checks.spdx import (
            run_check as spdx_check,
        )

        violations.extend(spdx_check(target, spec, fix=fix))

    if "pyproject" in selected:
        from onex_change_control.cosmetic.checks.pyproject import (
            run_check as pyproject_check,
        )

        violations.extend(pyproject_check(target, spec, fix=fix))

    if "precommit" in selected:
        from onex_change_control.cosmetic.checks.precommit import (
            run_check as precommit_check,
        )

        violations.extend(precommit_check(target, spec, fix=fix))

    if "readme" in selected:
        from onex_change_control.cosmetic.checks.readme import (
            run_check as readme_check,
        )

        violations.extend(readme_check(target, spec, fix=fix))

    if "github" in selected:
        from onex_change_control.cosmetic.checks.github import (
            run_check as github_check,
        )

        violations.extend(github_check(target, spec, fix=fix))

    if verbose:
        print(  # noqa: T201
            f"cosmetic-lint: ran {len(selected)} check(s), "
            f"found {len(violations)} violation(s)",
            file=sys.stderr,
        )

    return violations


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point for ``cosmetic-lint``."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        raise SystemExit(0)

    selected = _resolve_selected(args.select)
    fix_mode = args.command == "fix"

    spec = load_spec(args.spec)

    violations = _run_checks(
        target=args.target,
        spec=spec,
        selected=selected,
        fix=fix_mode,
        verbose=args.verbose,
    )

    # Print violations to stderr
    for v in violations:
        print(v.format(), file=sys.stderr)  # noqa: T201

    remaining = [v for v in violations if not (fix_mode and v.fixable)]

    if remaining:
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
