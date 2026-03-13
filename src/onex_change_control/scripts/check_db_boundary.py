# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Check DB boundary policy compliance.

Validates that services do not cross database boundaries by detecting:
1. Cross-service DB environment variable usage
2. Direct cross-service model/repository imports
3. Exception registry validity (schema + expiry)

Scope: v1 enforces direct violation patterns only. Indirect patterns
(helper wrappers, dynamic imports, proxy modules) are out of scope.

Usage:
    uv run check-db-boundary --repo <service> --path <repo_path>
    uv run check-db-boundary --validate-all --registry <path>

Exit codes:
    0: All checks passed (or --warn-only flag is set)
    1: One or more violations found (unless --warn-only is set)
"""

import argparse
import ast
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import yaml
from colorama import Fore, Style, init
from pydantic import ValidationError

from onex_change_control.models.model_db_boundary_exception import (
    ModelDbBoundaryExceptionsRegistry,
)

# Known service prefixes for cross-service env var detection
KNOWN_SERVICE_PREFIXES: frozenset[str] = frozenset(
    {
        "OMNIINTELLIGENCE_",
        "OMNIMEMORY_",
        "OMNIBASE_INFRA_",
        "OMNIDASH_",
    },
)

# Shared env vars that do not constitute boundary violations
SHARED_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_DB",
    },
)

# Service packages that own a database -- cross-imports between these are violations.
# Shared packages (omnibase_core, omnibase_spi, onex_change_control, omniclaude) have
# no DB boundary and are always safe to import from any service.
SERVICE_PACKAGES: frozenset[str] = frozenset(
    {
        "omniintelligence",
        "omnimemory",
        "omnibase_infra",
        "omnidash",
        "omninode_infra",
    },
)

# DB-related submodules that indicate a boundary violation
DB_SUBMODULES: frozenset[str] = frozenset(
    {
        "models",
        "repositories",
    },
)

# Pattern to match service DB URL env vars
_DB_URL_PATTERN = re.compile(
    r"""["\']([A-Z_]+_DB_URL)["\']""",
)


class Violation(NamedTuple):
    """Represents a single DB boundary violation."""

    file: Path
    line: int
    category: str
    message: str


def _service_prefix(service: str) -> str:
    """Get the env var prefix for a service name.

    Examples:
        omniintelligence -> OMNIINTELLIGENCE_
        omnibase_infra -> OMNIBASE_INFRA_

    """
    return service.upper().replace("-", "_") + "_"


def check_file_for_cross_service_env(
    file_path: Path,
    service: str,
) -> list[Violation]:
    """Scan a file for cross-service DB environment variable usage.

    Detects patterns like os.getenv("OMNIMEMORY_DB_URL") when the
    current service is not omnimemory.

    Args:
        file_path: Path to the Python file to scan
        service: Current service name (e.g., "omniintelligence")

    Returns:
        List of violations found

    """
    violations: list[Violation] = []
    own_prefix = _service_prefix(service)

    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    for line_num, line in enumerate(content.splitlines(), start=1):
        for match in _DB_URL_PATTERN.finditer(line):
            env_var = match.group(1)

            # Skip shared allowlist
            if env_var in SHARED_ENV_ALLOWLIST:
                continue

            # Skip own service prefix
            if env_var.startswith(own_prefix):
                continue

            # Check if it belongs to another known service
            for prefix in KNOWN_SERVICE_PREFIXES:
                if env_var.startswith(prefix) and prefix != own_prefix:
                    violations.append(
                        Violation(
                            file=file_path,
                            line=line_num,
                            category="cross_service_env",
                            message=(
                                f"Cross-service DB env var: "
                                f"{env_var} (service={service})"
                            ),
                        ),
                    )
                    break

    return violations


