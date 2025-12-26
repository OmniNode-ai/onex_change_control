"""Check schema module purity and naming conventions.

This script enforces:
1. Purity (D-008): No env/fs/network/time usage in schema modules
2. Naming conventions: Model*/model_*, Enum*/enum_*
3. No environment-dependent defaults

Usage:
    poetry run check-schema-purity [--warn-only] [--no-color]

Options:
    --warn-only    Print violations but exit with code 0 (for gradual adoption)
    --no-color     Disable colored output (useful for CI environments)

Exit codes:
    0: All checks passed (or --warn-only flag is set)
    1: One or more violations found (unless --warn-only is set)

Alias Detection Limitations:
    The script tracks imported aliases and resolves them for attribute chains.
    However, deeply nested alias patterns (e.g.,
    `import datetime as dt; dt.datetime.now()`) may not be fully detected.
    The import itself will be caught if the module is forbidden, providing
    defense-in-depth. This limitation is acceptable for schema modules where
    such patterns are uncommon.
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import NamedTuple

from colorama import Fore, Style, init

# Directories to scan for schema modules
SCHEMA_MODULE_PATHS = [
    "src/onex_change_control/models",
    "src/onex_change_control/enums",
]

# Forbidden module imports for purity.
# These modules can access environment, filesystem, network, or system time.
FORBIDDEN_IMPORTS = frozenset(
    {
        "os",
        "os.environ",
        "dotenv",
        "shutil",
        "tempfile",
        "glob",
        "socket",
        "urllib",
        "urllib.request",
        "urllib.parse",
        "requests",
        "httpx",
        "aiohttp",
        "time",
        "subprocess",
        "multiprocessing",
        "threading",
        "signal",
        "random",
        "secrets",
        "locale",
    }
)

# Forbidden function calls that access environment or current time.
FORBIDDEN_CALLS = frozenset(
    {
        "datetime.now",
        "datetime.today",
        "datetime.utcnow",
        "date.today",
        "os.environ.get",
        "os.getenv",
        "os.getcwd",
        "os.path.expanduser",
        "os.path.expandvars",
        "Path.home",
        "Path.cwd",
    }
)

# Minimum number of parts in a call name needed for simplification.
# Used to detect nested alias patterns like `dt.datetime.now()` where
# `datetime` is imported as `dt`. We simplify `datetime.datetime.now` to
# `datetime.now` for matching against FORBIDDEN_CALLS.
# Requires at least 3 parts: module.class.method (e.g., datetime.datetime.now)
_MIN_PARTS_FOR_CALL_SIMPLIFICATION = 3


class Violation(NamedTuple):
    """Represents a single purity or naming violation."""

    file: Path
    line: int
    category: str
    message: str


class PurityChecker(ast.NodeVisitor):
    """AST visitor to check for purity violations."""

    def __init__(self, file_path: Path) -> None:
        """Initialize the purity checker.

        Args:
            file_path: Path to the file being checked

        """
        self.file_path = file_path
        self.violations: list[Violation] = []
        self._imported_names: dict[str, str] = {}  # alias -> full module name

    def visit_Import(self, node: ast.Import) -> None:
        """Check import statements for forbidden modules."""
        for alias in node.names:
            module_name = alias.name
            stored_name = alias.asname if alias.asname else alias.name
            self._imported_names[stored_name] = module_name

            # Check top-level module
            top_module = module_name.split(".")[0]
            if top_module in FORBIDDEN_IMPORTS or module_name in FORBIDDEN_IMPORTS:
                self.violations.append(
                    Violation(
                        file=self.file_path,
                        line=node.lineno,
                        category="forbidden_import",
                        message=f"Forbidden import: '{module_name}' (violates purity)",
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Check from-import statements for forbidden modules."""
        if node.module is None:
            self.generic_visit(node)
            return

        module_name = node.module
        top_module = module_name.split(".")[0]

        # Track imported names
        for alias in node.names:
            stored_name = alias.asname if alias.asname else alias.name
            self._imported_names[stored_name] = f"{module_name}.{alias.name}"

        if top_module in FORBIDDEN_IMPORTS or module_name in FORBIDDEN_IMPORTS:
            self.violations.append(
                Violation(
                    file=self.file_path,
                    line=node.lineno,
                    category="forbidden_import",
                    message=f"Forbidden import from: '{module_name}' (violates purity)",
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Check function calls for forbidden patterns."""
        call_name = self._get_call_name(node)
        if call_name:
            # Check against forbidden calls (exact match)
            if call_name in FORBIDDEN_CALLS:
                self.violations.append(
                    Violation(
                        file=self.file_path,
                        line=node.lineno,
                        category="forbidden_call",
                        message=f"Forbidden call: '{call_name}' (violates purity)",
                    )
                )
            # Check for nested alias patterns (e.g., dt.datetime.now -> datetime.now)
            # This handles cases where modules are imported with aliases and then
            # accessed via nested attributes. For example, when datetime is imported
            # as dt, the call dt.datetime.now() resolves to 'datetime.datetime.now'.
            # We simplify 'datetime.datetime.now' to 'datetime.now' by taking the
            # last two parts (class.method) and matching against FORBIDDEN_CALLS.
            # This pattern matching is necessary because the full call name may not
            # match FORBIDDEN_CALLS directly, but the simplified version will.
            elif "." in call_name:
                parts = call_name.split(".")
                # Only attempt simplification if we have enough parts to extract
                # a meaningful class.method pattern (requires at least 3 parts:
                # module.class.method)
                if len(parts) >= _MIN_PARTS_FOR_CALL_SIMPLIFICATION:
                    # Extract the last two parts (class.method) for matching
                    simplified = f"{parts[-2]}.{parts[-1]}"
                    if simplified in FORBIDDEN_CALLS:
                        self.violations.append(
                            Violation(
                                file=self.file_path,
                                line=node.lineno,
                                category="forbidden_call",
                                message=(
                                    f"Forbidden call: '{call_name}' (violates purity)"
                                ),
                            )
                        )
            # Check for os.environ access patterns
            if call_name.startswith(("os.environ", "environ.")):
                self.violations.append(
                    Violation(
                        file=self.file_path,
                        line=node.lineno,
                        category="forbidden_call",
                        message=f"Environment access: '{call_name}' (violates purity)",
                    )
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Check attribute access for forbidden patterns."""
        attr_chain = self._get_attribute_chain(node)
        # Check for os.environ direct access
        if attr_chain and (
            attr_chain == "os.environ" or attr_chain.startswith("os.environ.")
        ):
            self.violations.append(
                Violation(
                    file=self.file_path,
                    line=node.lineno,
                    category="forbidden_access",
                    message=f"Environment access: '{attr_chain}' (violates purity)",
                )
            )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        """Check subscript access for forbidden patterns (e.g., os.environ['VAR'])."""
        # Check for os.environ['VAR'] or os.environ["VAR"] patterns
        if isinstance(node.value, ast.Attribute):
            attr_chain = self._get_attribute_chain(node.value)
            if attr_chain == "os.environ":
                self.violations.append(
                    Violation(
                        file=self.file_path,
                        line=node.lineno,
                        category="forbidden_access",
                        message=(
                            "Environment access via subscript: "
                            "os.environ[...] (violates purity)"
                        ),
                    )
                )
        elif isinstance(node.value, ast.Name):
            # Check for aliased os.environ access (e.g., env = os.environ; env['VAR'])
            resolved_name = self._imported_names.get(node.value.id, node.value.id)
            if resolved_name == "os.environ":
                self.violations.append(
                    Violation(
                        file=self.file_path,
                        line=node.lineno,
                        category="forbidden_access",
                        message=(
                            "Environment access via subscript: "
                            "os.environ[...] (violates purity)"
                        ),
                    )
                )
        self.generic_visit(node)

    def _get_call_name(self, node: ast.Call) -> str | None:
        """Get the full name of a function call, resolving aliases."""
        if isinstance(node.func, ast.Name):
            # Resolve alias if present
            return self._imported_names.get(node.func.id, node.func.id)
        if isinstance(node.func, ast.Attribute):
            return self._get_attribute_chain(node.func)
        return None

    def _get_attribute_chain(self, node: ast.Attribute) -> str | None:
        """Get the full attribute chain, resolving aliases.

        Example: 'datetime.datetime.now' -> 'datetime.datetime.now'
        """
        parts: list[str] = []
        current: ast.expr = node

        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value

        if isinstance(current, ast.Name):
            # Resolve alias if present
            resolved_name = self._imported_names.get(current.id, current.id)
            parts.append(resolved_name)
            return ".".join(reversed(parts))
        return None


def _read_file_safely(  # noqa: PLR0911
    file_path: Path, *, validate_path: bool = True
) -> tuple[str | None, list[Violation]]:
    """Read a file safely, returning source and any file reading violations.

    Args:
        file_path: Path to the file to read
        validate_path: If True, validate that file is within allowed schema directories.
            Set to False for testing scenarios.

    Returns:
        Tuple of (source content or None, list of violations)
        If reading fails, returns (None, violations).
        If successful, returns (source, []).

    """
    violations: list[Violation] = []

    # Security: Validate that file path is within allowed schema directories
    # This prevents path traversal attacks
    if validate_path:
        project_root = Path(__file__).parent.parent
        resolved_path = file_path.resolve()
        allowed_dirs = [
            (project_root / module_path).resolve()
            for module_path in SCHEMA_MODULE_PATHS
        ]

        # Check if the resolved path is within any allowed directory
        is_allowed = any(
            str(resolved_path).startswith(str(allowed_dir))
            for allowed_dir in allowed_dirs
        )

        if not is_allowed:
            violations.append(
                Violation(
                    file=file_path,
                    line=1,
                    category="file_error",
                    message=(
                        f"File path '{file_path}' is outside allowed "
                        f"schema directories (security: path traversal prevention)"
                    ),
                )
            )
            return None, violations

    # Additional security: Always check for path traversal patterns in the path string
    # This provides defense-in-depth even for test scenarios
    path_str = str(file_path)
    if ".." in path_str:
        # Block path traversal patterns (e.g., ../../../etc/passwd)
        # This applies to both production and test scenarios for security
        violations.append(
            Violation(
                file=file_path,
                line=1,
                category="file_error",
                message=(
                    f"File path '{file_path}' contains path traversal pattern '..' "
                    f"(security: path traversal prevention)"
                ),
            )
        )
        return None, violations
    try:
        with file_path.open("r", encoding="utf-8") as f:
            source = f.read()
    except FileNotFoundError as e:
        violations.append(
            Violation(
                file=file_path,
                line=1,
                category="file_error",
                message=f"File not found: {e}",
            )
        )
        return None, violations
    except PermissionError as e:
        violations.append(
            Violation(
                file=file_path,
                line=1,
                category="file_error",
                message=f"Permission denied: {e}",
            )
        )
        return None, violations
    except UnicodeDecodeError as e:
        violations.append(
            Violation(
                file=file_path,
                line=1,
                category="file_error",
                message=f"Unicode decode error: {e}",
            )
        )
        return None, violations
    except OSError as e:
        violations.append(
            Violation(
                file=file_path,
                line=1,
                category="file_error",
                message=f"Cannot read file ({type(e).__name__}): {e}",
            )
        )
        return None, violations
    else:
        return source, violations


def check_file(file_path: Path) -> list[Violation]:
    """Check a file for both purity and naming convention violations.

    Parses the file once and performs all checks in a single pass.

    Args:
        file_path: Path to the Python file to check

    Returns:
        List of all violations (purity and naming)

    """
    all_violations: list[Violation] = []
    file_name = file_path.name

    # Determine expected prefix based on directory
    if "models" in file_path.parts:
        expected_file_prefix = "model_"
        expected_class_prefix = "Model"
    elif "enums" in file_path.parts:
        expected_file_prefix = "enum_"
        expected_class_prefix = "Enum"
    else:
        return all_violations  # Not a schema module

    # Skip __init__.py
    if file_name == "__init__.py":
        return all_violations

    # Check file naming (doesn't require parsing)
    if not file_name.startswith(expected_file_prefix):
        all_violations.append(
            Violation(
                file=file_path,
                line=1,
                category="naming_file",
                message=f"File '{file_name}' needs prefix '{expected_file_prefix}'",
            )
        )

    # Read and parse file once
    # Validate path only for files discovered via find_schema_files (production use)
    # For direct calls (e.g., tests), skip path validation
    project_root = Path(__file__).parent.parent
    resolved_path = file_path.resolve()
    is_in_schema_dir = any(
        str(resolved_path).startswith(str((project_root / module_path).resolve()))
        for module_path in SCHEMA_MODULE_PATHS
    )
    validate_path = is_in_schema_dir

    source, file_violations = _read_file_safely(file_path, validate_path=validate_path)
    all_violations.extend(file_violations)
    if source is None:
        return all_violations

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as e:
        all_violations.append(
            Violation(
                file=file_path,
                line=e.lineno or 1,
                category="syntax_error",
                message=f"Syntax error: {e.msg}",
            )
        )
        return all_violations

    # Check naming conventions (top-level classes only)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_name = node.name
            # Flag non-prefixed classes (allow private classes starting with _)
            if not class_name.startswith(
                expected_class_prefix
            ) and not class_name.startswith("_"):
                all_violations.append(
                    Violation(
                        file=file_path,
                        line=node.lineno,
                        category="naming_class",
                        message=(
                            f"Class '{class_name}' should start with "
                            f"'{expected_class_prefix}'"
                        ),
                    )
                )

    # Check purity (visits entire AST)
    checker = PurityChecker(file_path)
    checker.visit(tree)
    all_violations.extend(checker.violations)

    return all_violations


def find_schema_files(project_root: Path) -> list[Path]:
    """Find all Python files in schema module directories.

    Args:
        project_root: Path to the project root

    Returns:
        List of Python file paths

    """
    files: list[Path] = []
    for module_path in SCHEMA_MODULE_PATHS:
        full_path = project_root / module_path
        if full_path.exists():
            files.extend(full_path.glob("*.py"))
    return sorted(files)


def _get_category_color(category: str, *, use_color: bool) -> str:
    """Get color code for a violation category.

    Args:
        category: Violation category name
        use_color: Whether to use colors

    Returns:
        Color code string (empty if use_color is False)

    """
    if not use_color:
        return ""

    color_map: dict[str, str] = {
        "forbidden_import": Fore.RED,
        "forbidden_call": Fore.RED,
        "forbidden_access": Fore.RED,
        "naming_file": Fore.YELLOW,
        "naming_class": Fore.YELLOW,
        "syntax_error": Fore.RED,
        "file_error": Fore.RED,
    }
    return color_map.get(category, "")


def _print_violations_report(
    all_violations: list[Violation],
    *,
    use_color: bool,
    warn_only: bool,
) -> None:
    """Print violations grouped by category.

    Args:
        all_violations: List of all violations found
        use_color: Whether to use colored output
        warn_only: Whether warn-only mode is enabled

    """
    error_color = Fore.RED if use_color else ""
    reset = Style.RESET_ALL if use_color else ""
    warn_only_msg = " (warn-only mode: exiting with code 0)" if warn_only else ""
    violation_count = len(all_violations)
    print(  # noqa: T201
        f"{error_color}❌ Found {violation_count} violation(s):{warn_only_msg}{reset}"
    )
    print()  # noqa: T201

    # Group by category
    by_category: dict[str, list[Violation]] = {}
    for v in all_violations:
        by_category.setdefault(v.category, []).append(v)

    for category, violations in sorted(by_category.items()):
        category_color = _get_category_color(category, use_color=use_color)
        reset = Style.RESET_ALL if use_color else ""
        print(f"  {category_color}{category} ({len(violations)}):{reset}")  # noqa: T201
        for v in violations:
            print_violation(v, use_color=use_color)
        print()  # noqa: T201


def print_violation(v: Violation, *, use_color: bool = True) -> None:
    """Print a violation in a readable format.

    Args:
        v: Violation to print
        use_color: Whether to use colored output

    """
    relative_path = v.file.relative_to(Path.cwd()) if v.file.is_absolute() else v.file
    category_color = _get_category_color(v.category, use_color=use_color)
    reset = Style.RESET_ALL if use_color else ""
    print(  # noqa: T201
        f"  {category_color}{relative_path}:{v.line}: [{v.category}]{reset} {v.message}"
    )


def main() -> int:
    """Run purity and naming checks on schema modules.

    Returns:
        Exit code: 0 if all checks pass (or --warn-only), 1 if violations found

    """
    parser = argparse.ArgumentParser(
        description="Check schema module purity and naming conventions.",
        epilog=(
            "Example: poetry run check-schema-purity --warn-only "
            "(for gradual adoption in CI)"
        ),
    )
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help=(
            "Print violations but exit with code 0. "
            "Useful for gradual adoption in downstream repositories."
        ),
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output (useful for CI environments).",
    )

    args = parser.parse_args()

    # Initialize colorama (auto-detects if TTY is available)
    # Only use colors if --no-color is not set AND we're in a TTY
    use_color = not args.no_color and sys.stdout.isatty()
    if use_color:
        # colorama automatically detects TTY and disables colors in non-TTY environments
        # (e.g., CI). Setting strip=False preserves ANSI codes for TTY detection.
        init(autoreset=True, strip=False)

    # Find project root: scripts are in src/onex_change_control/scripts/
    # so we need to go up 3 levels to get to project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent.parent

    # Validate that schema directories exist
    missing_dirs: list[str] = []
    for module_path in SCHEMA_MODULE_PATHS:
        full_path = project_root / module_path
        if not full_path.exists():
            missing_dirs.append(module_path)

    if missing_dirs:
        warning_color = Fore.YELLOW if use_color else ""
        reset = Style.RESET_ALL if use_color else ""
        print(  # noqa: T201
            f"{warning_color}⚠️  Schema directories not found: "
            f"{', '.join(missing_dirs)}{reset}"
        )
        print("   This may indicate a configuration error.")  # noqa: T201
        return 1

    schema_files = find_schema_files(project_root)

    if not schema_files:
        warning_color = Fore.YELLOW if use_color else ""
        reset = Style.RESET_ALL if use_color else ""
        print(f"{warning_color}⚠️  No schema files found to check{reset}")  # noqa: T201
        return 0

    all_violations: list[Violation] = []

    info_color = Fore.CYAN if use_color else ""
    reset = Style.RESET_ALL if use_color else ""
    print(f"{info_color}Checking {len(schema_files)} schema files...{reset}")  # noqa: T201
    print()  # noqa: T201

    for file_path in schema_files:
        # Check both purity and naming in a single parse
        violations = check_file(file_path)
        all_violations.extend(violations)

    if all_violations:
        _print_violations_report(
            all_violations, use_color=use_color, warn_only=args.warn_only
        )
        # Return 0 if --warn-only is set, otherwise 1
        return 0 if args.warn_only else 1

    success_color = Fore.GREEN if use_color else ""
    reset = Style.RESET_ALL if use_color else ""
    print(  # noqa: T201
        f"{success_color}✅ All {len(schema_files)} schema files passed "
        f"purity and naming checks{reset}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