def _find_type_checking_lines(tree: ast.Module) -> set[int]:
    """Find all line numbers inside TYPE_CHECKING blocks.

    Args:
        tree: Parsed AST module

    Returns:
        Set of line numbers inside TYPE_CHECKING blocks

    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute)
            and isinstance(test.value, ast.Name)
            and test.attr == "TYPE_CHECKING"
        )
        if is_tc:
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    lines.add(child.lineno)
    return lines


def _is_cross_service_db_import(
    node: ast.ImportFrom,
    service: str,
) -> bool:
    """Check if an ImportFrom node is a cross-service DB import.

    Args:
        node: AST ImportFrom node
        service: Current service name

    Returns:
        True if the import crosses a DB boundary

    """
    if node.module is None:
        return False
    parts = node.module.split(".")
    if len(parts) < 2:  # noqa: PLR2004
        return False
    pkg, submodule = parts[0], parts[1]
    return pkg in SERVICE_PACKAGES and pkg != service and submodule in DB_SUBMODULES


def check_file_for_cross_service_imports(
    file_path: Path,
    service: str,
) -> list[Violation]:
    """Scan a file for direct cross-service model/repository imports.

    Uses AST to detect import patterns like:
        from omnimemory.models import DocumentModel
        from omnimemory.repositories import DocumentRepository

    Exempts TYPE_CHECKING blocks.

    Args:
        file_path: Path to the Python file to scan
        service: Current service name (e.g., "omniintelligence")

    Returns:
        List of violations found

    """
    violations: list[Violation] = []

    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return violations

    tc_lines = _find_type_checking_lines(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.lineno in tc_lines:
            continue
        if _is_cross_service_db_import(node, service):
            violations.append(
                Violation(
                    file=file_path,
                    line=node.lineno,
                    category="cross_service_import",
                    message=(
                        f"Cross-service DB import: "
                        f"from {node.module} (service={service})"
                    ),
                ),
            )

    return violations


def validate_exceptions_yaml(
    registry_path: Path,
) -> list[Violation]:
    """Validate the exceptions registry YAML file.

    Checks:
    1. YAML parses correctly
    2. All entries validate against ModelDbBoundaryExceptionsRegistry
    3. No entries have expired review_by dates

    Args:
        registry_path: Path to the exceptions YAML file

    Returns:
        List of violations found

    """
    violations: list[Violation] = []

    try:
        content = registry_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
    except (OSError, yaml.YAMLError) as e:
        violations.append(
            Violation(
                file=registry_path,
                line=1,
                category="registry_error",
                message=f"Cannot read registry: {e}",
            ),
        )
        return violations

    if data is None:
        data = {"exceptions": []}

    # Validate against Pydantic model
    try:
        registry = ModelDbBoundaryExceptionsRegistry.model_validate(data)
    except ValidationError as e:
        violations.append(
            Violation(
                file=registry_path,
                line=1,
                category="registry_validation",
                message=f"Registry validation failed: {e}",
            ),
        )
        return violations

    # Check for expired review_by dates
    now = datetime.now(tz=UTC)
    current_ym = f"{now.year:04d}-{now.month:02d}"

    for i, exc in enumerate(registry.exceptions):
        if exc.review_by < current_ym:
            violations.append(
                Violation(
                    file=registry_path,
                    line=i + 1,
                    category="expired_review",
                    message=(
                        f"Expired review_by date: {exc.review_by} "
                        f"for {exc.repo}:{exc.file} "
                        f"(current: {current_ym})"
                    ),
                ),
            )

    return violations


def _load_exception_keys(
    registry_path: Path | None,
    service: str,
) -> set[tuple[str, str]]:
    """Load (repo, relative_file) keys for APPROVED exceptions.

    Args:
        registry_path: Path to exceptions YAML, or None to skip
        service: Current service name to filter by

    Returns:
        Set of (repo, file) tuples for approved exceptions

    """
    if registry_path is None or not registry_path.exists():
        return set()
    try:
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        registry = ModelDbBoundaryExceptionsRegistry.model_validate(
            data or {"exceptions": []},
        )
    except (OSError, yaml.YAMLError, ValidationError):
        return set()
    return {
        (exc.repo, exc.file)
        for exc in registry.exceptions
        if exc.repo == service and exc.status.value == "APPROVED"
    }


def _relative_file(repo_path: Path, file_path: Path) -> str:
    """Return the repo-relative file path as a string."""
    try:
        return str(file_path.relative_to(repo_path))
    except ValueError:
        return str(file_path)


def _scan_repo(
    repo_path: Path,
    service: str,
    *,
    exception_keys: set[tuple[str, str]] | None = None,
) -> list[Violation]:
    """Scan all Python files in a repo for DB boundary violations.

    Args:
        repo_path: Path to the repository root
        service: Service name (e.g., "omniintelligence")
        exception_keys: Set of (repo, file) tuples to skip (registered exceptions)

    Returns:
        List of all violations found (excluding registered exceptions)

    """
    violations: list[Violation] = []
    src_dir = repo_path / "src"
    exc_keys = exception_keys or set()

    if not src_dir.exists():
        return violations

    for py_file in sorted(src_dir.rglob("*.py")):
        rel = _relative_file(repo_path, py_file)
        if (service, rel) in exc_keys:
            continue
        violations.extend(
            check_file_for_cross_service_env(py_file, service),
        )
        violations.extend(
            check_file_for_cross_service_imports(py_file, service),
        )

    return violations


def main() -> int:
    """Run DB boundary checks.

    Returns:
        Exit code: 0 if all checks pass (or --warn-only), 1 otherwise

    """
    parser = argparse.ArgumentParser(
        description="Check DB boundary policy compliance.",
        prog="check-db-boundary",
    )
    parser.add_argument(
        "--repo",
        help="Service name to check (e.g., omniintelligence)",
    )
    parser.add_argument(
        "--path",
        help="Path to the repository root to scan",
    )
    parser.add_argument(
        "--validate-all",
        action="store_true",
        help="Validate the exceptions registry YAML",
    )
    parser.add_argument(
        "--registry",
        help="Path to exceptions registry YAML file",
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Print violations but exit with code 0",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    args = parser.parse_args()

    use_color = not args.no_color and sys.stdout.isatty()
    if use_color:
        init(autoreset=True, strip=False)

    all_violations: list[Violation] = []

    # Repo scan mode
    if args.repo and args.path:
        repo_path = Path(args.path)
        if not repo_path.exists():
            _print_error(
                f"Repository path does not exist: {repo_path}",
                use_color=use_color,
            )
            return 1
        registry_path = Path(args.registry) if args.registry else None
        exc_keys = _load_exception_keys(registry_path, args.repo)
        all_violations.extend(
            _scan_repo(repo_path, args.repo, exception_keys=exc_keys),
        )

    # Registry validation mode
    if args.validate_all and args.registry:
        registry_path = Path(args.registry)
        if not registry_path.exists():
            _print_error(
                f"Registry file does not exist: {registry_path}",
                use_color=use_color,
            )
            return 1
        all_violations.extend(validate_exceptions_yaml(registry_path))

    if all_violations:
        _print_violations(all_violations, use_color=use_color)
        if args.warn_only:
            return 0
        return 1

    success_color = Fore.GREEN if use_color else ""
    reset = Style.RESET_ALL if use_color else ""
    print(  # noqa: T201
        f"{success_color}check-db-boundary: all checks passed{reset}",
    )
    return 0


def _print_error(msg: str, *, use_color: bool) -> None:
    """Print an error message."""
    error_color = Fore.RED if use_color else ""
    reset = Style.RESET_ALL if use_color else ""
    print(f"{error_color}Error: {msg}{reset}")  # noqa: T201


def _print_violations(
    violations: list[Violation],
    *,
    use_color: bool,
) -> None:
    """Print violations report."""
    error_color = Fore.RED if use_color else ""
    reset = Style.RESET_ALL if use_color else ""
    print(  # noqa: T201
        f"{error_color}Found {len(violations)} violation(s):{reset}",
    )
    for v in violations:
        cat_color = Fore.YELLOW if use_color else ""
        print(  # noqa: T201
            f"  {cat_color}{v.file}:{v.line}: [{v.category}]{reset} {v.message}",
        )


if __name__ == "__main__":
    sys.exit(main())
